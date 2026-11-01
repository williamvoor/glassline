"""SQL safety and execution (parent spec §9).

Layers, outermost first: the `pglast` guard, a `READ ONLY` transaction with a statement timeout
and the extended protocol (one statement only), a pool that resets every connection, and the
read-only role's grants. The guard is a layer, not the defence.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict
from pglast import ast, parse_sql
from pglast.parser import ParseError
from psycopg_pool import AsyncConnectionPool

from explorer_agent.results import to_json_safe

log = logging.getLogger(__name__)

POOL_MAX_SIZE = 4
STREAM_CHUNK = 500

DENIED_FUNCTIONS = frozenset(
    {"set_config", "pg_terminate_backend", "pg_cancel_backend", "pg_notify", "pg_current_xact_id",
     "pg_reload_conf", "ts_stat"}
)
DENIED_FUNCTION_PREFIXES = (
    "pg_sleep", "pg_advisory", "pg_try_advisory", "pg_read_", "pg_ls_", "pg_stat_", "lo_", "dblink", "txid_",
)
# Functions that run SQL passed as a string, out of the guard's sight: ts_stat above and the
# *_to_xml family (query_to_xml, cursor_to_xml, table_to_xml, ...).
DENIED_FUNCTION_SUFFIXES = ("_to_xml", "_to_xmlschema", "_to_xml_and_xmlschema")
CATALOG_SCHEMAS = frozenset({"pg_catalog", "information_schema"})
WRITE_STATEMENTS = (ast.InsertStmt, ast.UpdateStmt, ast.DeleteStmt, ast.MergeStmt)


class GuardError(ValueError):
    """The SQL was rejected before execution. The message says what to change."""


class StartupError(RuntimeError):
    """The database login is unsafe or the dataset is unusable; the agent must not start."""


def _is_denied(name: str) -> bool:
    return (
        name in DENIED_FUNCTIONS
        or name.startswith(DENIED_FUNCTION_PREFIXES)
        or name.endswith(DENIED_FUNCTION_SUFFIXES)
    )


class _Checker:
    """Walks the parse tree carrying the CTE names visible at each query level.

    Postgres scopes CTEs: a WITH list is visible to its own query and the queries nested in it,
    and a non-recursive CTE sees only the CTEs before it. A global set of CTE names would let an
    unrelated CTE called `pg_roles` exempt a real `pg_roles` elsewhere in the query.
    """

    def __init__(self, allowed: frozenset[str]):
        self.allowed = allowed
        self.example = f"{min(allowed)}.<table>" if allowed else "<schema>.<table>"
        self.problems: list[str] = []

    def walk(self, value: Any, visible: frozenset[str]) -> None:
        if isinstance(value, (list, tuple)):
            for item in value:
                self.walk(item, visible)
        elif isinstance(value, ast.Node):
            self.node(value, visible)

    def children(self, node: ast.Node, visible: frozenset[str], skip: str | None = None) -> None:
        for attr in node.__slots__:
            if attr != skip:
                self.walk(getattr(node, attr), visible)

    def node(self, node: ast.Node, visible: frozenset[str]) -> None:
        if isinstance(node, WRITE_STATEMENTS):
            self.problems.append("only read queries are allowed: no INSERT, UPDATE, DELETE or MERGE")
        elif isinstance(node, ast.SelectStmt):
            if node.intoClause is not None:
                self.problems.append("SELECT ... INTO is not allowed: just SELECT the rows")
            if node.lockingClause:
                self.problems.append("row locking (FOR UPDATE / FOR SHARE) is not allowed")
            if node.withClause is not None:
                ctes = node.withClause.ctes or ()
                names = [c.ctename for c in ctes]
                for i, cte in enumerate(ctes):
                    seen = names if node.withClause.recursive else names[:i]
                    self.children(cte, visible | frozenset(seen))
                self.children(node, visible | frozenset(names), skip="withClause")
                return
        elif isinstance(node, ast.RangeVar):
            self.relation(node, visible)
        elif isinstance(node, ast.FuncCall):
            self.function([s.sval for s in node.funcname])
        self.children(node, visible)

    def relation(self, rel: ast.RangeVar, visible: frozenset[str]) -> None:
        if rel.schemaname is None:
            if rel.relname not in visible:
                self.problems.append(f'qualify table names with their schema: "{rel.relname}" → {self.example}')
        elif rel.schemaname in CATALOG_SCHEMAS:
            self.problems.append(
                f"{rel.schemaname} is not available: use the schema in your instructions "
                "or the list_tables/describe_table tools"
            )
        elif rel.schemaname not in self.allowed:
            self.problems.append(
                f'schema "{rel.schemaname}" is not allowed; allowed: {", ".join(sorted(self.allowed))}'
            )

    def function(self, names: list[str]) -> None:
        name = names[-1]
        schema = names[-2] if len(names) > 1 else None
        if _is_denied(name):
            self.problems.append(f"function {name}() is not allowed")
        elif schema is not None and schema not in self.allowed:
            self.problems.append(f'functions from schema "{schema}" are not allowed')


def guard(sql: str, allowed_schemas: Iterable[str]) -> None:
    """Raise GuardError unless `sql` is one read-only SELECT over the allowed schemas."""
    if "\x00" in sql:
        raise GuardError("the SQL contains a NUL character; remove it")
    try:
        stmts = parse_sql(sql)
    except ParseError as e:
        raise GuardError(f"SQL syntax error: {e.args[0]}") from None
    except RecursionError:
        raise GuardError("the query is too deeply nested; simplify it") from None
    if len(stmts) != 1:
        raise GuardError("send exactly one SQL statement (no semicolon-separated statements)")
    if not isinstance(stmts[0].stmt, ast.SelectStmt):
        kind = type(stmts[0].stmt).__name__.removesuffix("Stmt")
        raise GuardError(f"only SELECT (or WITH ... SELECT) is allowed, not {kind}")
    checker = _Checker(frozenset(allowed_schemas))
    try:
        checker.walk(stmts[0].stmt, frozenset())
    except RecursionError:
        raise GuardError("the query is too deeply nested; simplify it") from None
    if checker.problems:
        raise GuardError("; ".join(dict.fromkeys(checker.problems)))


@dataclass(frozen=True)
class SqlOk:
    columns: list[str]
    column_types: list[str]
    rows: list[dict[str, Any]]
    truncated: bool
    elapsed_ms: int


@dataclass(frozen=True)
class SqlFail:
    sqlstate: str | None
    message: str
    hint: str | None = None


async def _reset(conn: psycopg.AsyncConnection) -> None:
    await conn.execute("DISCARD ALL")


def check_dsn(dsn: str) -> None:
    """Raise ValueError, without the DSN in the message, if libpq can't parse it.

    psycopg.pool logs connection errors, and libpq quotes a malformed DSN verbatim, password
    included. Parsing first keeps it out of the log.
    """
    try:
        conninfo_to_dict(dsn)
    except psycopg.ProgrammingError:
        raise ValueError("the database URL is malformed (expected postgresql://user:password@host:port/db)") from None


def open_pool(dsn: str) -> AsyncConnectionPool:
    """A closed pool; the caller awaits `pool.open()`. The DSN is never logged."""
    check_dsn(dsn)
    return AsyncConnectionPool(
        dsn,
        min_size=1,
        max_size=POOL_MAX_SIZE,
        open=False,
        kwargs={"autocommit": True},
        reset=_reset,
        name="explorer-sql",
    )


def _type_name(conn: psycopg.AsyncConnection, col: psycopg.Column) -> str:
    """`bigint`, `numeric`, `timestamp with time zone`, ... without typmods such as (18,2)."""
    info = conn.adapters.types.get(col.type_code)
    if info is not None and col.type_code == info.oid:
        return info.regtype
    if info is not None and col.type_code == info.array_oid:
        return f"{info.regtype}[]"
    return col.type_display


class SqlRunner:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        allowed_schemas: Iterable[str],
        timeout_ms: int,
        row_cap: int,
        *,
        guard_enabled: bool = True,
    ):
        self.pool = pool
        self.allowed_schemas = tuple(sorted(allowed_schemas))
        self.timeout_ms = int(timeout_ms)
        self.row_cap = int(row_cap)
        self.guard_enabled = guard_enabled

    async def check_privileges(self) -> None:
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT rolsuper, rolcreaterole FROM pg_roles WHERE rolname = current_user"
                )
            ).fetchone()
            if row is None or row[0] or row[1]:
                raise StartupError("the database login must not be a superuser or have CREATEROLE")
            writable = await (
                await conn.execute(
                    """
                    SELECT n.nspname || '.' || c.relname
                    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
                      AND (has_table_privilege(c.oid, 'INSERT')
                        OR has_table_privilege(c.oid, 'UPDATE')
                        OR has_table_privilege(c.oid, 'DELETE'))
                    ORDER BY 1 LIMIT 5
                    """,
                    [list(self.allowed_schemas)],
                )
            ).fetchall()
            if writable:
                names = ", ".join(r[0] for r in writable)
                raise StartupError(f"the database login can write to allowed tables ({names}); use the read-only role")
            counts = dict(
                await (
                    await conn.execute(
                        """
                        SELECT s, count(c.oid) FROM unnest(%s::text[]) s
                        LEFT JOIN pg_namespace n ON n.nspname = s
                        LEFT JOIN pg_class c ON c.relnamespace = n.oid AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
                        GROUP BY s
                        """,
                        [list(self.allowed_schemas)],
                    )
                ).fetchall()
            )
            empty = [s for s in self.allowed_schemas if not counts.get(s)]
            if empty:
                raise StartupError(f"allowed schema has no tables visible to this login: {', '.join(empty)}")

    async def run(self, sql: str) -> SqlOk | SqlFail:
        if self.guard_enabled:
            try:
                guard(sql, self.allowed_schemas)
            except GuardError as e:
                return SqlFail(sqlstate=None, message=str(e))
        try:
            async with self.pool.connection() as conn:
                await conn.execute("BEGIN READ ONLY")
                try:
                    await conn.execute(f"SET LOCAL statement_timeout = {self.timeout_ms}")
                    start = time.perf_counter()
                    cur = conn.cursor()
                    # Streamed in chunks, so the agent holds at most row_cap + 1 rows plus one
                    # chunk; leaving the loop early cancels the rest of the query.
                    fetched: list[tuple[Any, ...]] = []
                    async for row in cur.stream(sql, size=STREAM_CHUNK):
                        fetched.append(row)
                        if len(fetched) > self.row_cap:
                            break
                    elapsed_ms = round((time.perf_counter() - start) * 1000)
                    if cur.description is None:
                        return SqlFail(sqlstate=None, message="the statement returned no rows")
                    columns = [col.name for col in cur.description]
                    types = [_type_name(conn, col) for col in cur.description]
                finally:
                    with contextlib.suppress(psycopg.Error):  # keep the original error
                        await conn.execute("ROLLBACK")
        except psycopg.Error as e:
            diag = e.diag
            return SqlFail(
                sqlstate=e.sqlstate,
                message=diag.message_primary or str(e).strip(),
                hint=diag.message_hint,
            )
        duplicates = sorted({c for c in columns if columns.count(c) > 1})
        if duplicates:
            return SqlFail(
                sqlstate=None,
                message=f"duplicate column names in the result: {', '.join(duplicates)}; give each column a unique alias",
            )
        truncated = len(fetched) > self.row_cap
        rows = [
            {name: to_json_safe(value) for name, value in zip(columns, row, strict=True)}
            for row in fetched[: self.row_cap]
        ]
        log.info("sql ok: %d rows%s in %d ms", len(rows), " (truncated)" if truncated else "", elapsed_ms)
        return SqlOk(columns=columns, column_types=types, rows=rows, truncated=truncated, elapsed_ms=elapsed_ms)
