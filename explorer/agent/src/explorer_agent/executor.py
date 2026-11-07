"""A2A executor: one task per user message (parent spec §2, SPEC §15).

A message is a typed question or an Action. A typed question starts a Turn; an Action adds a
Level to the Turn it came from. A question or an Ask is a model run: the `Answerer` (the tool
loop in `llm.py`, or the stub server's canned one) returns a finished `UiResult` and the
executor only sends it. A Drill-down runs its Drill query here, with no model call.
"""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import functools
import hashlib
import logging
import secrets
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.id_generator import IDGeneratorContext, UUIDGenerator
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentCard, Part, TaskState, TextPart
from a2a.utils import new_task
from a2ui.a2a import create_a2ui_part, get_a2ui_datapart, try_activate_a2ui_extension
from a2ui.constants import A2UI_CLIENT_CAPABILITIES_KEY

from explorer_agent import trace
from explorer_agent.actions import (
    MAX_ACTION_SOURCES,
    MAX_PENDING_PAIRS,
    TOO_OLD,
    ActionError,
    ActionSource,
    ActionState,
    Level,
    Resolved,
    ask_text,
    board_pair,
    drill_down_pair,
    restore_result,
    row_pair,
)
from explorer_agent.budget import TurnBudget
from explorer_agent.boards import (
    EXPLORER,
    BoardInstance,
    BoardRenderer,
    ControlError,
    OptionsError,
    bound_values,
    build_board,
    changed_panels,
    instance_results,
    layout_components,
    log_text,
    panel_components,
    panel_dependencies,
    panel_sources,
    shown_values,
    validate_values,
)
from explorer_agent.catalog import EXPLORER_CATALOG_ID
from explorer_agent.drill import DrillSpec, Prefetcher, display_sql
from explorer_agent.macros import RERUN_FAILED, key_value
from explorer_agent.results import ResultStore, ResultView, StoredResult, to_json_safe
from explorer_agent.saved import (
    STATE_VERSION,
    Loaded,
    SavedResult,
    SavedStore,
    Snapshot,
    omit_rows,
    result_ids_in,
    row_hash,
    title_of,
)
from explorer_agent.sql import SqlFail
from explorer_agent.ui import UiError, UiPipeline, UiResult

log = logging.getLogger(__name__)

UI_ERROR_PREFIX = "I couldn't build the view for this answer:"
UNKNOWN_CONTEXT_PROSE = "Earlier results from this conversation are no longer available."
MAX_CONVERSATIONS = 100
GENERIC_ERROR = "Something went wrong while answering. Please try again."
BUSY_ERROR = "The agent is busy answering; try again when it has finished."
DRILL_FAILED = "The drill-down query failed ({reason}). You can retry, or ask the agent instead."
NO_DRILLS = "Drill-downs aren't available here; ask the agent instead."
LEVEL_KEY = "explorerLevel"  # final message metadata: the Level this answer is
VIEWING_KEY = "explorerViewing"  # request metadata: the selected tab's ID (SPEC §18)
TAB_KEY = "explorerTab"  # final message metadata: the tab this Level opens, {id, kind, title}
LOG_KEY = "explorerLogEntry"  # final message metadata: its investigation log entry, {kind, tag, text}
NEW_TAB = "NEW TAB"
NO_MODEL = "NO MODEL"
MODEL_KEY = "explorerModel"  # final message metadata: the serving model's name, null for a Drill-down (SPEC §16)
ACTION_FAILED_KEY = "explorerActionFailed"  # final message metadata: the Action failed, no Level was added
NO_BOARDS_KEY = "explorerNoBoards"  # final message metadata: open_board in a Dataset without Boards (SPEC §18)
OPEN_BOARD = "open_board"
CONTROLS = "controls"
BOARD_KEY = "explorerBoard"  # final message metadata on a Board's tab: {id, controls: [{name, kind}]}
IN_PLACE_KEY = "explorerInPlace"  # final message metadata: a re-query replaced panels, {board, seq}
SUPERSEDED_KEY = "explorerSuperseded"  # final message metadata: a newer Control change won; ignore
IN_PLACE = "IN PLACE"
CONTROLS_LOG_KIND = "controls"
CONTROLS_KEYS = {"board", "seq", "values"}
BOARD_UPDATE_FAILED = "The Board couldn't be updated. Try again."
NO_BOARDS = "This dataset has no Boards."
BOARD_OPEN = "The Explorer is already open in this conversation."
UNKNOWN_BOARD = "Unknown board."
BOARD_BUILD_FAILED = "The Explorer couldn't be shown. You can still ask a question."
ENTITY_FAILED = "The Board couldn't be shown. You can retry, or ask the agent instead."
KEYED_BOARD = "This Board opens from a row."
MAX_BOARD_INSTANCES = 10
TRACE_KEY = "explorerTrace"  # request metadata: true records a Trace; final metadata: {level} (specs/trace)
TRACE_EVENT_KEY = "explorerTraceEvent"  # status-update metadata: one Trace event, live
VIEW_KEY = "explorerView"  # request metadata: {closed: [tab IDs], selected: tab ID} (Workspaces B12)
GAVE_UP = "This answer couldn't be completed when it was asked."
REBUILD_FAILED = "This answer couldn't be shown again."
MAX_VIEW_TABS = 1000


def _drill_view(spec: DrillSpec) -> dict[str, Any]:
    return {"sql": spec.sql, "names": list(spec.names), "drill_as": spec.drill_as, "drill_x": spec.drill_x,
            "drill_y": spec.drill_y, "title": spec.title}


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")


def chosen_values(board: Any, options: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, list[Any]]:
    """The option values a choice or multi Control's indexes name (refuter F5): a saved index is
    only meaningful against the options it was chosen from."""
    out: dict[str, list[Any]] = {}
    for c in board.controls:
        if c.kind not in ("choice", "multi") or c.name not in options:
            continue
        values = options[c.name].values
        v = state.get(c.name)
        picked = [] if v is None else [v] if not isinstance(v, list) else v
        out[c.name] = [to_json_safe(values[i]) if isinstance(i, int) and 0 <= i < len(values) else None for i in picked]
    return out


def level_id(level: Level) -> str:
    return f"{level.turn}-{level.level}"


