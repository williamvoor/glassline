"""Actions (SPEC §15): action sources and Follow-ups per Conversation, Turns and Levels,
validating what the client sends, Ask texts, synthetic tool results and `Viewing` lines.

The client sends references only (surface, source, row and Follow-up IDs). Values, labels and
request texts are looked up here, kept from when the Visual was built. Database text reaches
the model only inside tool results: Ask texts and `Viewing` lines carry IDs and numbers only.
"""

from __future__ import annotations

import json
from collections import OrderedDict, deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from explorer_agent.drill import DrillSpec
from explorer_agent.results import ResultStore, StoredResult, to_json_safe

MAX_ACTION_SOURCES = 50
MAX_PENDING_PAIRS = 5
OVERVIEW_LABEL = "Overview"
ASK_DRILL_LABEL = "Explanation"
REQUIRED_FIELDS = ("name", "surfaceId", "sourceComponentId", "timestamp", "context")
ACTION_NAMES = ("drill", "ask")
TOO_OLD = "This view is too old to explore; ask the question again."
ROWS_PREVIEW_TEXT_LIMIT = 500

ASK_ROW_TEXT = (
    "Ask: the user clicked a row of a Visual in Turn {turn} (Result {result_id}, row {row}). Show the "
    "next level of detail for that row, keeping the same filters and period."
)
ASK_DRILL_TEXT = "Ask: explain the Drill-down in Turn {turn}, Level {level} (Result {result_id})."


class ActionError(ValueError):
    """The action is rejected: no model call, no query. The message is shown to the user."""


# --- what `UiPipeline.build` reports (nothing is registered yet) -----------------------------


@dataclass(frozen=True)
class SourceSpec:
    id: str  # reserved v<n>
    visual: int  # macro call order of the Visual on the Surface
    result: StoredResult  # a reference, not a copy: keeps the rows alive after store eviction
    title: str
    rows: tuple[int, ...]  # clickable row indices, in displayed order
    labels: dict[int, str]
    # Entity Boards (SPEC §18 slice 3): the rows whose click opens `board`, keyed by `key_column`.
    boards: tuple[int, ...] = ()
    board: str = ""
    key_column: str = ""


@dataclass(frozen=True)
class FollowUpSpec:
    id: str  # reserved f<n>
    label: str
    request: str


class IdAllocator:
    """Reserves `v<n>` and `f<n>` while macros run. A failed attempt's IDs are skipped, never reused."""

    def __init__(self) -> None:
        self._v = 0
        self._f = 0

    def source(self) -> str:
        self._v += 1
        return f"v{self._v}"

    def follow_up(self) -> str:
        self._f += 1
        return f"f{self._f}"

    def issued_source(self, sid: str) -> bool:
        return _number(sid, "v") is not None and 0 < _number(sid, "v") <= self._v  # type: ignore[operator]

    def issued_follow_up(self, fid: str) -> bool:
        return _number(fid, "f") is not None and 0 < _number(fid, "f") <= self._f  # type: ignore[operator]


def _number(value: str, prefix: str) -> int | None:
    if isinstance(value, str) and value.startswith(prefix) and value[1:].isdigit():
        return int(value[1:])
    return None


# --- registered state ------------------------------------------------------------------------


@dataclass(frozen=True)
class Level:
    turn: int
    level: int
    surface_id: str
    label: str


@dataclass
class ActionSource:
    spec: SourceSpec
    level: Level
    drill: DrillSpec | None = None
    is_drill_down: bool = False  # the Visual of a Drill-down Level: it has an Ask button

    @property
    def id(self) -> str:
        return self.spec.id


@dataclass(frozen=True)
class FollowUp:
    spec: FollowUpSpec
    level: Level


@dataclass(frozen=True)
class Resolved:
    """A validated action."""

    name: str  # drill | ask
    kind: str  # drill | board | row | drill_down | follow_up
    level: Level  # where it was clicked
    source: ActionSource | None = None
    row: int | None = None
    follow_up: FollowUp | None = None

    @property
    def label(self) -> str:
        if self.follow_up is not None:
            return self.follow_up.spec.label
        if self.kind == "drill_down":
            return ASK_DRILL_LABEL
        assert self.source is not None and self.row is not None
        return self.source.spec.labels[self.row]


