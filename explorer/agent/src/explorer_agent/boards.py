"""Boards (SPEC §18, ADR 0028): views written with the Dataset and rendered without the model.

A Board file (`datasets/<name>/boards/<board>.toml`) holds SQL and an Express layout whose Macro
calls name queries where an answer names Result IDs. Rendering runs the queries, turns each
query's rows into a Result owned by the Board instance, and builds the layout in Board mode. Every
figure on a Board comes from the database when it renders (ADR 0001).

Slices 1a to 2: Controls (typed placeholders, re-query in place) but no keys yet. Board Visuals
are clickable, and their action sources belong to the Board instance. Pure parts: loading and
checking files, Control values (validation, binding, the Controls Card) and changed sets. Async
parts: the startup check, the cache with single-flight, and the warm-up of the Explorer.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime as dt
import logging
import re
import time
import tomllib
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from a2ui.core.exceptions import A2uiError

from explorer_agent.drill import DrillSpec, DrillSqlError, check_output, display_sql, normalise, rewrite, wrap
from explorer_agent.macros import VISUAL_MACROS, check_columns, placeholders, static_drill
from explorer_agent.results import NUMERIC_TYPES, StoredResult
from explorer_agent.sql import GuardError, SqlFail, guard

if TYPE_CHECKING:  # ui imports prompt, which imports config, which imports this module
    from explorer_agent.actions import ActionSource, IdAllocator, Level
    from explorer_agent.ui import UiPipeline, UiResult

log = logging.getLogger(__name__)

EXPLORER = "explorer"  # the Explorer Board's file stem, required once boards/ exists
BOARDS_DIR = "boards"
MAX_BOARD_QUERIES = 12
QUERY_NAME = re.compile(r"[a-z_][a-z0-9_]*")
RESULT_ID = re.compile(r"r\d+")
RESERVED_PREFIX = "board_"  # IDs the server adds around the layout: board_heading, board_layout, ...
BOARD_ALIAS = "_explorer_board"
CHECK_ALIAS = "_explorer_board_check"
DRILL_CHECK_ALIAS = "_explorer_board_drill_check"
KEYS = frozenset({"title", "question", "scope", "controls", "queries", "layout", "key", "key_type", "key_columns"})
KEY_KEYS = ("key", "key_type", "key_columns")
OPTIONS_ALIAS = "_explorer_board_options"
# Control kinds and their keys besides name, kind and label (SPEC §18 Controls).
CONTROL_KEYS = {
    "choice": frozenset({"type", "options_sql", "all_label", "default"}),
    "multi": frozenset({"type", "options_sql", "default"}),
    "toggle": frozenset({"default"}),
    "number": frozenset({"default", "min", "max", "range_sql"}),
    "date": frozenset({"default", "min", "max", "range_sql", "optional"}),
}
IMPLIED_TYPE = {"toggle": "bool", "number": "int", "date": "date"}
SQL_TYPES = {"text": "text", "int": "bigint", "float": "double precision", "date": "date", "bool": "boolean"}
ALL = "all"  # a choice's "All" in the data model
FILTERABLE_OVER = 10  # a multi with more options than this gets the filter box
CONTROLS_NOTE = "First {n} options shown"
DEFAULTS_TEXT = "Controls reset"
BOARD_COLD_TARGET_S = 1.5  # SPEC §18 speed targets: any Board, cold

# Statement names in the Express layout (`name = ...` at the start of a line).
_STATEMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", re.MULTILINE)


class BoardError(ValueError):
    """A Board file problem. The message names the file and the key."""


@dataclass(frozen=True)
class Control:
    """A Board's Control (SPEC §18): what it binds, its default and where its options come from."""

    name: str
    kind: str  # choice | multi | toggle | number | date
    label: str
    type: str  # a key of SQL_TYPES
    default: Any  # choice: an option value or "all"; multi: a tuple; toggle: bool; number: int; date: ISO or ""
    options_sql: str = ""
    all_label: str = ""
    min: Any = None  # number: int; date: ISO
    max: Any = None
    range_sql: str = ""
    optional: bool = False  # date only: it can be cleared (NULL)

    @property
    def cast(self) -> str:
        """The placeholder's cast: `int` binds as `bigint`, and a `multi` as an array."""
        base = SQL_TYPES[self.type]
        return f"{base}[]" if self.kind == "multi" else base

    @property
    def nullable(self) -> bool:
        return (self.kind == "choice" and bool(self.all_label)) or self.kind == "multi" or (self.kind == "date" and self.optional)


@dataclass(frozen=True)
class BoardQuery:
    name: str
    sql: str  # normalised: no trailing `;`, ready to wrap; `:name` placeholders as written
    dollar: str = ""  # the typed `$n` form that runs; "" when there are no placeholders
    names: tuple[str, ...] = ()  # the Controls bound, in `$n` order

    @property
    def runs(self) -> str:
        return self.dollar or self.sql


@dataclass(frozen=True)
class Board:
    name: str  # the file stem
    path: Path
    title: str
    question: str
    scope: str
    queries: tuple[BoardQuery, ...]
    layout: str  # Express statements, without <a2ui>
    controls: tuple[Control, ...] = ()
    # An Entity Board (slice 3): the placeholder the clicked row's key binds to, its SQL type,
    # and the Result columns whose rows open it. Empty for the Explorer.
    key: str = ""
    key_type: str = ""
    key_columns: tuple[str, ...] = ()

    @property
    def express(self) -> str:
        return f"<a2ui>\n{self.layout}\n</a2ui>"

    def control(self, name: str) -> Control:
        return next(c for c in self.controls if c.name == name)

    @property
    def casts(self) -> dict[str, str]:
        """The Controls' casts, as Drill queries bind them: the key isn't bound there (slice 3)."""
        return {c.name: c.cast for c in self.controls}


# --- loading (pure) ------------------------------------------------------------------------------


def load_boards(dataset_dir: Path, allowed_schemas: tuple[str, ...]) -> dict[str, Board]:
    """The Dataset's Boards by name. No `boards/` directory: none. With one, `explorer.toml` is
    required. Raises BoardError naming the file and key."""
    directory = dataset_dir / BOARDS_DIR
    if not directory.is_dir():
        return {}
    paths = sorted(directory.glob("*.toml"))
    if not any(p.stem == EXPLORER for p in paths):
        raise BoardError(f"{directory}: {EXPLORER}.toml is required once a Dataset has a {BOARDS_DIR}/ directory")
    boards = {p.stem: load_board(p, allowed_schemas) for p in paths}
    explorer = boards[EXPLORER]
    if explorer.key:
        raise BoardError(f"{explorer.path}: {EXPLORER}.toml can't have a key: the Explorer opens without a row")
    opens: dict[str, Board] = {}
    for board in boards.values():
        for column in board.key_columns:
            other = opens.get(column)
            if other is not None:
                raise BoardError(f"{board.path}: key_columns: {column} also opens {other.path}")
            opens[column] = board
    return boards


def entity_columns(boards: Mapping[str, Board]) -> dict[str, str]:
    """Key column -> the name of the Entity Board its rows open (SPEC §18 slice 3)."""
    return {column: b.name for b in boards.values() for column in b.key_columns}


