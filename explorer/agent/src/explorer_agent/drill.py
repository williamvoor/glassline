"""Drill queries (SPEC §15, ADR 0024): SQL the model writes ahead of time for the next level of
detail below a clicked row, run later with the row's values bound and no model call.

Pure parts: the `:name` placeholder scan and its rewrite to native `$n` parameters, the walk for
comparisons that silently drop NULLs, and the display copy of the SQL. Async parts: the `LIMIT 0`
check (step 2), running a Drill-down, and prefetch.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pglast import ast, parse_sql
from pglast.enums import A_Expr_Kind
from pglast.parser import ParseError, scan
from psycopg import sql as psql

from explorer_agent.results import NUMERIC_TYPES, TEMPORAL_TYPES

log = logging.getLogger(__name__)

PLACEHOLDER_NAME = re.compile(r"[a-z_][a-z0-9_]*")
BINDABLE = (str, int, float, bool, type(None))
DRILL_AS = ("table", "bar", "line")
TEXT_TYPES = frozenset({"text", "character varying", "varchar", "character", "char", "bpchar", "name", "citext"})
NULL_DROPPING_OPS = frozenset({"=", "<>", "!="})


class DrillSqlError(ValueError):
    """The Drill query can't be used as written. The message says what to change."""


@dataclass(frozen=True)
class Rewritten:
    sql: str  # the `$n` form: what the guard checks and what runs
    names: tuple[str, ...]  # placeholder names in `$n` order ($1 = names[0])


COMMENT_TOKENS = ("SQL_COMMENT", "C_COMMENT")


def normalise(sql: str) -> str:
    """No trailing `;` (also one before a trailing comment), so the statement can be wrapped; a
    wrapper puts it on its own lines, so a trailing `--` comment stays inside."""
    body = sql.strip()
    try:
        tokens = [t for t in scan(body) if t.name not in COMMENT_TOKENS]
    except ParseError:
        return body.rstrip(";").rstrip()  # the guard reports the syntax error
    while tokens and tokens[-1].name == "ASCII_59":
        last = tokens.pop()
        body = body[: last.start] + body[last.end + 1 :]
    return body.strip()


def wrap(sql: str, alias: str, suffix: str = "") -> str:
    """`SELECT * FROM (<sql>) AS <alias>`, newlines keeping a trailing `--` comment inside."""
    return f"SELECT * FROM (\n{normalise(sql)}\n) AS {alias}{suffix}"


def _tokens(sql: str) -> list[Any]:
    try:
        return list(scan(sql))
    except ParseError as e:
        raise DrillSqlError(f"drill_sql: SQL syntax error: {e.args[0]}") from None


def rewrite(sql: str, casts: Mapping[str, str] | None = None) -> Rewritten:
    """`:name` placeholders → `$n`, one per distinct name, found with the Postgres scanner.
    `casts`: a SQL type per name, written as `$n::<type>` (a Board's Controls, SPEC §18).

    Not placeholders: `::` casts, text inside strings, comments and quoted identifiers, and `:`
    inside a subscript `[…]` (array slices). Inside an `ARRAY[…]` constructor they are. A `:` directly before an identifier that isn't lower case is
    an error, as is a `$n` the model wrote itself.
    """
    body = normalise(sql)
    tokens = _tokens(body)
    names: list[str] = []
    spans: list[tuple[int, int, str]] = []
    brackets: list[bool] = []  # per open `[`: is it a slice/subscript (not an ARRAY[...] constructor)?
    for i, tok in enumerate(tokens):
        if tok.name == "ASCII_91":
            brackets.append(not (i > 0 and tokens[i - 1].name == "ARRAY"))
        elif tok.name == "ASCII_93":
            if brackets:
                brackets.pop()
        elif tok.name == "PARAM":
            raise DrillSqlError("drill_sql: write placeholders as :column, not $1")
        elif tok.name == "ASCII_58" and not (brackets and brackets[-1]) and i + 1 < len(tokens):
            nxt = tokens[i + 1]
            if nxt.start != tok.end + 1:
                continue
            word = body[nxt.start : nxt.end + 1]
            if nxt.name != "IDENT" and nxt.kind == "NO_KEYWORD":
                continue  # `:` before a number or a string: not a placeholder (the guard decides)
            if word.startswith('"') or not PLACEHOLDER_NAME.fullmatch(word):
                raise DrillSqlError(
                    f"drill_sql: placeholder :{word} must be a lower-case column name of the Visual's result"
                )
            if word not in names:
                names.append(word)
            cast = f"::{casts[word]}" if casts and word in casts else ""
            spans.append((tok.start, nxt.end + 1, f"${names.index(word) + 1}{cast}"))
    out = body
    for start, end, param in reversed(spans):
        out = out[:start] + param + out[end:]
    return Rewritten(sql=out, names=tuple(names))