@dataclass
class ActionState:
    tag: str
    max_sources: int = MAX_ACTION_SOURCES
    max_pending: int = MAX_PENDING_PAIRS
    ids: IdAllocator = field(default_factory=IdAllocator)
    sources: OrderedDict[str, ActionSource] = field(default_factory=OrderedDict)
    follow_ups: OrderedDict[str, FollowUp] = field(default_factory=OrderedDict)
    surfaces: dict[str, Level] = field(default_factory=dict)
    turns: int = 0
    levels: dict[int, int] = field(default_factory=dict)  # highest Level number per Turn
    pending: deque[list[dict[str, Any]]] = field(default_factory=deque)  # synthetic pairs
    board_pair: list[dict[str, Any]] | None = None  # at most one, outside max_pending (SPEC §18)
    # Tabs (SPEC §18 slice 1b): t<n> -> its Level, issued when a reply adds a Level, never reused.
    tabs: dict[str, Level] = field(default_factory=dict)
    board_tabs: set[str] = field(default_factory=set)  # tabs showing a Board
    latest_tab: str | None = None

    # Turns and Levels ------------------------------------------------------------------------

    def new_turn(self) -> Level:
        self.turns += 1
        return self._level(self.turns, OVERVIEW_LABEL)

    def new_level(self, turn: int, label: str) -> Level:
        return self._level(turn, label)

    def _level(self, turn: int, label: str) -> Level:
        n = self.levels.get(turn, 0) + 1
        self.levels[turn] = n
        level = Level(turn=turn, level=n, surface_id=f"turn-{turn}-{n}-{self.tag}", label=label)
        self.surfaces[level.surface_id] = level
        return level

    def new_tab(self, level: Level, *, board: bool = False) -> str:
        """The tab ID of a Level a reply is about to add (SPEC §18): `t<n>`, never reused."""
        tab = f"t{len(self.tabs) + 1}"
        self.tabs[tab] = level
        if board:
            self.board_tabs.add(tab)
        self.latest_tab = tab
        return tab

    # registration ----------------------------------------------------------------------------

    @staticmethod
    def board_sources(level: Level, sources: list[SourceSpec], drills: list[DrillSpec]) -> dict[str, ActionSource]:
        """A Board's action sources, kept by its Board instance: not in `sources`, so not counted
        toward `max_sources` (SPEC §18)."""
        by_source = {d.source: d for d in drills}
        return {s.id: ActionSource(spec=s, level=level, drill=by_source.get(s.id)) for s in sources}

    def register(self, level: Level, sources: list[SourceSpec], drills: list[DrillSpec],
                 follow_ups: list[FollowUpSpec], *, drill_down: bool = False) -> list[ActionSource]:
        """Called once a reply has passed every check. Returns the registered sources."""
        by_source = {d.source: d for d in drills}
        out = []
        for spec in sources:
            src = ActionSource(spec=spec, level=level, drill=by_source.get(spec.id), is_drill_down=drill_down)
            self.sources[spec.id] = src
            out.append(src)
        while len(self.sources) > self.max_sources:
            self.sources.popitem(last=False)
        for fu in follow_ups:
            self.follow_ups[fu.id] = FollowUp(spec=fu, level=level)
        while len(self.follow_ups) > self.max_sources:
            self.follow_ups.popitem(last=False)
        return out

    # validation ------------------------------------------------------------------------------

    def resolve(self, action: Any, board_sources: Mapping[str, ActionSource] | None = None) -> Resolved:
        """Validate an action. Raises ActionError and changes nothing when it's invalid.
        `board_sources`: the live Board instances' sources, looked up after the Conversation's."""
        if not isinstance(action, dict):
            raise ActionError("The action is malformed.")
        missing = [f for f in REQUIRED_FIELDS if f not in action]
        if missing:
            raise ActionError(f"The action is missing {', '.join(missing)}.")
        name, context = action["name"], action["context"]
        if name not in ACTION_NAMES:
            raise ActionError("Unknown action.")
        if not isinstance(context, dict):
            raise ActionError("The action is malformed.")
        level = self.surfaces.get(action["surfaceId"]) if isinstance(action["surfaceId"], str) else None
        if level is None:
            raise ActionError("This view is not part of this conversation.")

        if "follow_up" in context:
            if name != "ask" or set(context) != {"follow_up"}:
                raise ActionError("The action is malformed.")
            fid = context["follow_up"]
            fu = self.follow_ups.get(fid) if isinstance(fid, str) else None
            if fu is None:
                raise ActionError(TOO_OLD if isinstance(fid, str) and self.ids.issued_follow_up(fid) else "Unknown follow-up.")
            if fu.level != level:
                raise ActionError("The action doesn't match its view.")
            return Resolved(name=name, kind="follow_up", level=level, follow_up=fu)

        sid = context.get("source")
        if not isinstance(sid, str) or set(context) - {"source", "row"}:
            raise ActionError("The action is malformed.")
        src = self.sources.get(sid) or (board_sources or {}).get(sid)
        if src is None:
            raise ActionError(TOO_OLD if self.ids.issued_source(sid) else "Unknown view element.")
        if src.level != level:
            raise ActionError("The action doesn't match its view.")
        if "row" not in context:
            # Only a Drill-down's own Visual has an Ask without a row (its Ask button).
            if name != "ask" or not src.is_drill_down:
                raise ActionError("The action needs a row.")
            return Resolved(name=name, kind="drill_down", level=level, source=src)
        row = context["row"]
        if isinstance(row, bool) or not isinstance(row, int) or row not in src.spec.labels:
            raise ActionError("That row can't be explored.")
        if name == "drill" and row in src.spec.boards:
            return Resolved(name=name, kind="board", level=level, source=src, row=row)  # SPEC §18 slice 3
        if name == "drill":
            if src.drill is None:
                raise ActionError("This view has no drill-down; ask the agent instead.")
            return Resolved(name=name, kind="drill", level=level, source=src, row=row)
        return Resolved(name=name, kind="row", level=level, source=src, row=row)

    # model-facing text -------------------------------------------------------------------------

    def viewing_lines(self, tab: Any) -> list[str]:
        """`Viewing in Turn <t>: Level <l>` (§15's form) for the selected tab, when it isn't the
        newest tab. Never for a Board tab: the model hasn't seen it (SPEC §18 slice 1b)."""
        level = self.tabs.get(tab) if isinstance(tab, str) else None
        if level is None or tab == self.latest_tab or tab in self.board_tabs:
            return []
        return [f"Viewing in Turn {level.turn}: Level {level.level}"]

    def queue_pair(self, pair: list[dict[str, Any]]) -> None:
        self.pending.append(pair)
        while len(self.pending) > self.max_pending:
            self.pending.popleft()

    def take_pairs(self) -> list[dict[str, Any]]:
        """The queued pairs, then the Board pair (it describes what is on screen now)."""
        out = [m for pair in self.pending for m in pair] + (self.board_pair or [])
        self.pending.clear()
        self.board_pair = None
        return out