def _text(data: Mapping[str, Any], key: str, path: Path, *, required: bool) -> str:
    if key not in data:
        if required:
            raise BoardError(f"{path}: missing key {key}")
        return ""
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise BoardError(f"{path}: {key} must be non-empty text")
    return value.strip()


def load_board(path: Path, allowed_schemas: tuple[str, ...]) -> Board:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise BoardError(f"{path} is not valid TOML: {e}") from None
    return parse_board(text, path, allowed_schemas)


def parse_board(text: str, path: Path, allowed_schemas: tuple[str, ...]) -> Board:
    """A Board from its TOML text; `path` names it in errors (and its stem is its name)."""
    if not QUERY_NAME.fullmatch(path.stem):
        raise BoardError(f"{path}: a Board file name must be lower case letters, digits and _")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise BoardError(f"{path} is not valid TOML: {e}") from None
    for key in sorted(data):
        if key not in KEYS:
            raise BoardError(f"{path}: unknown key {key}")
    title = _text(data, "title", path, required=True)
    if any(ch.isdigit() for ch in title):
        raise BoardError(f"{path}: title must be plain text with no numbers")
    controls = _controls(data, path, allowed_schemas)
    key, key_type, key_columns = _key(data, path, controls)
    queries = _queries(data, path, allowed_schemas, controls, key, key_type)
    layout = _text(data, "layout", path, required=True)
    defined = _STATEMENT.findall(layout)
    if "root" not in defined:
        raise BoardError(f"{path}: layout must define root")
    names = {q.name for q in queries}
    for name in defined:
        if name.startswith(RESERVED_PREFIX):
            raise BoardError(f"{path}: layout name {name} is reserved (names starting {RESERVED_PREFIX} are the server's)")
        if name in names:
            raise BoardError(f"{path}: layout name {name} is also a query name")
        if "__" in name:  # built IDs are `<panel>__<n>`: `_root` splits at the first `__`
            raise BoardError(f"{path}: layout name {name} must not contain __")
    return Board(
        name=path.stem, path=path, title=title, question=_text(data, "question", path, required=False),
        scope=_text(data, "scope", path, required=False), queries=queries, layout=layout, controls=controls,
        key=key, key_type=key_type, key_columns=key_columns,
    )


def _key(data: Mapping[str, Any], path: Path, controls: tuple[Control, ...]) -> tuple[str, str, tuple[str, ...]]:
    """An Entity Board's `key`, `key_type` and `key_columns` (all three or none)."""
    present = [k for k in KEY_KEYS if k in data]
    if not present:
        return "", "", ()
    if len(present) != len(KEY_KEYS):
        missing = ", ".join(k for k in KEY_KEYS if k not in data)
        raise BoardError(f"{path}: key, key_type and key_columns go together; missing {missing}")
    key, key_type, columns = data["key"], data["key_type"], data["key_columns"]
    if not isinstance(key, str) or not QUERY_NAME.fullmatch(key) or RESULT_ID.fullmatch(key):
        raise BoardError(f"{path}: key must match [a-z_][a-z0-9_]* and not look like a Result ID")
    if any(c.name == key for c in controls):
        raise BoardError(f"{path}: key {key} is also a Control name")
    if key_type not in SQL_TYPES:
        raise BoardError(f"{path}: key_type must be one of: {', '.join(SQL_TYPES)}")
    if (not isinstance(columns, list) or not columns
            or not all(isinstance(c, str) and QUERY_NAME.fullmatch(c) for c in columns)):
        raise BoardError(f"{path}: key_columns must be a non-empty list of column names ([a-z_][a-z0-9_]*)")
    for c in columns:
        if columns.count(c) > 1:
            raise BoardError(f"{path}: key_columns names {c} twice")
    return key, key_type, tuple(columns)


_YMD = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


def _iso_date(value: Any) -> bool:
    """`YYYY-MM-DD` only: `date.fromisoformat` also takes week dates such as `2026-W01-1`."""
    if not isinstance(value, str) or not _YMD.fullmatch(value):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or isinstance(value, dt.date)


def _side_sql(sql: Any, where: str, key: str, allowed_schemas: tuple[str, ...]) -> str:
    """An `options_sql` or `range_sql`: one guarded statement with no placeholders."""
    if not isinstance(sql, str) or not sql.strip():
        raise BoardError(f"{where}.{key} must be non-empty text")
    try:
        names = rewrite(sql).names
    except DrillSqlError as e:
        raise BoardError(f"{where}.{key}: {str(e).removeprefix('drill_sql: ')}") from None
    if names:
        raise BoardError(f"{where}.{key}: takes no placeholders, found :{names[0]}")
    try:
        guard(sql, allowed_schemas)
    except GuardError as e:
        raise BoardError(f"{where}.{key}: {e}") from None
    return normalise(sql)


def _controls(data: Mapping[str, Any], path: Path, allowed_schemas: tuple[str, ...]) -> tuple[Control, ...]:
    raw = data.get("controls", [])
    if not isinstance(raw, list):
        raise BoardError(f"{path}: controls must be [[controls]] tables")
    out: list[Control] = []
    for i, c in enumerate(raw):
        where = f"{path}: controls[{i}]"
        if not isinstance(c, dict):
            raise BoardError(f"{where} must be a table")
        name = c.get("name")
        if not isinstance(name, str) or not QUERY_NAME.fullmatch(name) or RESULT_ID.fullmatch(name):
            raise BoardError(f"{where}: name must match [a-z_][a-z0-9_]* and not look like a Result ID")
        where = f"{path}: controls.{name}"
        if any(o.name == name for o in out):
            raise BoardError(f"{where}: name {name} is used twice")
        kind = c.get("kind")
        if kind not in CONTROL_KEYS:
            raise BoardError(f"{where}.kind must be one of: {', '.join(CONTROL_KEYS)}")
        extra = sorted(set(c) - {"name", "kind", "label"} - CONTROL_KEYS[kind])
        if extra:
            hint = " (top-level keys must come before the first [[controls]])" if extra[0] in KEYS else ""
            raise BoardError(f"{where}: unknown key {extra[0]} for a {kind}{hint}")
        label = c.get("label")
        if not isinstance(label, str) or not label.strip():
            raise BoardError(f"{where}.label must be non-empty text")
        out.append(_control(c, name, kind, label.strip(), where, allowed_schemas))
    return tuple(out)