def display_sql(dollar_sql: str, values: Sequence[Any]) -> str:
    """The `$n` form with each parameter replaced by its quoted literal, for showing and
    rerunning by hand. Found by the scanner, so a `$1` inside a string or comment stays."""
    out = dollar_sql
    for tok in reversed(_tokens(dollar_sql)):
        if tok.name == "PARAM":
            n = int(dollar_sql[tok.start + 1 : tok.end + 1])
            out = out[: tok.start] + psql.Literal(values[n - 1]).as_string() + out[tok.end + 1 :]
    return out


def _param(node: Any) -> int | None:
    """The `$n` a comparison operand is, looking through casts (`$1::text`)."""
    while isinstance(node, ast.TypeCast):
        node = node.arg
    return node.number if isinstance(node, ast.ParamRef) else None


def null_dropping_params(dollar_sql: str) -> set[int]:
    """Parameters compared with `=`, `<>`, `IN` or `= ANY`, which are never true for NULL."""
    found: set[int] = set()

    def operands(expr: ast.A_Expr) -> Iterable[Any]:
        yield expr.lexpr
        if isinstance(expr.rexpr, (list, tuple)):
            yield from expr.rexpr
        elif isinstance(expr.rexpr, ast.A_ArrayExpr):  # = ANY(ARRAY[$1, ...])
            yield from expr.rexpr.elements or ()
        else:
            yield expr.rexpr

    def walk(value: Any) -> None:
        if isinstance(value, (list, tuple)):
            for v in value:
                walk(v)
            return
        if not isinstance(value, ast.Node):
            return
        if isinstance(value, ast.A_Expr):
            op = value.name[-1].sval if value.name else ""
            kind = value.kind
            dropping = (
                (kind == A_Expr_Kind.AEXPR_OP and op in NULL_DROPPING_OPS)
                or kind == A_Expr_Kind.AEXPR_IN
                or (kind in (A_Expr_Kind.AEXPR_OP_ANY, A_Expr_Kind.AEXPR_OP_ALL) and op in NULL_DROPPING_OPS)
            )
            if dropping:
                found.update(n for n in map(_param, operands(value)) if n is not None)
        for attr in value.__slots__:
            walk(getattr(value, attr))

    walk([s.stmt for s in parse_sql(dollar_sql)])
    return found


# --- what a Visual's Drill query carries ----------------------------------------------------


@dataclass(frozen=True)
class DrillSpec:
    """A checked Drill query (check step 1 passed). `visual` is the Visual's index on the Surface."""

    source: str  # action source ID, v<n>
    visual: int
    sql: str  # `$n` form
    names: tuple[str, ...]
    drill_as: str
    drill_x: str
    drill_y: str
    title: str  # filled template; the row label is appended per Drill-down
    # A Board's Control placeholders and the values they bind, fixed when the panel was built
    # (SPEC §18 slice 2): a Control change rebuilds the panel, so these never go stale.
    fixed: tuple[tuple[str, Any], ...] = ()

    def values(self, row: dict[str, Any]) -> list[Any]:
        fixed = dict(self.fixed)
        return [fixed[n] if n in fixed else row.get(n) for n in self.names]


def check_output(spec: DrillSpec, columns: list[str], types: list[str]) -> list[str]:
    """Step 2's checks on the output columns of the `LIMIT 0` run."""
    if spec.drill_as == "table":
        return []
    problems = []
    kinds = dict(zip(columns, types, strict=True))
    for role, col in (("drill_x", spec.drill_x), ("drill_y", spec.drill_y)):
        if col not in kinds:
            problems.append(f"{role} {col!r} is not an output column of drill_sql; its columns are: {', '.join(columns)}")
    if problems:
        return problems
    if kinds[spec.drill_y] not in NUMERIC_TYPES:
        problems.append(f"drill_y {spec.drill_y!r} is {kinds[spec.drill_y]}, not a number")
    x_type = kinds[spec.drill_x]
    if spec.drill_as == "line" and not (x_type in TEXT_TYPES or x_type in TEMPORAL_TYPES or x_type in NUMERIC_TYPES):
        problems.append(f"drill_x {spec.drill_x!r} is {x_type}; a line needs text, a date or a number")
    return problems


DATA_EXCEPTION = "22"  # SQLSTATE class: invalid input, bad casts, out of range (the value is quoted)
DATA_EXCEPTION_MESSAGE = (
    "a clicked row's value doesn't fit the type it is compared with or cast to; check the placeholder's "
    "column and any casts"
)
REDACTED = "<value>"


def _redact(text: str, values: Sequence[Any]) -> str:
    """Mask the bound values in a database message (Step 2 errors go to the model as user text)."""
    for v in sorted((str(v) for v in values if v is not None and str(v).strip()), key=len, reverse=True):
        text = text.replace(v, REDACTED)
    return text


