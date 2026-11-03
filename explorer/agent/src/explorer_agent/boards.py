"""Boards (SPEC §18, ADR 0028): views written with the Dataset and rendered without the model.

A Board file (`datasets/<name>/boards/<board>.toml`) holds SQL and an Express layout whose Macro
calls name queries where an answer names Result IDs. Rendering runs the queries, turns each
query's rows into a Result owned by the Board instance, and builds the layout in Board mode. Every
figure on a Board comes from the database when it renders (ADR 0001).

Slice 1a: no Controls and no keys (placeholders are a startup error), and Board Visuals aren't
clickable. Pure parts: loading and checking files. Async parts: the startup check, the cache
with single-flight, and the warm-up of the Explorer.
"""

from __future__ import annotations

import asyncio
import dataclasses
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

from explorer_agent.drill import DrillSqlError, normalise, rewrite, wrap
from explorer_agent.macros import check_columns
from explorer_agent.results import StoredResult
from explorer_agent.sql import GuardError, SqlFail, guard

if TYPE_CHECKING:  # ui imports prompt, which imports config, which imports this module
    from explorer_agent.actions import IdAllocator
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
KEYS = frozenset({"title", "question", "scope", "queries", "layout"})
NOT_YET = frozenset({"controls", "key", "key_type", "key_columns"})  # slices 2 and 3
BOARD_COLD_TARGET_S = 1.5  # SPEC §18 speed targets: any Board, cold

# Statement names in the Express layout (`name = ...` at the start of a line).
_STATEMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", re.MULTILINE)


class BoardError(ValueError):
    """A Board file problem. The message names the file and the key."""


@dataclass(frozen=True)
class BoardQuery:
    name: str
    sql: str  # normalised: no trailing `;`, ready to wrap


@dataclass(frozen=True)
class Board:
    name: str  # the file stem
    path: Path
    title: str
    question: str
    scope: str
    queries: tuple[BoardQuery, ...]
    layout: str  # Express statements, without <a2ui>

    @property
    def express(self) -> str:
        return f"<a2ui>\n{self.layout}\n</a2ui>"


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
    return {p.stem: load_board(p, allowed_schemas) for p in paths}


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
    if not QUERY_NAME.fullmatch(path.stem):
        raise BoardError(f"{path}: a Board file name must be lower case letters, digits and _")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise BoardError(f"{path} is not valid TOML: {e}") from None
    for key in sorted(data):
        if key in NOT_YET:
            raise BoardError(f"{path}: key {key} is not supported yet")
        if key not in KEYS:
            raise BoardError(f"{path}: unknown key {key}")
    title = _text(data, "title", path, required=True)
    if any(ch.isdigit() for ch in title):
        raise BoardError(f"{path}: title must be plain text with no numbers")
    queries = _queries(data, path, allowed_schemas)
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
    return Board(
        name=path.stem, path=path, title=title, question=_text(data, "question", path, required=False),
        scope=_text(data, "scope", path, required=False), queries=queries, layout=layout,
    )


def _queries(data: Mapping[str, Any], path: Path, allowed_schemas: tuple[str, ...]) -> tuple[BoardQuery, ...]:
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
        try:
            placeholders = rewrite(sql).names
        except DrillSqlError as e:
            raise BoardError(f"{path}: queries.{name}.sql: {str(e).removeprefix('drill_sql: ')}") from None
        if placeholders:
            raise BoardError(
                f"{path}: queries.{name}.sql: placeholder :{placeholders[0]} names no Control or key "
                "(Controls and keys are not supported yet)"
            )
        try:
            guard(sql, allowed_schemas)
        except GuardError as e:
            raise BoardError(f"{path}: queries.{name}.sql: {e}") from None
        out.append(BoardQuery(name=name, sql=normalise(sql)))
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


async def check_board(board: Board, runner: Any, pipeline: UiPipeline) -> Outputs:
    """Each query as `LIMIT 0` (no rows fetched), giving its output columns and types, then the
    layout's columns-only check. Raises BoardError."""
    outputs: Outputs = {}
    for q in board.queries:
        outcome = await runner.run(wrap(q.sql, CHECK_ALIAS, " LIMIT 0"))
        if isinstance(outcome, SqlFail):
            state = f" (SQLSTATE {outcome.sqlstate})" if outcome.sqlstate else ""
            raise BoardError(f"{board.path}: queries.{q.name}.sql fails{state}: {outcome.message}")
        outputs[q.name] = (outcome.columns, outcome.column_types)
    comps = layout_components(board, pipeline)
    problems = [f"{c.get('id')}: FollowUp is for answers, not Boards" for c in comps if c.get("component") == "FollowUp"]
    problems += check_columns(comps, outputs)
    if problems:
        raise BoardError(f"{board.path}: layout: {'; '.join(problems)}")
    return outputs


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


def default_key(board: Board) -> tuple[Any, ...]:
    return (board.name, None, ())  # no key value, no Controls (slice 1a)