def _control(c: Mapping[str, Any], name: str, kind: str, label: str, where: str,
             allowed_schemas: tuple[str, ...]) -> Control:
    type_ = IMPLIED_TYPE.get(kind) or c.get("type")
    if type_ not in SQL_TYPES:
        raise BoardError(f"{where}.type must be one of: {', '.join(SQL_TYPES)}")
    fields: dict[str, Any] = {}
    if kind in ("choice", "multi"):
        fields["options_sql"] = _side_sql(c.get("options_sql"), where, "options_sql", allowed_schemas)
    if kind == "choice":
        all_label = c.get("all_label", "")
        if not isinstance(all_label, str) or ("all_label" in c and not all_label.strip()):
            raise BoardError(f"{where}.all_label must be non-empty text")
        fields["all_label"] = all_label.strip()
        default = c.get("default", ALL if all_label else None)
        if default == ALL and not all_label:
            raise BoardError(f'{where}.default "all" needs all_label')
        if default is not None and not _scalar(default):
            raise BoardError(f"{where}.default must be an option value or \"all\"")
    elif kind == "multi":
        default = c.get("default", [])
        if not isinstance(default, list) or not all(_scalar(v) for v in default):
            raise BoardError(f"{where}.default must be a list of option values")
        default = tuple(default)
    elif kind == "toggle":
        default = c.get("default", False)
        if not isinstance(default, bool):
            raise BoardError(f"{where}.default must be true or false")
    elif kind == "number":
        default = c.get("default")
        if not _int(default):
            raise BoardError(f"{where}.default must be an integer (number Controls are integer-only)")
        fields.update(_range(c, where, allowed_schemas, _int, "an integer"))
        lo, hi = fields.get("min"), fields.get("max")
        if lo is not None and not lo <= default <= hi:
            raise BoardError(f"{where}.default must be between min and max")
    else:  # date
        optional = c.get("optional", False)
        if not isinstance(optional, bool):
            raise BoardError(f"{where}.optional must be true or false")
        fields["optional"] = optional
        default = c.get("default", "" if optional else None)
        if default == "" and not optional:
            raise BoardError(f'{where}.default "" needs optional = true')
        if default != "" and not _iso_date(default):
            raise BoardError(f"{where}.default must be an ISO date (YYYY-MM-DD)")
        fields.update(_range(c, where, allowed_schemas, _iso_date, "an ISO date", required=False))
    return Control(name=name, kind=kind, label=label, type=type_, default=default, **fields)


def _range(c: Mapping[str, Any], where: str, allowed_schemas: tuple[str, ...], ok: Callable[[Any], bool],
           what: str, *, required: bool = True) -> dict[str, Any]:
    has_static = "min" in c or "max" in c
    if "range_sql" in c:
        if has_static:
            raise BoardError(f"{where}: give min and max, or range_sql, not both")
        return {"range_sql": _side_sql(c["range_sql"], where, "range_sql", allowed_schemas)}
    if not has_static:
        if required:
            raise BoardError(f"{where}: needs min and max, or range_sql")
        return {}
    lo, hi = c.get("min"), c.get("max")
    if not ok(lo) or not ok(hi):
        raise BoardError(f"{where}: min and max must both be {what}")
    if lo > hi:
        raise BoardError(f"{where}: min must not be above max")
    return {"min": lo, "max": hi}


def _queries(data: Mapping[str, Any], path: Path, allowed_schemas: tuple[str, ...],
             controls: tuple[Control, ...] = (), key: str = "", key_type: str = "") -> tuple[BoardQuery, ...]:
    raw = data.get("queries")
    if not isinstance(raw, list) or not raw:
        raise BoardError(f"{path}: [[queries]] must hold at least one query")
    if len(raw) > MAX_BOARD_QUERIES:
        raise BoardError(f"{path}: queries: at most {MAX_BOARD_QUERIES} per Board, found {len(raw)}")
    out: list[BoardQuery] = []
    for i, q in enumerate(raw):
        where = f"{path}: queries[{i}]"
        if not isinstance(q, dict):
            raise BoardError(f"{where} must be a table")
        extra = sorted(set(q) - {"name", "sql"})
        if extra:
            hint = " (top-level keys must come before the first [[queries]])" if extra[0] in KEYS else ""
            raise BoardError(f"{where}: unknown key {extra[0]}{hint}")
        name = q.get("name")
        if not isinstance(name, str) or not QUERY_NAME.fullmatch(name):
            raise BoardError(f"{where}: name must match [a-z_][a-z0-9_]*")
        if RESULT_ID.fullmatch(name):
            raise BoardError(f"{where}: name {name} looks like a Result ID (r<digits>)")
        if any(o.name == name for o in out):
            raise BoardError(f"{where}: name {name} is used twice")
        sql = q.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            raise BoardError(f"{path}: queries.{name}.sql must be non-empty text")
        casts = {c.name: c.cast for c in controls} | ({key: SQL_TYPES[key_type]} if key else {})
        try:
            rewritten = rewrite(sql, casts)
        except DrillSqlError as e:
            raise BoardError(f"{path}: queries.{name}.sql: {str(e).removeprefix('drill_sql: ')}") from None
        unknown = [n for n in rewritten.names if n not in casts]
        if unknown:
            raise BoardError(
                f"{path}: queries.{name}.sql: placeholder :{unknown[0]} names no Control or the key"
            )
        try:
            guard(rewritten.sql, allowed_schemas)
        except GuardError as e:
            raise BoardError(f"{path}: queries.{name}.sql: {e}") from None
        out.append(BoardQuery(name=name, sql=normalise(sql), dollar=rewritten.sql if rewritten.names else "",
                              names=rewritten.names))
    if key and not any(key in q.names for q in out):
        raise BoardError(f"{path}: key {key}: no query uses :{key}")
    return tuple(out)


# --- startup check (database, no rows) -------------------------------------------------------------


Outputs = dict[str, tuple[list[str], list[str]]]


def layout_components(board: Board, pipeline: UiPipeline) -> list[dict[str, Any]]:
    """The layout's components as parsed, before macro expansion. Raises BoardError."""
    try:
        parts = pipeline.format.create_parser().parse_response(board.express)
    except A2uiError as e:
        raise BoardError(f"{board.path}: layout does not parse: {e}") from None
    comps: list[dict[str, Any]] = []
    for part in parts:
        for message in getattr(part, "a2ui", None) or []:
            update = getattr(message, "update_components", None)
            if update is not None:
                comps.extend(c for c in update.components if isinstance(c, dict))
    if not comps:
        raise BoardError(f"{board.path}: layout has no components")
    return comps


def startup_values(board: Board) -> dict[str, Any]:
    """Each Control's bound value at its declared default, before any options are known (the
    startup check fetches no rows): a `choice` binds its default value as written."""
    out: dict[str, Any] = {}
    for c in board.controls:
        if c.kind == "choice":
            out[c.name] = None if c.default in (ALL, None) else c.default
        elif c.kind == "multi":
            out[c.name] = list(c.default) or None
        elif c.kind == "date":
            out[c.name] = c.default or None
        else:
            out[c.name] = c.default
    if board.key:
        out[board.key] = None  # the check fetches no rows: a NULL key is enough to plan
    return out


async def _limit0(runner: Any, q: BoardQuery, values: Mapping[str, Any]) -> Any:
    if q.names:
        return await runner.run_bound(wrap(q.dollar, CHECK_ALIAS, " LIMIT 0"), [values[n] for n in q.names])
    return await runner.run(wrap(q.sql, CHECK_ALIAS, " LIMIT 0"))