async def check_drill(runner: Any, spec: DrillSpec, row: dict[str, Any], timeout_ms: int) -> list[str]:
    """Check step 2: plan the query with the first clickable row's values bound, producing no rows."""
    from explorer_agent.sql import SqlFail

    values = spec.values(row)
    outcome = await runner.run_bound(wrap(spec.sql, "_explorer_drill", " LIMIT 0"), values, timeout_ms=timeout_ms)
    if isinstance(outcome, SqlFail):
        code = f" ({outcome.sqlstate})" if outcome.sqlstate else ""
        if (outcome.sqlstate or "").startswith(DATA_EXCEPTION):
            # Postgres quotes the offending value here; it's row data, never user text or a log line.
            return [f"drill_sql of Visual {spec.visual + 1} failed{code}: {DATA_EXCEPTION_MESSAGE}"]
        hint = f" Hint: {_redact(outcome.hint, values)}" if outcome.hint else ""
        return [f"drill_sql of Visual {spec.visual + 1} failed{code}: {_redact(outcome.message, values)}.{hint}"]
    return [f"Visual {spec.visual + 1}: {p}" for p in check_output(spec, outcome.columns, outcome.column_types)]


# --- prefetch ---------------------------------------------------------------------------------


@dataclass
class _Item:
    key: tuple[str, str, int]  # conversation ID, source ID, row index
    task: asyncio.Task[Any] | None = None
    state: str = "queued"  # queued | running | done | failed | dropped
    result: Any = None


@dataclass
class _ConvPrefetch:
    items: dict[tuple[str, int], _Item] = field(default_factory=dict)


class Prefetcher:
    """Runs Drill queries for the first rows on screen in the background (SPEC §15, Prefetch).

    One query at a time agent-wide, each with its own timeout; at most `max_held` results held
    agent-wide, oldest dropped. Held results are not Results until clicked.
    """

    def __init__(self, runner: Any, *, rows: int, timeout_ms: int, max_held: int):
        self.runner = runner
        self.rows = rows
        self.timeout_ms = timeout_ms
        self.max_held = max_held
        self._sem = asyncio.Semaphore(1)
        self._convs: dict[str, _ConvPrefetch] = {}
        self._held: OrderedDict[tuple[str, str, int], _Item] = OrderedDict()

    def _conv(self, conv_id: str) -> _ConvPrefetch:
        return self._convs.setdefault(conv_id, _ConvPrefetch())

    def cancel(self, conv_id: str) -> None:
        """Cancel queued and running prefetch of a Conversation. Held results stay."""
        state = self._convs.get(conv_id)
        if state is None:
            return
        for item in list(state.items.values()):
            if item.state in ("queued", "running") and item.task is not None:
                item.task.cancel()
        state.items = {k: v for k, v in state.items.items() if v.state == "done"}

    def start(self, conv_id: str, jobs: Iterable[tuple[DrillSpec, int, list[Any]]]) -> None:
        """Replace the Conversation's prefetch with `jobs` (spec, row index, bound values), in order."""
        jobs = list(jobs)
        if self.rows <= 0 or not jobs:
            return
        self.cancel(conv_id)
        state = self._conv(conv_id)
        for spec, row, values in jobs:
            key = (conv_id, spec.source, row)
            if (spec.source, row) in state.items:
                continue  # already held
            item = _Item(key=key)
            item.task = asyncio.create_task(self._run(item, spec, values))
            state.items[(spec.source, row)] = item

    async def _run(self, item: _Item, spec: DrillSpec, values: list[Any]) -> None:
        from explorer_agent.sql import SqlFail

        try:
            async with self._sem:
                item.state = "running"
                outcome = await self.runner.run_bound(spec.sql, values, timeout_ms=self.timeout_ms)
        except asyncio.CancelledError:
            item.state = "dropped"
            raise
        except Exception:
            log.exception("prefetch failed: source=%s row=%d", spec.source, item.key[2])
            item.state = "failed"
            return
        if isinstance(outcome, SqlFail):
            log.warning("prefetch failed: source=%s row=%d sqlstate=%s", spec.source, item.key[2], outcome.sqlstate)
            item.state = "failed"
            return
        item.state, item.result = "done", outcome
        self._held[item.key] = item
        while len(self._held) > self.max_held:
            _, old = self._held.popitem(last=False)
            old.state, old.result = "dropped", None
            state = self._convs.get(old.key[0])
            if state is not None:
                state.items.pop((old.key[1], old.key[2]), None)

    async def take(self, conv_id: str, source: str, row: int) -> Any:
        """The prefetched outcome for a click, or None when the click must run the query itself.

        Held: returned, no query. Running: waited for. Queued: cancelled. Cancelled while
        waited for, failed or dropped: None.
        """
        state = self._convs.get(conv_id)
        item = state.items.get((source, row)) if state else None
        if item is None:
            return None
        if item.state == "queued" and item.task is not None:
            item.task.cancel()
            state.items.pop((source, row), None)
            return None
        if item.state == "running" and item.task is not None:
            await asyncio.wait({item.task})  # never raises, even when the task is cancelled
        if item.state != "done":
            state.items.pop((source, row), None)
            return None
        self._held.pop(item.key, None)
        state.items.pop((source, row), None)
        return item.result

    def held(self, conv_id: str) -> list[tuple[str, int]]:
        state = self._convs.get(conv_id)
        return [k for k, v in state.items.items() if v.state == "done"] if state else []

    def forget(self, conv_id: str) -> None:
        self.cancel(conv_id)
        state = self._convs.pop(conv_id, None)
        if state:
            for item in state.items.values():
                self._held.pop(item.key, None)