@dataclass
class Conversation:
    id: str
    store: ResultStore = field(default_factory=ResultStore)
    surface_id: str = ""  # the surface of the Level being answered, set by the executor
    # Random per Conversation, so surface IDs don't repeat across agent restarts: the client keeps
    # its contextId and surfaces, and rejects a createSurface for an ID it already holds.
    surface_tag: str = field(default_factory=lambda: secrets.token_hex(3))
    history: list[dict[str, Any]] = field(default_factory=list)  # messages for the model, stored in full
    run_starts: list[int] = field(default_factory=list)  # history index where each model run begins
    prompt_tokens: int = 0
    cached_tokens: int = 0
    unknown_context: bool = False  # created for a contextId we don't hold (restart or eviction)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)  # one message at a time
    model_runs_waiting: int = 0  # model runs holding or awaiting the lock
    max_action_sources: int = MAX_ACTION_SOURCES
    max_pending_pairs: int = MAX_PENDING_PAIRS
    actions: ActionState = field(init=False, repr=False)
    boards: OrderedDict[str, BoardInstance] = field(default_factory=OrderedDict)  # by instance ID b<n>
    board_opened: bool = False  # open_board is allowed once per Conversation (SPEC §18)
    max_board_instances: int = MAX_BOARD_INSTANCES
    board_counter: int = 0  # Board instance IDs b<n>, never reused (slice 3)
    dropped_boards: set[str] = field(default_factory=set)  # beyond max_board_instances: "too old"
    pinned: dict[str, StoredResult] = field(default_factory=dict)  # the Board pair's Results, for its model run
    board_rows_to_model: int = 5
    board_pair_max_chars: int = 6000
    # Saving (Workspaces phase 1). `persist` turns on with the first typed question or Action
    # other than open_board (B13); until then nothing is written.
    persist: bool = False
    title: str = ""
    created_at: str = field(default_factory=_now)
    records: dict[str, dict[str, Any]] = field(default_factory=dict)  # Level ID -> what it was built from (B7)
    log: list[dict[str, Any]] = field(default_factory=list)  # Log entries in order: tab and in-place
    view: dict[str, Any] = field(default_factory=lambda: {"closed": [], "selected": None})
    board_levels: dict[str, str] = field(default_factory=dict)  # Level ID -> its live Board instance ID
    saved_results: dict[str, SavedResult] = field(default_factory=dict)  # as loaded from the store
    written: set[str] = field(default_factory=set)  # Result IDs already in the store
    reruns: dict[str, tuple[bool, tuple[str, str] | None]] = field(default_factory=dict)  # ID -> (changed, failure)
    restore_results: Callable[[list[str]], Awaitable[None]] | None = field(default=None, repr=False)  # B9
    unsaved: list[StoredResult] = field(default_factory=list)  # named by a reply, outside the store (pinned)
    deleted: bool = False  # deleted from the store: never written again
    trace_prompts: dict[str, str] = field(default_factory=dict)  # system prompts of saved Traces, by hash

    def __post_init__(self) -> None:
        self.actions = ActionState(self.surface_tag, self.max_action_sources, self.max_pending_pairs)

    @property
    def results(self) -> ResultView:
        """`r<id>` lookups: the store, then the live Board instances, then pinned (SPEC §18)."""
        return ResultView(
            self.store,
            lambda: [r for b in self.boards.values() for r in b.results.values()],
            lambda: list(self.pinned.values()),
        )

    def board_of_tab(self, tab: Any) -> BoardInstance | None:
        """The Board instance a Board tab shows."""
        if not isinstance(tab, str) or tab not in self.actions.board_tabs:
            return None
        level = self.actions.tabs[tab]
        return next((b for b in self.boards.values() if b.surface_id == level.surface_id), None)

    def board_of_source(self, source_id: str) -> BoardInstance | None:
        return next((b for b in self.boards.values() if source_id in b.sources), None)

    @property
    def busy(self) -> bool:
        """A model run holds or awaits the lock (`lock.locked()` alone misses a woken waiter)."""
        return self.model_runs_waiting > 0


class _IssuedIds(UUIDGenerator):
    """Context ID generator that remembers what it issued, so a new conversation (we made the
    ID) can be told apart from an unknown one (the client sent an ID we don't hold)."""

    MAX_PENDING = 1000  # IDs issued but not yet used (a request can fail before it gets there)

    def __init__(self) -> None:
        self.issued: OrderedDict[str, None] = OrderedDict()

    def generate(self, context: IDGeneratorContext) -> str:
        value = super().generate(context)
        self.issued[value] = None
        while len(self.issued) > self.MAX_PENDING:
            self.issued.popitem(last=False)
        return value


class Conversations:
    """Conversations by A2A context ID, least recently used evicted beyond the cap. With a
    `saved` store (Workspace mode) memory is a cache over it: an unknown or evicted ID is loaded
    from the store (B14)."""

    def __init__(self, max_conversations: int = MAX_CONVERSATIONS, *, max_action_sources: int = MAX_ACTION_SOURCES,
                 max_pending_pairs: int = MAX_PENDING_PAIRS, board_rows_to_model: int = 5,
                 board_pair_max_chars: int = 6000, max_board_instances: int = MAX_BOARD_INSTANCES,
                 saved: SavedStore | None = None) -> None:
        self._max = max_conversations
        self._by_id: OrderedDict[str, Conversation] = OrderedDict()
        self.id_generator = _IssuedIds()
        self._limits = {"max_action_sources": max_action_sources, "max_pending_pairs": max_pending_pairs,
                        "board_rows_to_model": board_rows_to_model, "board_pair_max_chars": board_pair_max_chars,
                        "max_board_instances": max_board_instances}
        self.on_evict: Callable[[str], None] | None = None
        self.on_load: Callable[[Conversation], None] | None = None  # set by the executor
        self.saved = saved
        self._loading: dict[str, asyncio.Future[Conversation | None]] = {}

    def get_or_create(self, context_id: str) -> Conversation:
        conv = self._by_id.get(context_id)
        if conv is not None:
            self._by_id.move_to_end(context_id)
            return conv
        conv = Conversation(context_id, **self._limits)
        if context_id in self.id_generator.issued:
            del self.id_generator.issued[context_id]
        else:
            conv.unknown_context = True
        self._insert(conv)
        return conv

    def _insert(self, conv: Conversation) -> None:
        self._by_id[conv.id] = conv
        while len(self._by_id) > self._max:
            old, _ = self._by_id.popitem(last=False)
            if self.on_evict is not None:
                self.on_evict(old)

    def held(self, context_id: str) -> Conversation | None:
        return self._by_id.get(context_id)

    async def load(self, context_id: str) -> Conversation | None:
        """The Conversation in memory, or loaded from the store; None when neither holds it."""
        conv = self._by_id.get(context_id)
        if conv is not None:
            self._by_id.move_to_end(context_id)
            return conv
        if self.saved is None or context_id in self.id_generator.issued:
            return None
        waiting = self._loading.get(context_id)
        if waiting is not None:
            return await asyncio.shield(waiting)
        future: asyncio.Future[Conversation | None] = asyncio.get_running_loop().create_future()
        self._loading[context_id] = future
        try:
            loaded = await self.saved.load(context_id)
            conv = self._by_id.get(context_id)  # created meanwhile: keep that one
            if conv is None and loaded is not None:
                try:
                    conv = restore(loaded, self._limits)
                except (KeyError, TypeError, ValueError, AttributeError, IndexError):
                    log.warning("saved conversation could not be read; treated as unknown")
                    conv = None
                    future.set_result(None)
                    return None
                if self.on_load is not None:
                    self.on_load(conv)
                self._insert(conv)
                log.info("conversation loaded from the store: levels=%d", len(conv.records))
            future.set_result(conv)
            return conv
        except BaseException as e:
            future.set_exception(e)
            future.exception()  # retrieved: no "never retrieved" warning when nobody waits
            raise
        finally:
            del self._loading[context_id]

    async def get_or_load(self, context_id: str) -> Conversation:
        return await self.load(context_id) or self.get_or_create(context_id)

    def forget(self, context_id: str) -> None:
        """Drops a deleted Conversation from memory; an in-flight reply won't save it again."""
        conv = self._by_id.pop(context_id, None)
        if conv is not None:
            conv.deleted = True
            if self.on_evict is not None:
                self.on_evict(context_id)

    def forget_all(self) -> None:
        for cid in list(self._by_id):
            self.forget(cid)

    @property
    def running(self) -> bool:
        return any(c.busy for c in self._by_id.values())