def _fails(outcome: SqlFail) -> str:
    state = f" (SQLSTATE {outcome.sqlstate})" if outcome.sqlstate else ""
    hint = "; add a cast to the placeholder, e.g. :name::text" if outcome.sqlstate == "42P18" else ""
    return f"{state}: {outcome.message}{hint}"


async def check_board(board: Board, runner: Any, pipeline: UiPipeline) -> Outputs:
    """Each query as `LIMIT 0` (no rows fetched) at the Controls' defaults, and again with every
    nullable Control at NULL, giving its output columns and types; the option and range queries;
    then the layout's columns-only check and the Drill queries. Raises BoardError."""
    defaults = startup_values(board)
    runs = [("at the defaults", defaults)]
    nullable = [c.name for c in board.controls if c.nullable]
    if nullable:
        runs.append(("with its nullable Controls NULL", {**defaults, **dict.fromkeys(nullable)}))
    outputs: Outputs = {}
    for q in board.queries:
        for label, values in runs if q.names else runs[:1]:
            outcome = await _limit0(runner, q, values)
            if isinstance(outcome, SqlFail):
                at = f" {label}" if q.names else ""
                raise BoardError(f"{board.path}: queries.{q.name}.sql fails{at}{_fails(outcome)}")
            outputs.setdefault(q.name, (outcome.columns, outcome.column_types))
    for c in board.controls:
        for q, (columns, _types) in outputs.items():
            if c.name in columns:
                raise BoardError(f"{board.path}: controls.{c.name}: name is also an output column of query {q!r}")
        await _check_side_queries(board, c, runner)
    comps = layout_components(board, pipeline)
    problems = [f"{c.get('id')}: FollowUp is for answers, not Boards" for c in comps if c.get("component") == "FollowUp"]
    problems += check_columns(comps, outputs)
    if problems:
        raise BoardError(f"{board.path}: layout: {'; '.join(problems)}")
    used = {n for q in board.queries for n in q.names} | {n for d in panel_dependencies(board, comps).values() for n in d[1]}
    for c in board.controls:
        if c.name not in used:
            raise BoardError(f"{board.path}: controls.{c.name}: no query or drill_sql uses :{c.name}")
    await _check_drills(board, comps, outputs, runner, pipeline.allowed_schemas)
    return outputs


async def _check_side_queries(board: Board, c: Control, runner: Any) -> None:
    where = f"{board.path}: controls.{c.name}"
    if c.options_sql:
        outcome = await runner.run(wrap(c.options_sql, CHECK_ALIAS, " LIMIT 0"))
        if isinstance(outcome, SqlFail):
            raise BoardError(f"{where}.options_sql fails{_fails(outcome)}")
        missing = [k for k in ("value", "label") if k not in outcome.columns]
        if missing:
            raise BoardError(f"{where}.options_sql must return value and label columns; missing {', '.join(missing)}")
    if c.range_sql:
        outcome = await runner.run(wrap(c.range_sql, CHECK_ALIAS, " LIMIT 0"))
        if isinstance(outcome, SqlFail):
            raise BoardError(f"{where}.range_sql fails{_fails(outcome)}")
        kinds = dict(zip(outcome.columns, outcome.column_types, strict=True))
        for k in ("min", "max"):
            if k not in kinds:
                raise BoardError(f"{where}.range_sql must return min and max columns; missing {k}")
            ok = kinds[k] in NUMERIC_TYPES if c.kind == "number" else kinds[k] == "date"
            if not ok:
                want = "a number" if c.kind == "number" else "a date"
                raise BoardError(f"{where}.range_sql: {k} is {kinds[k]}, not {want}")


def panel_dependencies(board: Board, comps: list[dict[str, Any]]) -> dict[str, tuple[frozenset[str], frozenset[str]]]:
    """Per layout component (its root ID): the queries it reads (a Macro's `result_id`, and the
    `{query[i].col}` placeholders in its texts) and the Controls its `drill_sql` binds. Only
    components with any are listed. A Control change re-sends exactly these (SPEC §18 slice 2)."""
    casts = board.casts
    out: dict[str, tuple[frozenset[str], frozenset[str]]] = {}
    for comp in comps:
        queries: set[str] = set()
        texts = [comp.get(k) for k in ("text", "template", "title", "label", "drill_title")]
        texts += [t.get("title") for t in comp.get("tabs") or [] if isinstance(t, dict)]
        for text in texts:
            if isinstance(text, str):
                queries.update(q for q, *_ in placeholders(text, board=True))
        if comp.get("component") in VISUAL_MACROS or comp.get("component") == "Metric":
            if isinstance(comp.get("result_id"), str):
                queries.add(comp["result_id"])
        controls: set[str] = set()
        sql = comp.get("drill_sql")
        if isinstance(sql, str) and sql.strip() and casts:
            try:
                controls = {n for n in rewrite(sql, casts).names if n in casts}
            except DrillSqlError:
                controls = set()  # the Drill query check reports it
        if queries or controls:
            out[str(comp.get("id"))] = (frozenset(queries), frozenset(controls))
    return out


async def _check_drills(board: Board, comps: list[dict[str, Any]], outputs: Outputs, runner: Any,
                        allowed_schemas: tuple[str, ...]) -> None:
    """Each Visual's `drill_sql` (SPEC §18 slice 1b): §15's checks that need no rows, against the
    query's output columns, then a `LIMIT 0` run with every row placeholder bound to NULL and the
    Controls at their defaults, and the `drill_x`/`drill_y` check on its output. Row-dependent
    checks happen at render."""
    casts = board.casts
    defaults = startup_values(board)
    for comp in comps:
        sql = comp.get("drill_sql")
        if comp.get("component") not in VISUAL_MACROS or not isinstance(sql, str) or not sql.strip():
            continue
        where = f"{board.path}: layout: {comp.get('id')}: "
        query = comp["result_id"]  # check_columns has checked it names a query
        drill_as, drill_x, drill_y = (str(comp.get(k) or d) for k, d in (("drill_as", "table"), ("drill_x", ""), ("drill_y", "")))
        try:
            rewritten = static_drill(outputs[query][0], f"query {query!r}", drill_sql=sql, drill_as=drill_as,
                                     drill_x=drill_x, drill_y=drill_y, allowed_schemas=allowed_schemas, controls=casts)
        except ValueError as e:
            raise BoardError(f"{where}{e}") from None
        values = [defaults[n] if n in casts else None for n in rewritten.names]
        outcome = await runner.run_bound(wrap(rewritten.sql, DRILL_CHECK_ALIAS, " LIMIT 0"), values)
        if isinstance(outcome, SqlFail):
            raise BoardError(f"{where}drill_sql fails with its placeholders NULL{_fails(outcome)}")
        spec = DrillSpec(source="", visual=0, sql=rewritten.sql, names=rewritten.names, drill_as=drill_as,
                         drill_x=drill_x.strip(), drill_y=drill_y.strip(), title="")
        problems = check_output(spec, outcome.columns, outcome.column_types)
        if problems:
            raise BoardError(f"{where}{'; '.join(problems)}")


# --- Control values (pure) ------------------------------------------------------------------------


