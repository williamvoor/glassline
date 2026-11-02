"""Database reads for the report: planner statistics, comment privileges and cheap min/max.

Every query is read-only: the pool sets `default_transaction_read_only` and `statement_timeout`
as connection options, and our own queries also open `BEGIN READ ONLY`. Min/max goes through the
agent's guarded `SqlRunner`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from psycopg_pool import AsyncConnectionPool

from explorer_agent.results import NUMERIC_TYPES, TEMPORAL_TYPES
from explorer_agent.sql import SqlFail, SqlRunner, check_dsn

QUERY_CANCELED = "57014"  # statement_timeout
MOST_COMMON_LIMIT = 5

_STATS_SQL = """
SELECT DISTINCT ON (schemaname, tablename, attname)
       schemaname, tablename, attname, null_frac, n_distinct,
       (most_common_vals::text)::text[], most_common_freqs
FROM pg_stats
WHERE schemaname = ANY(%(schemas)s)
ORDER BY schemaname, tablename, attname, inherited
"""

_COMMENTABLE_SQL = """
SELECT n.nspname, c.relname, pg_has_role(c.relowner, 'USAGE')
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY(%(schemas)s) AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND NOT c.relispartition
"""


def open_report_pool(dsn: str, timeout_ms: int) -> AsyncConnectionPool:
    """A closed pool whose sessions are read-only by default and time out. The DSN is never logged."""
    check_dsn(dsn)
    return AsyncConnectionPool(
        dsn,
        min_size=1,
        max_size=2,
        open=False,
        kwargs={
            "autocommit": True,
            "connect_timeout": 5,
            "options": f"-c default_transaction_read_only=on -c statement_timeout={int(timeout_ms)}",
        },
        name="bootstrap-report",
    )


@dataclass(frozen=True)
class ColumnStats:
    null_frac: float | None
    n_distinct: float | None
    most_common: tuple[tuple[str, float], ...]


async def _read(pool: AsyncConnectionPool, sql: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    async with pool.connection() as conn:
        await conn.execute("BEGIN READ ONLY")
        try:
            return await (await conn.execute(sql, params)).fetchall()
        finally:
            await conn.execute("ROLLBACK")


async def column_stats(pool: AsyncConnectionPool, schemas: Sequence[str]) -> dict[tuple[str, str, str], ColumnStats]:
    """pg_stats per (schema, table, column). Only columns the login can read appear in pg_stats."""
    rows = await _read(pool, _STATS_SQL, {"schemas": list(schemas)})
    out: dict[tuple[str, str, str], ColumnStats] = {}
    for schema, table, column, null_frac, n_distinct, values, freqs in rows:
        pairs = tuple(
            (str(v), float(f)) for v, f in zip(values or (), freqs or (), strict=False)
        )[:MOST_COMMON_LIMIT]
        out[(schema, table, column)] = ColumnStats(
            null_frac=None if null_frac is None else float(null_frac),
            n_distinct=None if n_distinct is None else float(n_distinct),
            most_common=pairs,
        )
    return out


async def commentable(pool: AsyncConnectionPool, schemas: Sequence[str]) -> dict[tuple[str, str], bool]:
    """Whether the login has the owner's privileges (needed for COMMENT ON); superusers always do."""
    rows = await _read(pool, _COMMENTABLE_SQL, {"schemas": list(schemas)})
    return {(schema, table): bool(ok) for schema, table, ok in rows}


def distinct_estimate(n_distinct: float | None, est_rows: int | None) -> int | None:
    """pg_stats.n_distinct as a count: a negative value is minus the fraction of rows."""
    if n_distinct is None:
        return None
    if n_distinct >= 0:
        return round(n_distinct)
    if est_rows is None:
        return None
    return round(-n_distinct * est_rows)


def base_type(type_name: str) -> str:
    """`numeric(18,2)` → `numeric`; `timestamp(3) with time zone` → `timestamp with time zone`."""
    return " ".join(re.sub(r"\([^)]*\)", "", type_name).split())


def is_range_type(type_name: str) -> bool:
    t = base_type(type_name)
    return t in NUMERIC_TYPES or t in TEMPORAL_TYPES


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True)
class RangeOutcome:
    status: str  # ok | skipped | error
    values: dict[str, tuple[Any, Any]]  # column -> (min, max), when ok
    error: str | None = None


async def column_ranges(runner: SqlRunner, schema: str, table: str, columns: Iterable[str]) -> RangeOutcome:
    """min and max of the given columns in one query, bounded by the runner's statement_timeout."""
    cols = list(columns)
    selects = ", ".join(
        f"min({_ident(c)}) AS mn_{i}, max({_ident(c)}) AS mx_{i}" for i, c in enumerate(cols)
    )
    result = await runner.run(f"SELECT {selects} FROM {_ident(schema)}.{_ident(table)}")
    if isinstance(result, SqlFail):
        if result.sqlstate == QUERY_CANCELED:
            return RangeOutcome("skipped", {})
        return RangeOutcome("error", {}, result.message)
    row = result.rows[0]
    return RangeOutcome("ok", {c: (row[f"mn_{i}"], row[f"mx_{i}"]) for i, c in enumerate(cols)})