class BoardRenderer:
    """Runs Board queries under an agent-wide semaphore, with the cache and single-flight."""

    def __init__(self, boards: Mapping[str, Board], runner: Any, *, concurrency: int, row_cap: int,
                 ttl_s: float, max_cache: int, clock: Callable[[], float] = time.monotonic):
        self.boards = dict(boards)
        self._runner = runner
        self._semaphore = asyncio.Semaphore(concurrency)
        self._row_cap = row_cap
        self.cache = BoardCache(ttl_s, max_cache, clock)
        self._inflight: dict[tuple[Any, ...], asyncio.Task[dict[str, Outcome]]] = {}
        self._refresh: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task[None]] = set()  # held, so the loop doesn't drop them

    async def outcomes(self, board: Board, *, fresh: bool = False) -> dict[str, Outcome]:
        """Each query's outcome. Concurrent calls for one key share one run, which runs in its own
        task: a caller that is cancelled (a client gone) stops waiting, but the run goes on for
        the others and still fills the cache."""
        key = default_key(board)
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.get_running_loop().create_task(self._render(board, key, fresh=fresh))
            self._inflight[key] = task
            task.add_done_callback(lambda t: self._done(key, t))
        return await asyncio.shield(task)

    def _done(self, key: tuple[Any, ...], task: asyncio.Task[dict[str, Outcome]]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled() and task.exception() is not None:  # retrieved, if every caller left
            log.warning("board render failed: key=%s", key[0])

    async def _render(self, board: Board, key: tuple[Any, ...], *, fresh: bool) -> dict[str, Outcome]:
        cached = {} if fresh else self.cache.get(key)
        todo = [q for q in board.queries if q.name not in cached]
        ran = await asyncio.gather(*(self._run(board, q) for q in todo))
        out: dict[str, Outcome] = {q.name: cached[q.name] for q in board.queries if q.name in cached}
        out.update({q.name: o for q, o in zip(todo, ran, strict=True)})
        if todo:
            ran_ok = {q.name: o for q, o in zip(todo, ran, strict=True) if isinstance(o, StoredResult)}
            self.cache.put(key, ran_ok, dict(cached), pin=board.name == EXPLORER)
        return {q.name: out[q.name] for q in board.queries}

    async def _run(self, board: Board, q: BoardQuery) -> Outcome:
        async with self._semaphore:
            start = time.perf_counter()
            outcome = await self._runner.run(wrap(q.sql, BOARD_ALIAS, f" LIMIT {self._row_cap + 1}"))
            ms = round((time.perf_counter() - start) * 1000)
        if isinstance(outcome, SqlFail):
            log.warning("board query failed: board=%s query=%s sqlstate=%s ms=%d", board.name, q.name, outcome.sqlstate, ms)
            return outcome
        truncated = outcome.truncated or len(outcome.rows) > self._row_cap
        log.info("board query: board=%s query=%s rows=%d ms=%d", board.name, q.name, min(len(outcome.rows), self._row_cap), ms)
        return StoredResult(
            id="", sql=q.sql, purpose=f"{board.title} · {q.name}", columns=outcome.columns,
            column_types=outcome.column_types, rows=outcome.rows[: self._row_cap], truncated=truncated,
        )

    async def warm(self) -> None:
        """Renders the Explorer at its defaults, then again each time its entry would expire."""
        board = self.boards.get(EXPLORER)
        if board is None:
            return
        start = time.perf_counter()
        try:
            await self.outcomes(board, fresh=True)
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
    """A Board shown in a Conversation: its Results (under the Conversation's IDs) and surface."""

    id: str  # b<n>
    board: Board
    surface_id: str
    results: dict[str, StoredResult]  # by query name


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


def heading_components(board: Board) -> list[dict[str, Any]]:
    """The title, then the optional question and scope lines. Written as they are: the client
    renders h1–h5 and caption Text as plain text, so escaping would show backslashes."""
    texts = [("board_title", board.title, "h2")]
    if board.question:
        texts.append(("board_question", board.question, "caption"))
    if board.scope:
        texts.append(("board_scope", board.scope, "caption"))
    return [
        {"id": "board_heading", "component": "Column", "children": [t[0] for t in texts]},
        *({"id": cid, "component": "Text", "text": text, "variant": variant} for cid, text, variant in texts),
    ]


def build_board(board: Board, results: Mapping[str, StoredResult], failed: Mapping[str, str], pipeline: UiPipeline,
                surface_id: str, ids: IdAllocator | None = None) -> UiResult:
    """The Board's Surface: heading, then the layout (renamed `board_layout`), in Board mode with
    clicks off (slice 1a). Panel IDs are the layout's own, stable across renders (SPEC §18)."""
    from explorer_agent.ui import validate  # noqa: PLC0415 - see TYPE_CHECKING above

    ui = pipeline.build(board.express, _Named(results), surface_id, ids, board={"failed": dict(failed), "clicks": False})
    for message in ui.messages:
        update = message.get("updateComponents")
        if update is None:
            continue
        for comp in update["components"]:
            if comp.get("id") == "root":
                comp["id"] = "board_layout"
        update["components"] = [
            {"id": "root", "component": "Column", "children": ["board_heading", "board_layout"]},
            *heading_components(board),
            *update["components"],
        ]
    validate(ui.messages)
    return ui