class ControlError(ValueError):
    """A `controls` action's values are rejected; nothing runs. The message is shown to the user."""


@dataclass(frozen=True)
class Options:
    """A Control's options and range at render (SPEC §18): kept per Board instance."""

    values: tuple[Any, ...] = ()  # option values as the database returned them: what binds
    labels: tuple[str, ...] = ()
    cut: bool = False  # more than MAX_CONTROL_OPTIONS: the rest were dropped
    lo: Any = None  # number: int; date: ISO
    hi: Any = None


def options_of(board: Board, side: Mapping[str, StoredResult], max_options: int) -> dict[str, Options]:
    """Each Control's Options from its option and range queries' rows (`side` by
    `options.<name>` / `range.<name>`). NULL option values are dropped: "All" is the NULL."""
    out: dict[str, Options] = {}
    for c in board.controls:
        values: list[Any] = []
        labels: list[str] = []
        cut = False
        opts = side.get(f"options.{c.name}")
        if opts is not None:
            rows = [r for r in opts.rows if r.get("value") is not None]
            cut = len(rows) > max_options or opts.truncated
            for r in rows[:max_options]:
                values.append(r["value"])
                labels.append("" if r.get("label") is None else str(r["label"]))
        lo, hi = c.min, c.max
        rng = side.get(f"range.{c.name}")
        if rng is not None and rng.rows:
            row = rng.rows[0]
            if c.kind == "number":
                lo = None if row.get("min") is None else round(float(row["min"]))
                hi = None if row.get("max") is None else round(float(row["max"]))
            else:
                lo = None if row.get("min") is None else str(row["min"])[:10]
                hi = None if row.get("max") is None else str(row["max"])[:10]
        if c.kind == "number" and (lo is None or hi is None):
            lo = hi = c.default  # an empty range: the slider holds its default
        out[c.name] = Options(tuple(values), tuple(labels), cut, lo, hi)
    return out


def _index(options: Options, value: Any) -> int | None:
    for i, v in enumerate(options.values):
        if v == value or str(v) == str(value):
            return i
    return None


def default_state(board: Board, options: Mapping[str, Options]) -> dict[str, Any]:
    """The Controls' values at their defaults, in wire form (what the client sends): an option
    index or None (All), index lists, bool, int, ISO date or None. A default that isn't among
    the options falls back (logged by Control name only)."""
    out: dict[str, Any] = {}
    for c in board.controls:
        o = options[c.name]
        if c.kind == "choice":
            index = None if c.default in (ALL, None) else _index(o, c.default)
            if c.default not in (ALL, None) and index is None:
                log.warning("board control default not among its options: board=%s control=%s", board.name, c.name)
            if index is None and not c.all_label and o.values:
                index = 0
            out[c.name] = index
        elif c.kind == "multi":
            found = [_index(o, v) for v in c.default]
            if None in found:
                log.warning("board control default not among its options: board=%s control=%s", board.name, c.name)
            out[c.name] = sorted({i for i in found if i is not None})
        elif c.kind == "number":
            clamped = min(max(c.default, o.lo), o.hi)
            if clamped != c.default:
                log.warning("board control default outside its range: board=%s control=%s", board.name, c.name)
            out[c.name] = clamped
        elif c.kind == "date":
            out[c.name] = c.default or None
        else:
            out[c.name] = c.default
    return out


def bound_values(board: Board, options: Mapping[str, Options], state: Mapping[str, Any]) -> dict[str, Any]:
    """What each Control's placeholder binds: an option's value, a list (None when empty), and so on."""
    out: dict[str, Any] = {}
    for c in board.controls:
        v = state[c.name]
        o = options[c.name]
        if c.kind == "choice":
            out[c.name] = None if v is None else o.values[v]
        elif c.kind == "multi":
            out[c.name] = [o.values[i] for i in v] or None
        else:
            out[c.name] = v
    return out


def validate_values(board: Board, options: Mapping[str, Options], values: Any) -> dict[str, Any]:
    """A `controls` action's values (SPEC §18 Validation), in wire form. Raises ControlError."""
    if not isinstance(values, dict):
        raise ControlError("The action is malformed.")
    names = [c.name for c in board.controls]
    unknown = [k for k in values if k not in names]
    if unknown:
        raise ControlError("Unknown control.")
    missing = [n for n in names if n not in values]
    if missing:
        raise ControlError("A control is missing.")
    out: dict[str, Any] = {}
    for c in board.controls:
        v = values[c.name]
        o = options[c.name]
        bad = ControlError(f"That value isn't allowed for {c.label}.")
        if c.kind == "choice":
            if v is None:
                if not c.all_label and o.values:
                    raise bad
            elif not (_int(v) and 0 <= v < len(o.values)):
                raise bad
        elif c.kind == "multi":
            if not isinstance(v, list) or not all(_int(i) and 0 <= i < len(o.values) for i in v) or len(set(v)) != len(v):
                raise bad
            v = sorted(v)
        elif c.kind == "toggle":
            if not isinstance(v, bool):
                raise bad
        elif c.kind == "number":
            if not _int(v) or not o.lo <= v <= o.hi:
                raise bad
        else:  # date
            if v is None:
                if not c.optional:
                    raise bad
            elif not _iso_date(v):
                raise bad
        out[c.name] = v
    return out


def data_model(board: Board, state: Mapping[str, Any]) -> dict[str, Any]:
    """`/controls` as the components bind it: a choice holds `[<index as a string>]` or
    `["all"]`, a multi index strings, a date an ISO date or ""."""
    out: dict[str, Any] = {}
    for c in board.controls:
        v = state[c.name]
        if c.kind == "choice":
            out[c.name] = [ALL] if v is None and c.all_label else [] if v is None else [str(v)]
        elif c.kind == "multi":
            out[c.name] = [str(i) for i in v]
        elif c.kind == "date":
            out[c.name] = v or ""
        else:
            out[c.name] = v
    return out


def shown_values(board: Board, options: Mapping[str, Options], state: Mapping[str, Any]) -> dict[str, str]:
    """Each Control's shown value by its label, for the log entry and the Board pair."""
    out: dict[str, str] = {}
    for c in board.controls:
        v = state[c.name]
        o = options[c.name]
        if c.kind == "choice":
            out[c.label] = (c.all_label or "none") if v is None else o.labels[v]
        elif c.kind == "multi":
            out[c.label] = ", ".join(o.labels[i] for i in v) or "none"
        elif c.kind == "toggle":
            out[c.label] = "on" if v else "off"
        elif c.kind == "date":
            out[c.label] = v or "any"
        else:
            out[c.label] = str(v)
    return out


def log_text(board: Board, options: Mapping[str, Options], state: Mapping[str, Any],
             defaults: Mapping[str, Any]) -> str:
    """The in-place log entry's full text: each Control not at its default, in order."""
    shown = shown_values(board, options, state)
    parts = [f"{c.label} set to {shown[c.label]}" for c in board.controls if state[c.name] != defaults[c.name]]
    return " · ".join(parts) or DEFAULTS_TEXT