def restore(loaded: Loaded, limits: Mapping[str, Any]) -> Conversation:
    """A Conversation as saved: history, counters, Tabs, Levels and the surface map, with no
    Results and no action sources (they come back as each Level is rebuilt, B14)."""
    st = loaded.state
    if st.get("version") != STATE_VERSION:
        raise ValueError("unknown saved state version")
    conv = Conversation(loaded.id, surface_tag=st["surface_tag"], **limits)
    conv.history = list(st["history"])
    conv.run_starts = list(st["run_starts"])
    conv.persist = True
    conv.title, conv.created_at = loaded.title, loaded.created_at
    conv.records = dict(st["records"])
    conv.log = list(st["log"])
    conv.view = dict(st["view"])
    conv.trace_prompts = dict(st.get("trace_prompts") or {})
    conv.board_counter = st["board_counter"]
    conv.board_opened = st["board_opened"]
    conv.store.next_number = st["store_next"]
    conv.saved_results = dict(loaded.results)
    conv.written = set(loaded.results)
    a = conv.actions
    a.turns = st["turns"]
    a.levels = {int(k): v for k, v in st["levels"].items()}
    a.ids._v, a.ids._f = st["ids"]
    for rec in conv.records.values():
        level = Level(rec["turn"], rec["level"], rec["surface_id"], rec["label"])
        a.surfaces[level.surface_id] = level
        a.tabs[rec["tab"]["id"]] = level
        if rec["kind"] == "board":
            a.board_tabs.add(rec["tab"]["id"])
    # Tab IDs are t<n> in issue order; a Level that failed after its tab was issued has none.
    a.tabs = dict(sorted(a.tabs.items(), key=lambda kv: int(kv[0][1:])))
    a.latest_tab = st["latest_tab"]
    return conv


StatusFn = Callable[[str], Awaitable[None]]
Answerer = Callable[[Conversation, str, StatusFn], Awaitable[UiResult]]


@dataclass(frozen=True)
class Tab:
    """What a reply's tab and log entry say (SPEC §18): kind, plain-text title and log text."""

    kind: str  # board | question | drill | ask
    title: str
    text: str


@dataclass
class DrillDowns:
    """What a Drill-down needs: the SQL runner (`run_bound`), the UI pipeline, and prefetch."""

    runner: Any
    pipeline: UiPipeline
    prefetcher: Prefetcher | None = None
    rows_to_model: int = 20


@dataclass
class BoardViews:
    """What a Board needs: the renderer (queries, cache) and the UI pipeline (SPEC §18)."""

    renderer: BoardRenderer
    pipeline: UiPipeline
    prefetch_rows: int = 1  # BOARD_PREFETCH_ROWS: Entity Boards prefetched per clickable Visual
    hashes: dict[str, str] = field(default_factory=dict)  # Board name -> SHA-256 of its file, at load

    def __post_init__(self) -> None:
        if not self.hashes:
            self.hashes = {name: board_hash(b) for name, b in self.renderer.boards.items()}


def board_hash(board: Any) -> str:
    try:
        return hashlib.sha256(board.path.read_bytes()).hexdigest()
    except OSError:
        return ""


@dataclass
class SavedViews:
    """What saving and rebuilding need (Workspaces phase 1): the store, the SQL runner for
    re-runs, and the UI pipeline that compiles a saved reply again."""

    store: SavedStore
    runner: Any
    pipeline: UiPipeline


def client_supports_catalog(metadata: dict[str, Any] | None) -> bool:
    try:
        ids = metadata[A2UI_CLIENT_CAPABILITIES_KEY]["v0.9"]["supportedCatalogIds"]  # type: ignore[index]
    except (KeyError, TypeError):
        return False
    return isinstance(ids, list) and EXPLORER_CATALOG_ID in ids


_NOT_AN_ACTION = object()


def action_of(message: Any) -> Any:
    """The A2UI action in a message, `_NOT_AN_ACTION` for a typed question, or None when an
    A2UI data part is there but holds no action."""
    for part in (message.parts if message else None) or ():
        data = get_a2ui_datapart(part)
        if data is not None:
            body = data.data
            return body.get("action") if isinstance(body, dict) and body.get("version") == "v0.9" else None
    return _NOT_AN_ACTION