def ask_text(resolved: Resolved, result_id: str) -> str:
    """The user text of an Ask model run. IDs and numbers only, never labels or values."""
    if resolved.kind == "follow_up":
        assert resolved.follow_up is not None
        return resolved.follow_up.spec.request
    if resolved.kind == "drill_down":
        return ASK_DRILL_TEXT.format(turn=resolved.level.turn, level=resolved.level.level, result_id=result_id)
    return ASK_ROW_TEXT.format(turn=resolved.level.turn, result_id=result_id, row=resolved.row)


def _cut(value: Any) -> Any:
    if isinstance(value, str) and len(value) > ROWS_PREVIEW_TEXT_LIMIT:
        return value[:ROWS_PREVIEW_TEXT_LIMIT] + "…"
    if isinstance(value, dict):
        return {k: _cut(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_cut(v) for v in value]
    return value


def synthetic_pair(call_id: str, sql: str, purpose: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    """An assistant `run_sql` call the model didn't write, and its tool result. No query runs."""
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": "run_sql", "arguments": json.dumps({"sql": sql, "purpose": purpose}, ensure_ascii=False)},
            }],
        },
        {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, ensure_ascii=False)},
    ]


def drill_down_pair(level: Level, result: StoredResult, label: str, rows_to_model: int) -> list[dict[str, Any]]:
    return synthetic_pair(
        f"synthetic-{level.turn}-{level.level}",
        result.sql,
        f"Drill-down shown in Turn {level.turn}, Level {level.level}",
        {
            "result_id": result.id,
            "columns": result.columns,
            "column_types": result.column_types,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "rows_preview": [_cut(r) for r in result.rows[:rows_to_model]],
            "clicked": label,
        },
    )


def row_pair(new_level: Level, clicked: Level, result: StoredResult, row: int) -> list[dict[str, Any]]:
    return synthetic_pair(
        f"synthetic-{new_level.turn}-{new_level.level}",
        result.sql,
        f"Row clicked in Turn {clicked.turn}",
        {
            "result_id": result.id,
            "columns": result.columns,
            "column_types": result.column_types,
            "row_index": row,
            "rows_preview": [_cut(result.rows[row])],
        },
    )


def restore_result(store: ResultStore, result: StoredResult) -> StoredResult:
    """The source's Result as stored now: itself, or, if the store evicted it, a copy put back
    under a fresh Result ID."""
    if store.get(result.id) is result:
        return result
    return store.readd(result)


BOARD_PAIR_PURPOSE = "Board shown in Tab {tab}"
BOARD_SQL_JOIN = ";\n\n"


def board_pair(call_id: str, tab: str, board_title: str, controls: Mapping[str, str], results: list[StoredResult],
               rows: int, max_chars: int) -> list[dict[str, Any]]:
    """The Board pair (SPEC §18): what the viewed Board shows, so a question can use its state.
    The call holds the display copies of its queries and no titles or labels; the result holds
    the title, the Controls' shown values, and a few rows per Result, cut to `max_chars` by
    shortening the previews first, then leaving out the last Results."""
    sql = BOARD_SQL_JOIN.join(r.sql for r in results)
    purpose = BOARD_PAIR_PURPOSE.format(tab=tab)

    def body(n: int, keep: int) -> dict[str, Any]:
        out: dict[str, Any] = {
            "board": board_title,
            "controls": dict(controls),
            "results": [
                {"id": r.id, "purpose": r.purpose, "columns": r.columns,
                 "rows_preview": [_cut(to_json_safe(row)) for row in r.rows[:n]]}
                for r in results[:keep]
            ],
        }
        if keep < len(results):
            out["omitted_results"] = len(results) - keep
        return out

    def size(b: dict[str, Any]) -> int:
        return len(json.dumps(b, ensure_ascii=False))

    chosen = None
    for keep in range(len(results), -1, -1):
        for n in range(rows, -1, -1):
            if size(body(n, keep)) <= max_chars:
                chosen = body(n, keep)
                break
        if chosen is not None:
            break
    return synthetic_pair(call_id, sql, purpose, chosen if chosen is not None else body(0, 0))