def controls_components(board: Board, options: Mapping[str, Options]) -> list[dict[str, Any]]:
    """The Controls Card (SPEC §18): basic components bound to `/controls/<name>`. Option labels
    are database text placed by the server, rendered by Lit as plain text."""
    items: list[str] = []
    comps: list[dict[str, Any]] = []
    for c in board.controls:
        cid = f"board_control_{c.name}"
        o = options[c.name]
        bind = {"path": f"/controls/{c.name}"}
        if c.kind in ("choice", "multi"):
            opts = [{"label": c.all_label, "value": ALL}] if c.kind == "choice" and c.all_label else []
            opts += [{"label": label, "value": str(i)} for i, label in enumerate(o.labels)]
            comp = {"id": cid, "component": "ChoicePicker", "label": c.label, "options": opts, "value": bind,
                    "variant": "multipleSelection" if c.kind == "multi" else "mutuallyExclusive"}
            if len(o.values) > FILTERABLE_OVER:
                comp["filterable"] = True
        elif c.kind == "toggle":
            comp = {"id": cid, "component": "CheckBox", "label": c.label, "value": bind}
        elif c.kind == "number":
            comp = {"id": cid, "component": "Slider", "label": c.label, "min": o.lo, "max": o.hi, "value": bind}
        else:
            comp = {"id": cid, "component": "DateTimeInput", "label": c.label, "value": bind, "enableDate": True}
            if o.lo is not None:
                comp["min"] = o.lo
            if o.hi is not None:
                comp["max"] = o.hi
        comps.append(comp)
        items.append(cid)
        if o.cut:
            comps.append({"id": f"{cid}_note", "component": "Text", "variant": "caption",
                          "text": CONTROLS_NOTE.format(n=len(o.values))})
            items.append(f"{cid}_note")
    return [
        {"id": "board_controls", "component": "Card", "child": "board_controls_items"},
        {"id": "board_controls_items", "component": "Column", "children": items},
        *comps,
    ]


def changed_panels(board: Board, deps: Mapping[str, tuple[frozenset[str], frozenset[str]]],
                   changed: set[str]) -> tuple[set[str], set[str]]:
    """The queries to re-run and the panels to re-send for a change of the Controls `changed`."""
    queries = {q.name for q in board.queries if set(q.names) & changed}
    panels = {p for p, (qs, cs) in deps.items() if qs & queries or cs & changed}
    return queries, panels


def _root(component_id: str) -> str:
    """The layout component a built component belongs to: its ID up to the first `__`. The
    layout's `root` is sent as `board_layout`; the server's own `root` belongs to no panel."""
    if component_id == "root":
        return ""
    return "root" if component_id == "board_layout" else component_id.split("__")[0]


def panel_components(messages: list[dict[str, Any]], panels: set[str]) -> list[dict[str, Any]]:
    """The built components of `panels`: each panel's root and its `<root>__…` expansion."""
    return [
        comp for m in messages for comp in (m.get("updateComponents") or {}).get("components", [])
        if _root(str(comp.get("id"))) in panels
    ]