class ExplorerExecutor(AgentExecutor):
    def __init__(self, answerer: Answerer, conversations: Conversations, card: AgentCard,
                 drills: DrillDowns | None = None, model: str | None = None, boards: BoardViews | None = None,
                 saved: SavedViews | None = None, budget: TurnBudget | None = None):
        self._answerer = answerer
        self._budget = budget
        self._boards = boards
        self._model = model
        self._conversations = conversations
        self._card = card
        self._drills = drills
        self._saved = saved
        self._writes: set[asyncio.Task[None]] = set()
        if drills is not None and drills.prefetcher is not None:
            conversations.on_evict = drills.prefetcher.forget
        if saved is not None:
            conversations.on_load = self._on_load

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        is_action = False

        def text_message(text: str, metadata: dict[str, Any] | None = None):
            return updater.new_agent_message([Part(root=TextPart(text=text))], metadata=metadata)

        try:
            metadata = context.message.metadata if context.message else None
            ui_on = try_activate_a2ui_extension(context, self._card) is not None and client_supports_catalog(metadata)
            conv = await self._conversations.get_or_load(task.context_id)
            viewing = (metadata or {}).get(VIEWING_KEY)
            self._note_view(conv, (metadata or {}).get(VIEW_KEY))
            recorder: trace.Recorder | None = None
            if (metadata or {}).get(TRACE_KEY) is True:
                async def live(event: dict[str, Any]) -> None:
                    await updater.update_status(TaskState.working, message=text_message("", {TRACE_EVENT_KEY: event}))

                recorder = trace.Recorder(live, known_prompts=set(conv.trace_prompts))

            async def status(text: str) -> None:
                await updater.update_status(TaskState.working, message=text_message(text))

            async def send(level: Level, ui: UiResult, tab: Tab, unknown: bool = False, *, model_run: bool = True,
                           board: BoardInstance | None = None, record: dict[str, Any] | None = None) -> None:
                prose = f"{UNKNOWN_CONTEXT_PROSE}\n\n{ui.prose}" if unknown else ui.prose
                parts = [Part(root=TextPart(text=prose))]
                if ui_on:
                    parts += [create_a2ui_part(m) for m in ui.messages]
                meta: dict[str, Any] = {LEVEL_KEY: {"turn": level.turn, "level": level.level, "label": level.label,
                                                    "surfaceId": level.surface_id}}
                if not model_run:
                    meta[MODEL_KEY] = None  # a Drill-down: no model, database only
                elif self._model:
                    meta[MODEL_KEY] = self._model  # unnamed: no key (null would say "no model")
                # Titles and texts can hold database text; the client renders them as plain text.
                tab_id = conv.actions.new_tab(level, board=tab.kind == "board")
                tag = f"{NEW_TAB} · {NO_MODEL}" if not model_run else f"{NEW_TAB} · {self._model}" if self._model else NEW_TAB
                meta[TAB_KEY] = {"id": tab_id, "kind": tab.kind, "title": tab.title}
                meta[LOG_KEY] = {"kind": tab.kind, "tag": tag, "text": tab.text}
                if board is not None:
                    meta[BOARD_KEY] = {"id": board.id, "controls": [{"name": c.name, "kind": c.kind} for c in board.board.controls]}
                if recorder is not None:
                    recorder.close()  # a prefetch started after this records nothing
                    meta[TRACE_KEY] = {"level": level_id(level)}
                if self._saved is not None:
                    if record is not None and MODEL_KEY in meta:
                        record = {**record, "model": meta[MODEL_KEY]}  # Workspaces SPEC E10: the chip on reopen
                    self._record(conv, level, meta[TAB_KEY], meta[LOG_KEY], record, board)
                    self._keep_trace(conv, level_id(level), recorder)
                await updater.complete(updater.new_agent_message(parts, metadata=meta))
                self._save(conv)  # after the reply is sent (B13)

            async def reply(metadata: dict[str, Any], messages: list[dict[str, Any]] | None = None) -> None:
                """A final message that adds no Level: in place, or superseded."""
                parts = [Part(root=TextPart(text=""))]
                if ui_on:
                    parts += [create_a2ui_part(m) for m in messages or []]
                if recorder is not None:
                    recorder.close()
                await updater.complete(updater.new_agent_message(parts, metadata=metadata))
                if LOG_KEY in metadata and self._saved is not None:  # a Control change: its in-place Log entry
                    board_id = metadata[IN_PLACE_KEY]["board"]
                    lid = next((k for k, v in conv.board_levels.items() if v == board_id), None)
                    tab = conv.records[lid]["tab"]["id"] if lid in conv.records else None
                    conv.log.append({"in_place": board_id, "on_tab": tab, **metadata[LOG_KEY]})
                    if lid is not None:
                        self._keep_trace(conv, lid, recorder)
                    self._save(conv)

            async def fail(text: str, **extra: Any) -> None:
                await updater.complete(text_message(text, {ACTION_FAILED_KEY: True, **extra}))

            action = action_of(context.message)
            is_action = action is not _NOT_AN_ACTION
            with trace.recording(recorder):
                await self._dispatch(context, conv, action, viewing, status, send, reply, fail, text_message, updater)
        except Exception:
            log.exception("answering failed")
            await updater.complete(text_message(GENERIC_ERROR, {ACTION_FAILED_KEY: True} if is_action else None))

    async def _dispatch(self, context, conv, action, viewing, status, send, reply, fail, text_message, updater) -> None:
        """Routes one request: a typed question or an Action."""
        if not (isinstance(action, dict) and action.get("name") == OPEN_BOARD):
            conv.persist = True  # anything beyond opening the Explorer is worth saving (B13)
        if action is _NOT_AN_ACTION:
            await self._question(conv, context.get_user_input(), viewing, status, send, text_message, updater)
            return
        if isinstance(action, dict) and action.get("name") == OPEN_BOARD:
            await self._open_board(conv, action, send, fail)
            return
        if isinstance(action, dict) and action.get("name") == CONTROLS:
            await self._controls(conv, action, reply, fail)
            return
        try:
            resolved = conv.actions.resolve(action, {sid: s for b in conv.boards.values() for sid, s in b.sources.items()})
        except ActionError as e:
            log.info("action rejected: %s", e)
            await fail(str(e))
            return
        if resolved.kind == "board":
            await self._open_entity(conv, resolved, send, fail)
        elif resolved.name == "drill":
            await self._drill(conv, resolved, send, fail)
        else:
            await self._ask(conv, resolved, viewing, status, send, text_message, updater)

    def _keep_trace(self, conv: Conversation, lid: str, recorder: trace.Recorder | None) -> None:
        """Saves the request's Trace events on Level `lid` (appended: a Control change adds to its
        Board's Level) and any new system prompt once, by hash (specs/trace)."""
        if recorder is None or lid not in conv.records or not recorder.events:
            return
        for e in recorder.events:
            if e.get("kind") == "prompt":
                conv.trace_prompts[e["hash"]] = e["text"]
        conv.records[lid].setdefault("trace", []).extend(trace.saved_form(recorder.events, omit_rows))

    # model runs -----------------------------------------------------------------------------

    async def _question(self, conv, text, viewing, status, send, text_message, updater) -> None:
        self._cancel_prefetch(conv)
        if not conv.title:
            conv.title = title_of(text)
        await self._model_run(conv, lambda: (conv.actions.new_turn(), text), viewing, status, send, text_message, updater,
                              action=False, tab=Tab("question", text, text))

    async def _ask(self, conv, resolved: Resolved, viewing, status, send, text_message, updater) -> None:
        self._cancel_prefetch(conv)

        def start() -> tuple[Level, str]:
            level = conv.actions.new_level(resolved.level.turn, resolved.label)
            result_id = ""
            if resolved.source is not None:
                result = self._ask_result(conv, resolved.source)
                result_id = result.id
                if resolved.kind == "row":
                    assert resolved.row is not None
                    conv.actions.queue_pair(row_pair(level, resolved.level, result, resolved.row))
            log.info("ask: kind=%s turn=%d level=%d", resolved.kind, level.turn, level.level)
            return level, ask_text(resolved, result_id)

        label = resolved.label
        await self._model_run(conv, start, viewing, status, send, text_message, updater, action=True,
                              tab=Tab("ask", label, f"Ask: {label}"), kind="ask")

    @staticmethod
    def _ask_result(conv: Conversation, source: ActionSource) -> StoredResult:
        """The Result an Ask names, in the store. A Board Result is copied in once, and the copy
        is reused while the store holds it (SPEC §18 slice 2)."""
        result = source.spec.result
        board = conv.board_of_source(source.id)
        if board is None:
            return restore_result(conv.store, result)
        copy = board.copies.get(result.id)
        if copy is None or conv.store.get(copy.id) is not copy:
            copy = conv.store.readd(result)
            board.copies[result.id] = copy
        return copy

    async def _board_pair(self, conv: Conversation, viewing: Any, level: Level) -> None:
        """Queues the Board pair for the viewed Board tab when it says something: an Entity Board
        or Controls off their defaults. Waits for a re-query in flight, so the pair describes a
        completed render, and pins its Results until the run ends (SPEC §18)."""
        board = conv.board_of_tab(viewing)
        if board is None or not (board.board.key or board.board.controls):
            return
        async with board.lock:
            if not board.board.key and board.at_defaults:
                return
            results = [board.results[q.name] for q in board.board.queries if q.name in board.results]
            conv.actions.board_pair = board_pair(
                f"synthetic-board-{level.turn}-{level.level}", viewing, board.title,
                shown_values(board.board, board.options, board.state), results,
                conv.board_rows_to_model, conv.board_pair_max_chars,
            )
            conv.pinned = {r.id: r for r in results}
        log.info("board pair: board=%s results=%d", board.id, len(results))

    async def _model_run(self, conv, start, viewing, status, send, text_message, updater, *, action: bool,
                         tab: Tab, kind: str = "answer") -> None:
        if self._budget is not None and not self._budget.take():
            await updater.complete(text_message(self._budget.message, {ACTION_FAILED_KEY: True} if action else None))
            return
        conv.model_runs_waiting += 1
        try:
            async with conv.lock:  # overlapping messages on one context run one after the other
                level, text = start()
                await self._board_pair(conv, viewing, level)
                lines = conv.actions.viewing_lines(viewing)
                if lines:
                    text = "\n".join(lines) + "\n\n" + text
                conv.surface_id = level.surface_id
                unknown = conv.unknown_context
                try:
                    ui = await self._answerer(conv, text, status)
                    # Before the pinned Results go; nothing is kept without a store (refuter F7).
                    record = self._reply_record(conv, ui, kind) if self._saved is not None else None
                except UiError as e:
                    lines = "\n".join(f"- {m}" for m in e.messages)
                    await updater.complete(
                        text_message(f"{UI_ERROR_PREFIX}\n{lines}", {ACTION_FAILED_KEY: True} if action else None)
                    )
                    return
                finally:
                    conv.unknown_context = False
                    conv.pinned = {}
                    conv.actions.board_pair = None  # unsent if the run failed before the model
                registered = conv.actions.register(level, ui.sources, ui.drills, ui.follow_ups)
        finally:
            conv.model_runs_waiting -= 1
        await send(level, ui, tab, unknown, record=record)
        self._start_prefetch(conv, registered)

    # Drill-downs (no model) ----------------------------------------------------------------------

    async def _drill(self, conv: Conversation, resolved: Resolved, send, fail) -> None:
        if self._drills is None:
            await fail(NO_DRILLS)
            return
        if conv.busy:
            await fail(BUSY_ERROR)
            return
        async with conv.lock:
            src = resolved.source
            assert src is not None and src.drill is not None and resolved.row is not None
            spec = src.drill
            values = spec.values(src.spec.result.rows[resolved.row])
            outcome = None
            if self._drills.prefetcher is not None:
                outcome = await self._drills.prefetcher.take(conv.id, src.id, resolved.row)
            prefetched = outcome is not None
            if outcome is None:
                with trace.source("drill"):
                    outcome = await self._drills.runner.run_bound(spec.sql, values)
            if isinstance(outcome, SqlFail):
                log.warning("drill-down failed: source=%s row=%d sqlstate=%s", src.id, resolved.row, outcome.sqlstate)
                await fail(DRILL_FAILED.format(reason=outcome.sqlstate or "query error"))
                return
            label = src.spec.labels[resolved.row]
            # The heading, tab and log are all plain text, so the label is written as is (issue 9).
            title = f"{spec.title} · {label}"
            result = conv.store.add(
                sql=display_sql(spec.sql, values), purpose=title, columns=outcome.columns,
                column_types=outcome.column_types, rows=outcome.rows, truncated=outcome.truncated,
            )
            level = conv.actions.new_level(resolved.level.turn, label)
            ui = self._drills.pipeline.build_drill_down(result, spec, title, level.surface_id, conv.actions.ids)
            conv.actions.register(level, ui.sources, [], [], drill_down=True)
            conv.actions.queue_pair(drill_down_pair(level, result, label, self._drills.rows_to_model))
            log.info(
                "drill-down: source=%s row=%d turn=%d level=%d prefetched=%s rows=%d",
                src.id, resolved.row, level.turn, level.level, prefetched, result.row_count,
            )
        record = {"kind": "drill", "result": result.id, "title": title, "spec": _drill_view(spec)}
        await send(level, ui, Tab("drill", title, f"Drill-down: {title}"), model_run=False, record=record)

    # Boards (no model) ----------------------------------------------------------------------------

    async def _open_board(self, conv: Conversation, action: dict[str, Any], send, fail) -> None:
        """Renders a Board as Turn 0, Level 1. The one action without an issued surfaceId."""
        context = action.get("context")
        if not isinstance(context, dict) or set(context) != {"board"} or not isinstance(context["board"], str):
            await fail("The action is malformed.")
            return
        if self._boards is None or not self._boards.renderer.boards:
            await fail(NO_BOARDS, **{NO_BOARDS_KEY: True})
            return
        board = self._boards.renderer.boards.get(context["board"])
        if board is None:
            await fail(UNKNOWN_BOARD)
            return
        if board.key:
            await fail(KEYED_BOARD)
            return
        if conv.board_opened:
            await fail(BOARD_OPEN)
            return
        conv.board_opened = True
        renderer = self._boards.renderer
        try:
            options, state, bound = await renderer.defaults(board)
            outcomes = await renderer.outcomes(board, bound)
        except OptionsError as e:
            log.warning("board options failed: board=%s query=%s", board.name, e)
            conv.board_opened = False
            await fail(BOARD_BUILD_FAILED)
            return
        except BaseException:
            conv.board_opened = False  # it can be tried again
            raise
        # No await from here to send: IDs, the Level and the instance are taken atomically.
        level = conv.actions.new_level(0, board.title)
        results, failed = instance_results(outcomes, conv.store.reserve_id)
        try:
            ui = build_board(board, results, failed, self._boards.pipeline, level.surface_id, conv.actions.ids,
                             options, state)
        except UiError:
            # Panels that can't be drawn are Cards (Board mode), so this is the Surface itself.
            log.error("board build failed: board=%s", board.name)  # the message can quote values
            conv.actions.surfaces.pop(level.surface_id, None)
            conv.actions.levels.pop(0, None)
            conv.board_opened = False
            await fail(BOARD_BUILD_FAILED)
            return
        instance = self._add_instance(conv, board, level, ui, results, failed, options, state)
        await send(level, ui, Tab("board", board.title, f"Opened {board.title}"), model_run=False, board=instance,
                   record=self._board_record(instance))
        self._start_prefetch(conv, list(instance.sources.values()))

    def _add_instance(self, conv: Conversation, board, level: Level, ui: UiResult, results, failed, options, state, *,
                      key: Any = None, title: str = "", defaults: dict[str, Any] | None = None) -> BoardInstance:
        """A new Board instance under the next `b<n>`; beyond `max_board_instances` the oldest
        one other than the Explorer is dropped, and its clicks and Controls answer "too old"."""
        assert self._boards is not None
        conv.board_counter += 1
        instance_id = f"b{conv.board_counter}"
        sources = conv.actions.board_sources(level, ui.sources, ui.drills)
        instance = BoardInstance(
            instance_id, board, level.surface_id, results, sources, level=level, failed=dict(failed),
            options=options, state=state, defaults=dict(state if defaults is None else defaults),
            deps=panel_dependencies(board, layout_components(board, self._boards.pipeline)),
            panel_sources=panel_sources(ui.messages), key=key, title=title,
        )
        conv.boards[instance_id] = instance
        while len(conv.boards) > conv.max_board_instances:
            victim = next((i for i, b in conv.boards.items() if b.board.name != EXPLORER), None)
            if victim is None or victim == instance_id:
                break
            del conv.boards[victim]
            conv.dropped_boards.add(victim)
            log.info("board instance dropped: instance=%s", victim)
        log.info("board opened: board=%s instance=%s failed=%d", board.name, instance_id, len(failed))
        return instance

    async def _open_entity(self, conv: Conversation, resolved: Resolved, send, fail) -> None:
        """A row click that opens an Entity Board (SPEC §18 slice 3), with no model call: a new
        Level in the clicked tab's Turn, rendered at the default Controls with the key bound."""
        src = resolved.source
        assert src is not None and resolved.row is not None
        board = self._boards.renderer.boards.get(src.spec.board) if self._boards is not None else None
        if board is None or self._boards is None:
            await fail(UNKNOWN_BOARD)
            return
        if conv.busy:
            await fail(BUSY_ERROR)
            return
        async with conv.lock:
            row = resolved.row
            key = key_value(src.spec.result.rows[row][src.spec.key_column], board.key_type)
            prefetcher = self._drills.prefetcher if self._drills is not None else None
            if prefetcher is not None:
                await prefetcher.take(conv.id, src.id, row)  # a running render is waited for: it fills the cache
            renderer = self._boards.renderer
            try:
                options, state, bound = await renderer.defaults(board)
                outcomes = await renderer.outcomes(board, bound, key=key)
            except OptionsError as e:
                log.warning("board options failed: board=%s query=%s", board.name, e)
                await fail(ENTITY_FAILED)
                return
            label = src.spec.labels[row]
            title = f"{board.title} · {label}"  # plain text everywhere: shown as written (issue 9)
            level = conv.actions.new_level(resolved.level.turn, label)
            results, failed = instance_results(outcomes, conv.store.reserve_id)
            try:
                ui = build_board(board, results, failed, self._boards.pipeline, level.surface_id, conv.actions.ids,
                                 options, state, title=title)
            except UiError:
                log.error("board build failed: board=%s", board.name)
                conv.actions.surfaces.pop(level.surface_id, None)
                conv.actions.levels[level.turn] = level.level - 1
                await fail(ENTITY_FAILED)
                return
            instance = self._add_instance(conv, board, level, ui, results, failed, options, state, key=key, title=title)
        await send(level, ui, Tab("board", title, f"Opened {title}"), model_run=False, board=instance,
                   record=self._board_record(instance))
        self._start_prefetch(conv, list(instance.sources.values()))

    async def _controls(self, conv: Conversation, action: dict[str, Any], reply, fail) -> None:
        """A Control change: re-query the Board instance in place (SPEC §18 slice 2). Logs name
        the instance, `seq` and queries only, never Control values or labels."""
        context = action.get("context")
        if not isinstance(context, dict) or set(context) != CONTROLS_KEYS or not isinstance(context["board"], str):
            await fail("The action is malformed.")
            return
        seq = context["seq"]
        if context["board"] in conv.dropped_boards:
            await fail(TOO_OLD)
            return
        instance = conv.boards.get(context["board"])
        if instance is None or self._boards is None:
            await fail(UNKNOWN_BOARD)
            return
        if action.get("surfaceId") != instance.surface_id:
            await fail("The action doesn't match its view.")
            return
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            await fail("The action is malformed.")
            return
        if conv.busy:
            await fail(BUSY_ERROR)
            return
        board = instance.board
        try:
            state = validate_values(board, instance.options, context["values"])
        except ControlError as e:
            log.info("controls rejected: board=%s seq=%d", instance.id, seq)
            await fail(str(e))
            return
        instance.seq_seen = max(instance.seq_seen, seq)
        async with instance.lock:
            if seq < instance.seq_seen or seq <= instance.seq_applied:
                log.info("controls superseded: board=%s seq=%d", instance.id, seq)
                await reply({SUPERSEDED_KEY: True})
                return
            # Measured against the last completed render, so a superseded change's panels re-run.
            changed = {n for n in state if state[n] != instance.state[n]}
            queries, panels = changed_panels(board, instance.deps, changed)
            bound = bound_values(board, instance.options, state)
            outcomes = await self._boards.renderer.outcomes(board, bound, only=queries, key=instance.key) if queries else {}
            new_results, new_failed = instance_results(outcomes, conv.store.reserve_id)
            results = {**{n: r for n, r in instance.results.items() if n not in queries}, **new_results}
            failed = {**{n: f for n, f in instance.failed.items() if n not in queries}, **new_failed}
            assert instance.level is not None
            try:
                ui = build_board(board, results, failed, self._boards.pipeline, instance.surface_id, conv.actions.ids,
                                 instance.options, state, title=instance.title)
            except UiError:
                log.error("board rebuild failed: board=%s seq=%d", instance.id, seq)
                await fail(BOARD_UPDATE_FAILED)
                return
            sent = panel_components(ui.messages, panels)
            built = panel_sources(ui.messages)
            new_sources = conv.actions.board_sources(instance.level, ui.sources, ui.drills)
            for panel in panels:
                for sid in instance.panel_sources.pop(panel, []):
                    instance.sources.pop(sid, None)
                if panel in built:
                    instance.panel_sources[panel] = built[panel]
                    instance.sources.update({sid: new_sources[sid] for sid in built[panel]})
            for name in queries:  # released Results: their store copies are no longer reused
                if name in instance.results:
                    instance.copies.pop(instance.results[name].id, None)
            instance.results, instance.failed, instance.state = results, failed, state
            instance.seq_applied = seq
            registered = [instance.sources[sid] for p in panels for sid in instance.panel_sources.get(p, [])]
            log.info("controls applied: board=%s seq=%d queries=%s panels=%d", instance.id, seq,
                     ",".join(sorted(queries)) or "-", len(sent))
            messages = [{"version": "v0.9", "updateComponents": {"surfaceId": instance.surface_id, "components": sent}}] if sent else []
            text = log_text(board, instance.options, state, instance.defaults)
            await reply({
                IN_PLACE_KEY: {"board": instance.id, "seq": seq},
                LOG_KEY: {"kind": CONTROLS_LOG_KIND, "tag": f"{IN_PLACE} · {NO_MODEL}", "text": text},
            }, messages)
        # `start` replaces the Conversation's prefetch, so the untouched panels' sources go too,
        # after the re-sent ones; rows already held are skipped.
        self._start_prefetch(conv, [*registered, *(s for s in instance.sources.values() if s not in registered)])

    # saving and rebuilding (Workspaces phase 1) ------------------------------------------------

    def _on_load(self, conv: Conversation) -> None:
        conv.restore_results = functools.partial(self._restore_named, conv)

    @staticmethod
    def _note_view(conv: Conversation, view: Any) -> None:
        """The client's closed and selected Tabs (B12). Unknown tab IDs are dropped."""
        if not isinstance(view, dict):
            return
        tabs = conv.actions.tabs
        closed, selected = view.get("closed"), view.get("selected")
        if isinstance(closed, list):
            conv.view["closed"] = list(dict.fromkeys(t for t in closed if isinstance(t, str) and t in tabs))[:MAX_VIEW_TABS]
        if isinstance(selected, str) and selected in tabs:
            conv.view["selected"] = selected

    @staticmethod
    def _reply_record(conv: Conversation, ui: UiResult, kind: str) -> dict[str, Any]:
        """An answer or Ask Level as saved (B7): the reply, the Drill Visuals it ignored, and the
        Result IDs it names that the Conversation held."""
        if ui.reply is None:
            return {"kind": kind, "reply": None}
        view = conv.results
        named = [(rid, view.get(rid)) for rid in result_ids_in(ui.reply)]
        held = [(rid, r) for rid, r in named if r is not None]
        conv.unsaved += [r for rid, r in held if conv.store.get(rid) is not r]
        return {"kind": kind, "reply": ui.reply, "ignore_drills": sorted(ui.ignore_drills),
                "results": [rid for rid, _ in held]}

    def _board_record(self, instance: BoardInstance) -> dict[str, Any]:
        name = instance.board.name
        return {"kind": "board", "board": name, "key": to_json_safe(instance.key), "values": dict(instance.state),
                "shown": chosen_values(instance.board, instance.options, instance.state),
                "hash": self._boards.hashes.get(name, "") if self._boards is not None else "", "title": instance.title}

    @staticmethod
    def _record(conv: Conversation, level: Level, tab: dict[str, Any], log_entry: dict[str, Any],
                record: dict[str, Any] | None, board: BoardInstance | None) -> None:
        if record is None:
            return
        lid = level_id(level)
        conv.records[lid] = {**record, "turn": level.turn, "level": level.level, "label": level.label,
                             "surface_id": level.surface_id, "tab": dict(tab)}
        conv.log.append({"tab": tab["id"], **log_entry})
        if board is not None:
            conv.board_levels[lid] = board.id

    def _snapshot(self, conv: Conversation) -> Snapshot:
        """The whole Conversation, built on the event loop from copies and references (B13)."""
        for lid, iid in conv.board_levels.items():
            instance = conv.boards.get(iid)
            if instance is not None and lid in conv.records:
                conv.records[lid]["values"] = dict(instance.state)
                conv.records[lid]["shown"] = chosen_values(instance.board, instance.options, instance.state)
        new: dict[str, StoredResult] = {}
        for rid in conv.store.ids():
            if rid not in conv.written:
                new[rid] = conv.store.get(rid)  # type: ignore[assignment]
        for r in conv.unsaved:
            if r.id not in conv.written:
                new.setdefault(r.id, r)
        conv.unsaved = []
        conv.written |= set(new)
        a = conv.actions
        state = {
            "version": STATE_VERSION, "surface_tag": conv.surface_tag, "run_starts": list(conv.run_starts), "turns": a.turns,
            "levels": {str(k): v for k, v in a.levels.items()}, "ids": [a.ids._v, a.ids._f],
            "latest_tab": a.latest_tab, "store_next": conv.store.next_number, "board_counter": conv.board_counter,
            "board_opened": conv.board_opened, "records": copy.deepcopy(conv.records),
            "log": [dict(e) for e in conv.log], "view": copy.deepcopy(conv.view),
        }
        if conv.trace_prompts:
            state["trace_prompts"] = dict(conv.trace_prompts)
        title = conv.title or next((r["tab"]["title"] for r in conv.records.values() if r["kind"] != "board"), "")
        title = title_of(title or next((r["tab"]["title"] for r in conv.records.values()), "") or "Conversation")
        return Snapshot(conv.id, title, conv.created_at, _now(), a.turns, state, list(conv.history), list(new.values()))

    def _save(self, conv: Conversation) -> None:
        if self._saved is None or not conv.persist or conv.deleted:
            return  # nothing worth saving yet, or deleted meanwhile
        task = asyncio.get_running_loop().create_task(self._write(conv, self._snapshot(conv)))
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)

    async def _write(self, conv: Conversation, snap: Snapshot) -> None:
        assert self._saved is not None
        try:
            await self._saved.store.save(snap)
        except Exception as e:
            log.error("saving a conversation failed: %s", type(e).__name__)  # never its content
            # Its Results go with the next write (refuter F8): the store's are picked up again,
            # the pinned ones are queued again.
            conv.written -= {r.id for r in snap.results}
            conv.unsaved += [r for r in snap.results if conv.store.get(r.id) is not r]

    async def flush(self) -> None:
        """Waits for the writes scheduled so far (tests and shutdown)."""
        while self._writes:
            pending = list(self._writes)
            await asyncio.gather(*pending, return_exceptions=True)
            self._writes.difference_update(pending)

    async def _restore_named(self, conv: Conversation, ids: list[str]) -> None:
        """B9: saved Results a new reply names, re-run before it is built."""
        missing = [rid for rid in ids if conv.results.get(rid) is None and rid in conv.saved_results]
        if missing:
            await self._rerun(conv, missing)

    async def _rerun(self, conv: Conversation, ids: list[str]) -> tuple[bool, dict[str, tuple[str, str]]]:
        """Re-runs saved Results into the store under their saved IDs (B8), once per process.
        Returns whether any hash differs (G16) and the failed ones: ID -> (reason, sql)."""
        assert self._saved is not None
        changed = False
        failed: dict[str, tuple[str, str]] = {}
        for rid in ids:
            if rid in conv.reruns:
                was_changed, failure = conv.reruns[rid]
            elif conv.results.get(rid) is not None:
                continue  # held since it was built in this process
            else:
                saved = conv.saved_results.get(rid)
                if saved is None:  # evicted from the store in this process: read its SQL back
                    loaded = await self._saved.store.load(conv.id)
                    if loaded is not None:
                        conv.saved_results.update(loaded.results)
                    saved = conv.saved_results.get(rid)
                if saved is None:
                    was_changed, failure = False, ("the result is no longer available", "")
                else:
                    with trace.source("rerun"):
                        outcome = await self._saved.runner.run(saved.sql)
                    if isinstance(outcome, SqlFail):
                        log.warning("saved result re-run failed: sqlstate=%s", outcome.sqlstate)
                        was_changed, failure = False, (outcome.message, saved.sql)
                    else:
                        result = conv.store.put(rid, sql=saved.sql, purpose=saved.purpose, columns=outcome.columns,
                                                column_types=outcome.column_types, rows=outcome.rows,
                                                truncated=outcome.truncated)
                        was_changed, failure = await asyncio.to_thread(row_hash, result.rows, result.columns, result.column_types) != saved.row_hash, None
                conv.reruns[rid] = (was_changed, failure)
            changed |= was_changed
            if failure is not None:
                failed[rid] = failure
        return changed, failed

    def view_of(self, conv: Conversation) -> dict[str, Any]:
        """The saved view (SPEC "HTTP API"): Tabs, Log entries, the selected Tab, Control values
        and Level IDs. No agent work."""
        closed = set(conv.view.get("closed") or ())
        tabs = []
        for lid, rec in sorted(conv.records.items(), key=lambda kv: int(kv[1]["tab"]["id"][1:])):
            entry = next((e for e in conv.log if e.get("tab") == rec["tab"]["id"]), {})
            tabs.append({**rec["tab"], "level": lid, "closed": rec["tab"]["id"] in closed,
                         "log": {k: entry.get(k) for k in ("kind", "tag", "text")}})
        controls = {lid: rec.get("values", {}) for lid, rec in conv.records.items() if rec["kind"] == "board"}
        return {"id": conv.id, "title": conv.title, "tabs": tabs, "log": [dict(e) for e in conv.log],
                "selected": conv.view.get("selected") or conv.actions.latest_tab, "controls": controls,
                "levels": [t["level"] for t in tabs]}

    async def rebuild_level(self, conv: Conversation, lid: str, *, defaults: bool = False) -> dict[str, Any] | None:
        """A saved Level shown again (SPEC "Resuming", B10, B11). None: no such Level."""
        rec = conv.records.get(lid)
        if rec is None or self._saved is None:
            return None
        async with conv.lock:
            level = conv.actions.surfaces[rec["surface_id"]]
            out: dict[str, Any] = {"prose": "", "a2ui": [], "changed": False, "failed_results": [],
                                   "board_changed": False, "board": None, "tab": dict(rec["tab"])}
            if "model" in rec:  # E10: Levels saved before it have no key, so no chip
                out["model"] = rec["model"]
            if rec.get("trace"):  # specs/trace: the saved events and the prompts they name
                hashes = {e.get("prompt_hash") for e in rec["trace"]}
                out["trace"] = {"events": rec["trace"],
                                "prompts": {h: t for h, t in conv.trace_prompts.items() if h in hashes}}
            kind = rec["kind"]
            if kind == "board":
                await self._rebuild_board(conv, lid, rec, level, out, defaults)
            elif kind == "drill":
                changed, failed = await self._rerun(conv, [rec["result"]])
                result = conv.results.get(rec["result"])
                if failed or result is None:
                    reason = failed.get(rec["result"], ("query error", ""))[0]
                    out.update(prose=RERUN_FAILED.format(reason=reason), failed_results=[rec["result"]])
                else:
                    sv = rec["spec"]
                    spec = DrillSpec(source="", visual=0, sql=sv["sql"], names=tuple(sv["names"]), drill_as=sv["drill_as"],
                                     drill_x=sv["drill_x"], drill_y=sv["drill_y"], title=sv["title"])
                    ui = self._saved.pipeline.build_drill_down(result, spec, rec["title"], level.surface_id, conv.actions.ids)
                    conv.actions.register(level, ui.sources, [], [], drill_down=True)
                    out.update(a2ui=ui.messages, changed=changed)
            elif rec.get("reply") is None:
                out["prose"] = GAVE_UP
            else:
                changed, failed = await self._rerun(conv, rec["results"])
                try:
                    ui = self._saved.pipeline.build(rec["reply"], conv.results, level.surface_id, conv.actions.ids,
                                                    rec.get("ignore_drills", ()), rerun_failed=failed)
                except UiError:
                    log.warning("saved level rebuild failed: level=%s", lid)
                    out["prose"] = REBUILD_FAILED
                    return out
                conv.actions.register(level, ui.sources, ui.drills, ui.follow_ups)
                out.update(prose=ui.prose, a2ui=ui.messages, changed=changed, failed_results=sorted(failed))
            log.info("level rebuilt: level=%s kind=%s changed=%s failed=%d", lid, kind, out["changed"],
                     len(out["failed_results"]))
            return out

    async def _rebuild_board(self, conv: Conversation, lid: str, rec: dict[str, Any], level: Level,
                             out: dict[str, Any], defaults: bool) -> None:
        views = self._boards
        board = views.renderer.boards.get(rec["board"]) if views is not None else None
        if board is None or views is None or (not defaults and views.hashes.get(board.name) != rec["hash"]):
            out["board_changed"] = True  # G18: never the old definition
            return
        renderer = views.renderer
        try:
            options, start, _ = await renderer.defaults(board)
        except OptionsError:
            log.warning("board options failed: board=%s", board.name)
            out["prose"] = ENTITY_FAILED
            return
        if defaults:
            state = start
        else:
            try:
                state = validate_values(board, options, rec["values"])
            except ControlError:  # its options moved: the saved values no longer apply
                out["board_changed"] = True
                return
            if chosen_values(board, options, state) != rec.get("shown", {}):
                out["board_changed"] = True  # an index now names another option (refuter F5)
                return
        key = key_value(rec["key"], board.key_type) if board.key else None
        outcomes = await renderer.outcomes(board, bound_values(board, options, state), key=key)
        results, failed = instance_results(outcomes, conv.store.reserve_id)
        try:
            ui = build_board(board, results, failed, views.pipeline, level.surface_id, conv.actions.ids, options, state,
                             title=rec["title"])
        except UiError:
            log.error("board rebuild failed: board=%s", board.name)
            out["prose"] = REBUILD_FAILED
            return
        for iid in [i for i, b in conv.boards.items() if b.surface_id == level.surface_id]:
            del conv.boards[iid]  # the instance shown before on this surface
        instance = self._add_instance(conv, board, level, ui, results, failed, options, state, key=key,
                                      title=rec["title"], defaults=start)
        conv.board_levels[lid] = instance.id
        if board.name == EXPLORER:
            conv.board_opened = True
        if defaults:
            rec["values"], rec["hash"] = dict(state), views.hashes.get(board.name, "")
            rec["shown"] = chosen_values(board, options, state)
            self._save(conv)
        out.update(a2ui=ui.messages, board={"id": instance.id, "controls": [{"name": c.name, "kind": c.kind}
                                                                            for c in board.controls]})

    # prefetch --------------------------------------------------------------------------------

    def _cancel_prefetch(self, conv: Conversation) -> None:
        if self._drills is not None and self._drills.prefetcher is not None:
            self._drills.prefetcher.cancel(conv.id)

    def _start_prefetch(self, conv: Conversation, sources: list[ActionSource]) -> None:
        prefetcher = self._drills.prefetcher if self._drills is not None else None
        if prefetcher is None or conv.busy:
            return  # never while a model run of the Conversation runs or waits
        jobs = [
            (src.drill, row, src.drill.values(src.spec.result.rows[row]))
            for src in sources if src.drill is not None and not src.spec.boards  # an Entity Board wins
            for row in src.spec.rows[: prefetcher.rows]
        ]
        boards: list[tuple[str, int, Any]] = []
        if self._boards is not None and self._boards.prefetch_rows > 0:
            renderer = self._boards.renderer
            for src in sources:
                board = renderer.boards.get(src.spec.board) if src.spec.boards else None
                if board is None:
                    continue
                for row in src.spec.rows[: self._boards.prefetch_rows]:
                    if row in src.spec.boards:
                        key = key_value(src.spec.result.rows[row][src.spec.key_column], board.key_type)
                        run = functools.partial(renderer.prefetch, board, key, timeout_ms=prefetcher.timeout_ms)
                        boards.append((src.id, row, run))
        prefetcher.start(conv.id, jobs, boards)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancel is not supported")