def panel_sources(messages: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Per layout component, the action source IDs its built components carry."""
    out: dict[str, list[str]] = {}
    for m in messages:
        for comp in (m.get("updateComponents") or {}).get("components", []):
            action = comp.get("action")
            if isinstance(action, dict) and isinstance(action.get("source"), str):
                out.setdefault(_root(str(comp.get("id"))), []).append(action["source"])
    return out


# --- rendering ----------------------------------------------------------------------------------------


Outcome = StoredResult | SqlFail  # a query's rows (Result ID "") or its failure


@dataclass
class _Entry:
    results: dict[str, StoredResult]  # successful queries only: failures are never cached
    at: dict[str, float]  # per query, so re-running one query doesn't refresh the others' age


@dataclass
class BoardCache:
    """Rendered Results by `(board, key value, Control values)`, agent-wide (SPEC §18). The
    Explorer at its defaults is pinned: never evicted, never counted, refreshed in the background."""

    ttl_s: float
    max_entries: int
    clock: Callable[[], float] = time.monotonic
    entries: OrderedDict[tuple[Any, ...], _Entry] = field(default_factory=OrderedDict)
    pinned: dict[tuple[Any, ...], _Entry] = field(default_factory=dict)

    def get(self, key: tuple[Any, ...]) -> dict[str, StoredResult]:
        """Fresh Results for `key`. A pinned entry is served until a refresh replaces it, even
        when a refresh fails (logged by `warm`): the Explorer stays up on a database hiccup."""
        if key in self.pinned:
            return self.pinned[key].results
        entry = self.entries.get(key)
        if entry is None:
            return {}
        now = self.clock()
        return {name: r for name, r in entry.results.items() if now - entry.at[name] < self.ttl_s}

    def put(self, key: tuple[Any, ...], ran: dict[str, StoredResult], kept: dict[str, StoredResult], *,
            pin: bool = False) -> None:
        """`ran`: queries just run, stamped now; `kept`: cached ones, keeping their own age."""
        now = self.clock()
        old = (self.pinned if pin else self.entries).get(key)
        at = {name: (old.at[name] if old and name in old.at else now) for name in kept}
        at.update({name: now for name in ran})
        entry = _Entry({**kept, **ran}, at)
        if pin:
            self.pinned[key] = entry
            return
        self.entries.pop(key, None)
        self.entries[key] = entry
        while len(self.entries) > self.max_entries:
            self.entries.popitem(last=False)


def _hashable(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def render_key(board: Board, bound: Mapping[str, Any] | None = None, key: Any = None) -> tuple[Any, ...]:
    """The cache key: `(board, key value, Control values)`."""
    bound = bound or {}
    return (board.name, _hashable(key), tuple(_hashable(bound.get(c.name)) for c in board.controls))


def default_key(board: Board) -> tuple[Any, ...]:
    return render_key(board, startup_values(board))


class BoardSemaphore:
    """The agent-wide Board query permits (`BOARD_QUERY_CONCURRENCY`). A low-priority permit (an
    Entity Board prefetch, SPEC §18 slice 3) is granted only while no user render waits."""

    def __init__(self, permits: int):
        self.permits = permits
        self.used = 0
        self._waiting = 0  # user renders waiting for a permit
        self._cond = asyncio.Condition()

    async def acquire(self, *, low: bool = False) -> None:
        async with self._cond:
            if not low:
                self._waiting += 1
            try:
                await self._cond.wait_for(lambda: self.used < self.permits and (not low or self._waiting == 0))
            finally:
                if not low:
                    self._waiting -= 1
                    # A low waiter may be blocked only by this one: let it look again, whether this
                    # waiter got a permit or was cancelled.
                    self._cond.notify_all()
            self.used += 1

    async def release(self) -> None:
        async with self._cond:
            self.used -= 1
            self._cond.notify_all()

    @contextlib.asynccontextmanager
    async def permit(self, *, low: bool = False):
        await self.acquire(low=low)
        try:
            yield
        finally:
            await asyncio.shield(self.release())


class OptionsError(Exception):
    """A Control's option or range query failed: the Board can't be shown (logged by name)."""


class BoardRenderer:
    """Runs Board queries under an agent-wide semaphore, with the cache and single-flight."""

    def __init__(self, boards: Mapping[str, Board], runner: Any, *, concurrency: int, row_cap: int,
                 ttl_s: float, max_cache: int, clock: Callable[[], float] = time.monotonic, max_options: int = 100):
        self.boards = dict(boards)
        self._runner = runner
        self._semaphore = BoardSemaphore(concurrency)
        self._row_cap = row_cap
        self.max_options = max_options
        self.cache = BoardCache(ttl_s, max_cache, clock)
        self._inflight: dict[tuple[Any, ...], asyncio.Task[dict[str, Outcome]]] = {}
        self._refresh: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task[None]] = set()  # held, so the loop doesn't drop them
        self._pinned_keys: dict[str, tuple[Any, ...]] = {}  # the Explorer's key at its defaults

    def _shared(self, key: tuple[Any, ...], make: Callable[[], Any]) -> Any:
        """One run per key at a time (single-flight). The run has its own task: a caller that is
        cancelled (a client gone) stops waiting, but the run goes on for the others and still
        fills the cache."""
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.get_running_loop().create_task(make())
            self._inflight[key] = task
            task.add_done_callback(lambda t: self._done(key, t))
        return asyncio.shield(task)

    async def outcomes(self, board: Board, bound: Mapping[str, Any] | None = None, *, fresh: bool = False,
                       only: set[str] | None = None, key: Any = None) -> dict[str, Outcome]:
        """Each query's outcome at the Control values `bound` (the startup defaults when None)
        and, for an Entity Board, the clicked row's `key` value. `only`: just these queries (a
        re-query in place)."""
        bound = startup_values(board) if bound is None else dict(bound)
        if board.key:
            bound[board.key] = key
        cache_key = render_key(board, bound, key)
        flight = (*cache_key, None if only is None else tuple(sorted(only)))
        return await self._shared(flight, lambda: self._render(board, cache_key, bound, fresh=fresh, only=only))

    async def prefetch(self, board: Board, key: Any, *, timeout_ms: int) -> None:
        """An Entity Board at its default Controls, for a row not yet clicked (SPEC §18 slice 3):
        its option queries, then its queries, one at a time on a low-priority permit, each with
        `timeout_ms`. Successful queries fill the cache; a click renders the rest."""
        options = await self.options(board, low=True, timeout_ms=timeout_ms)
        bound = bound_values(board, options, default_state(board, options)) if board.controls else {}
        bound[board.key] = key
        cache_key = render_key(board, bound, key)
        cached = self.cache.get(cache_key)
        ran: dict[str, StoredResult] = {}
        for q in board.queries:
            if q.name in cached:
                continue
            outcome = await self._run(board, q, bound, low=True, timeout_ms=timeout_ms)
            if isinstance(outcome, StoredResult):
                ran[q.name] = outcome
        if ran:
            self.cache.put(cache_key, ran, dict(self.cache.get(cache_key)))

    async def options(self, board: Board, *, fresh: bool = False, low: bool = False,
                      timeout_ms: int | None = None) -> dict[str, Options]:
        """The Controls' options and ranges, cached with the render. `fresh`: run them again (the
        background refresh), replacing a pinned entry. `low`: a prefetch's (its own flight, so a
        user render never joins a slow run). Raises OptionsError."""
        if not board.controls:
            return {}
        key = (board.name, None, "options")
        side = await self._shared((*key, fresh, low), lambda: self._side(board, key, fresh=fresh, low=low, timeout_ms=timeout_ms))
        return options_of(board, side, self.max_options)

    async def defaults(self, board: Board, *, fresh: bool = False) -> tuple[dict[str, Options], dict[str, Any], dict[str, Any]]:
        """Options, the default state (wire form) and its bound values. Raises OptionsError."""
        options = await self.options(board, fresh=fresh)
        state = default_state(board, options)
        bound = bound_values(board, options, state)
        if board.name == EXPLORER:
            self._pinned_keys[board.name] = render_key(board, bound)
        return options, state, bound

    def _done(self, key: tuple[Any, ...], task: asyncio.Task[Any]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled() and task.exception() is not None:  # retrieved, if every caller left
            log.warning("board render failed: key=%s", key[0])

    async def _side(self, board: Board, key: tuple[Any, ...], *, fresh: bool = False, low: bool = False,
                    timeout_ms: int | None = None) -> dict[str, StoredResult]:
        cached = {} if fresh else self.cache.get(key)
        jobs = [(f"options.{c.name}", c.options_sql) for c in board.controls if c.options_sql]
        jobs += [(f"range.{c.name}", c.range_sql) for c in board.controls if c.range_sql]
        todo = [(n, sql) for n, sql in jobs if n not in cached]
        if low:  # a prefetch: one at a time
            ran = [await self._run_sql(board, n, sql, sql, [], low=True, timeout_ms=timeout_ms) for n, sql in todo]
        else:
            ran = await asyncio.gather(*(self._run_sql(board, n, sql, sql, []) for n, sql in todo))
        failed = [n for (n, _), o in zip(todo, ran, strict=True) if isinstance(o, SqlFail)]
        if failed:
            raise OptionsError(failed[0])  # never cached
        ok = {n: o for (n, _), o in zip(todo, ran, strict=True)}
        if ok:
            self.cache.put(key, ok, dict(cached), pin=board.name == EXPLORER)
        return {**cached, **ok}

    async def _render(self, board: Board, key: tuple[Any, ...], bound: Mapping[str, Any], *, fresh: bool,
                      only: set[str] | None) -> dict[str, Outcome]:
        cached = {} if fresh else self.cache.get(key)
        wanted = [q for q in board.queries if only is None or q.name in only]
        todo = [q for q in wanted if q.name not in cached]
        ran = await asyncio.gather(*(self._run(board, q, bound) for q in todo))
        out: dict[str, Outcome] = {q.name: cached[q.name] for q in wanted if q.name in cached}
        out.update({q.name: o for q, o in zip(todo, ran, strict=True)})
        if todo:
            ran_ok = {q.name: o for q, o in zip(todo, ran, strict=True) if isinstance(o, StoredResult)}
            pin = board.name == EXPLORER and key == self._pinned_keys.get(board.name, default_key(board))
            self.cache.put(key, ran_ok, dict(cached), pin=pin)
        return {q.name: out[q.name] for q in wanted}

    async def _run(self, board: Board, q: BoardQuery, bound: Mapping[str, Any], *, low: bool = False,
                   timeout_ms: int | None = None) -> Outcome:
        values = [bound[n] for n in q.names]
        shown = display_sql(q.dollar, values) if q.names else q.sql
        return await self._run_sql(board, q.name, q.runs, shown, values, low=low, timeout_ms=timeout_ms)

    async def _run_sql(self, board: Board, name: str, sql: str, shown: str, values: list[Any], *,
                       low: bool = False, timeout_ms: int | None = None) -> Outcome:
        async with self._semaphore.permit(low=low):
            start = time.perf_counter()
            wrapped = wrap(sql, BOARD_ALIAS, f" LIMIT {self._row_cap + 1}")
            if timeout_ms is not None:  # a prefetch: its own, shorter timeout
                outcome = await self._runner.run_bound(wrapped, values, timeout_ms=timeout_ms)
            else:
                outcome = await (self._runner.run_bound(wrapped, values) if values else self._runner.run(wrapped))
            ms = round((time.perf_counter() - start) * 1000)
        # Logs name the Board and query only, never Control values (SPEC §18).
        if isinstance(outcome, SqlFail):
            log.warning("board query failed: board=%s query=%s sqlstate=%s ms=%d", board.name, name, outcome.sqlstate, ms)
            return outcome
        truncated = outcome.truncated or len(outcome.rows) > self._row_cap
        log.info("board query: board=%s query=%s rows=%d ms=%d", board.name, name, min(len(outcome.rows), self._row_cap), ms)
        return StoredResult(
            id="", sql=shown, purpose=f"{board.title} · {name}", columns=outcome.columns,
            column_types=outcome.column_types, rows=outcome.rows[: self._row_cap], truncated=truncated,
        )

    async def warm(self) -> None:
        """Renders the Explorer at its defaults, then again each time its entry would expire."""
        board = self.boards.get(EXPLORER)
        if board is None:
            return
        start = time.perf_counter()
        try:
            _options, _state, bound = await self.defaults(board, fresh=True)
            await self.outcomes(board, bound, fresh=True)
            log.info("board warm-up: board=%s ms=%d", board.name, round((time.perf_counter() - start) * 1000))
        except Exception:
            log.exception("board warm-up failed: board=%s", board.name)
        self._refresh = asyncio.get_running_loop().call_later(self.cache.ttl_s, self.start_warm)

    def start_warm(self) -> None:
        """`warm` in the background (at startup, and when the Explorer's entry expires)."""
        task = asyncio.get_running_loop().create_task(self.warm())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def stop(self) -> None:
        if self._refresh is not None:
            self._refresh.cancel()
        for task in [*self._tasks, *self._inflight.values()]:  # nothing outlives the pool
            task.cancel()


@dataclass
class BoardInstance:
    """A Board shown in a Conversation: its Results (under the Conversation's IDs), its surface,
    and its action sources, which don't count toward the Conversation's `MAX_ACTION_SOURCES`.
    Slice 2 adds its Controls' options and values, its own lock, and the `seq` bookkeeping."""

    id: str  # b<n>
    board: Board
    surface_id: str
    results: dict[str, StoredResult]  # by query name
    sources: dict[str, ActionSource] = field(default_factory=dict)  # by source ID v<n>
    level: Level | None = None
    failed: dict[str, str] = field(default_factory=dict)  # query name -> SQLSTATE, as displayed
    options: dict[str, Options] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)  # Control values of the last completed render
    defaults: dict[str, Any] = field(default_factory=dict)
    deps: dict[str, tuple[frozenset[str], frozenset[str]]] = field(default_factory=dict)  # panel -> deps
    panel_sources: dict[str, list[str]] = field(default_factory=dict)  # panel -> source IDs
    copies: dict[str, StoredResult] = field(default_factory=dict)  # Board Result ID -> its store copy
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    seq_seen: int = 0  # the highest `seq` that arrived (and passed validation)
    seq_applied: int = 0
    key: Any = None  # an Entity Board's bound key value (slice 3)
    title: str = ""  # the heading and tab title: "<title> · <row label>" for an Entity Board

    def __post_init__(self) -> None:
        self.title = self.title or self.board.title

    @property
    def at_defaults(self) -> bool:
        return self.state == self.defaults

    def result(self, result_id: str) -> StoredResult | None:
        return next((r for r in self.results.values() if r.id == result_id), None)

    def bound(self) -> dict[str, Any]:
        return bound_values(self.board, self.options, self.state)


class _Named:
    """A Board instance's Results looked up by query name, for the macros."""

    def __init__(self, results: Mapping[str, StoredResult]):
        self._results = results

    def get(self, key: str) -> StoredResult | None:
        return self._results.get(key)

    def ids(self) -> list[str]:
        return list(self._results)


def instance_results(outcomes: Mapping[str, Outcome], reserve: Callable[[], str]) -> tuple[dict[str, StoredResult], dict[str, str]]:
    """Fresh Result IDs per instance; cached rows are shared by reference. Returns the Results by
    query name and the failed queries' SQLSTATEs."""
    results: dict[str, StoredResult] = {}
    failed: dict[str, str] = {}
    for name, o in outcomes.items():
        if isinstance(o, StoredResult):
            results[name] = dataclasses.replace(o, id=reserve())
        else:
            failed[name] = o.sqlstate or ""
    return results, failed


def heading_components(board: Board, title: str = "") -> list[dict[str, Any]]:
    """The title (an Entity Board's with its row label), then the optional question and scope
    lines. Written as they are: the client renders h1–h5 and caption Text as plain text, so
    escaping would show backslashes."""
    texts = [("board_title", title or board.title, "h2")]
    if board.question:
        texts.append(("board_question", board.question, "caption"))
    if board.scope:
        texts.append(("board_scope", board.scope, "caption"))
    return [
        {"id": "board_heading", "component": "Column", "children": [t[0] for t in texts]},
        *({"id": cid, "component": "Text", "text": text, "variant": variant} for cid, text, variant in texts),
    ]


def build_board(board: Board, results: Mapping[str, StoredResult], failed: Mapping[str, str], pipeline: UiPipeline,
                surface_id: str, ids: IdAllocator | None = None, options: Mapping[str, Options] | None = None,
                state: Mapping[str, Any] | None = None, *, title: str = "") -> UiResult:
    """The Board's Surface: heading, then the Controls Card, then the layout (renamed
    `board_layout`), in Board mode, and the Controls' data model. Panel IDs are the layout's own,
    stable across renders (SPEC §18)."""
    from explorer_agent.ui import validate  # noqa: PLC0415 - see TYPE_CHECKING above

    options = {c.name: (options or {}).get(c.name) or Options() for c in board.controls}
    state = state if state is not None else ({} if not board.controls else default_state(board, options))
    bound = bound_values(board, options, state) if board.controls else {}
    controls = {c.name: (c.cast, bound[c.name]) for c in board.controls}
    board_args = {"failed": dict(failed), "controls": controls, "own_board": board.name}
    ui = pipeline.build(board.express, _Named(results), surface_id, ids, board=board_args)
    top = ["board_heading", *(["board_controls"] if board.controls else []), "board_layout"]
    for message in ui.messages:
        update = message.get("updateComponents")
        if update is None:
            continue
        for comp in update["components"]:
            if comp.get("id") == "root":
                comp["id"] = "board_layout"
        update["components"] = [
            {"id": "root", "component": "Column", "children": top},
            *heading_components(board, title),
            *(controls_components(board, options) if board.controls else []),
            *update["components"],
        ]
    if board.controls:
        ui.messages.append({"version": "v0.9", "updateDataModel": {
            "surfaceId": surface_id, "path": "/controls", "value": data_model(board, state)}})
    validate(ui.messages)
    return ui
