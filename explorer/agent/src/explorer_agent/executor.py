"""A2A executor: one task per user message (parent spec §2, SPEC §15).

A message is a typed question or an Action. A typed question starts a Turn; an Action adds a
Level to the Turn it came from. A question or an Ask is a model run: the `Answerer` (the tool
loop in `llm.py`, or the stub server's canned one) returns a finished `UiResult` and the
executor only sends it. A Drill-down runs its Drill query here, with no model call.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections import OrderedDict
from collections.abc import Awaitable, Callable
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

from explorer_agent.actions import (
    MAX_ACTION_SOURCES,
    MAX_PENDING_PAIRS,
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
from explorer_agent.boards import (
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
from explorer_agent.drill import Prefetcher, display_sql
from explorer_agent.macros import escape_markdown, unescape_markdown
from explorer_agent.results import ResultStore, ResultView, StoredResult
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
    pinned: dict[str, StoredResult] = field(default_factory=dict)  # the Board pair's Results, for its model run
    board_rows_to_model: int = 5
    board_pair_max_chars: int = 6000

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
    """In-memory conversations by A2A context ID, least recently used evicted beyond the cap."""

    def __init__(self, max_conversations: int = MAX_CONVERSATIONS, *, max_action_sources: int = MAX_ACTION_SOURCES,
                 max_pending_pairs: int = MAX_PENDING_PAIRS, board_rows_to_model: int = 5,
                 board_pair_max_chars: int = 6000) -> None:
        self._max = max_conversations
        self._by_id: OrderedDict[str, Conversation] = OrderedDict()
        self.id_generator = _IssuedIds()
        self._limits = {"max_action_sources": max_action_sources, "max_pending_pairs": max_pending_pairs,
                        "board_rows_to_model": board_rows_to_model, "board_pair_max_chars": board_pair_max_chars}
        self.on_evict: Callable[[str], None] | None = None

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
        self._by_id[context_id] = conv
        while len(self._by_id) > self._max:
            old, _ = self._by_id.popitem(last=False)
            if self.on_evict is not None:
                self.on_evict(old)
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
                 drills: DrillDowns | None = None, model: str | None = None, boards: BoardViews | None = None):
        self._answerer = answerer
        self._boards = boards
        self._model = model
        self._conversations = conversations
        self._card = card
        self._drills = drills
        if drills is not None and drills.prefetcher is not None:
            conversations.on_evict = drills.prefetcher.forget

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
            conv = self._conversations.get_or_create(task.context_id)
            viewing = (metadata or {}).get(VIEWING_KEY)

            async def status(text: str) -> None:
                await updater.update_status(TaskState.working, message=text_message(text))

            async def send(level: Level, ui: UiResult, tab: Tab, unknown: bool = False, *, model_run: bool = True,
                           board: BoardInstance | None = None) -> None:
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
                await updater.complete(updater.new_agent_message(parts, metadata=meta))

            async def reply(metadata: dict[str, Any], messages: list[dict[str, Any]] | None = None) -> None:
                """A final message that adds no Level: in place, or superseded."""
                parts = [Part(root=TextPart(text=""))]
                if ui_on:
                    parts += [create_a2ui_part(m) for m in messages or []]
                await updater.complete(updater.new_agent_message(parts, metadata=metadata))

            async def fail(text: str, **extra: Any) -> None:
                await updater.complete(text_message(text, {ACTION_FAILED_KEY: True, **extra}))

            action = action_of(context.message)
            is_action = action is not _NOT_AN_ACTION
            if not is_action:
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
            if resolved.name == "drill":
                await self._drill(conv, resolved, send, fail)
            else:
                await self._ask(conv, resolved, viewing, status, send, text_message, updater)
        except Exception:
            log.exception("answering failed")
            await updater.complete(text_message(GENERIC_ERROR, {ACTION_FAILED_KEY: True} if is_action else None))

    # model runs -----------------------------------------------------------------------------

    async def _question(self, conv, text, viewing, status, send, text_message, updater) -> None:
        self._cancel_prefetch(conv)
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
                              tab=Tab("ask", label, f"Ask: {label}"))

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
        if board is None or not board.board.controls:
            return
        async with board.lock:
            if board.at_defaults:
                return
            results = [board.results[q.name] for q in board.board.queries if q.name in board.results]
            conv.actions.board_pair = board_pair(
                f"synthetic-board-{level.turn}-{level.level}", viewing, board.board.title,
                shown_values(board.board, board.options, board.state), results,
                conv.board_rows_to_model, conv.board_pair_max_chars,
            )
            conv.pinned = {r.id: r for r in results}
        log.info("board pair: board=%s results=%d", board.id, len(results))

    async def _model_run(self, conv, start, viewing, status, send, text_message, updater, *, action: bool,
                         tab: Tab) -> None:
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
        await send(level, ui, tab, unknown)
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
                outcome = await self._drills.runner.run_bound(spec.sql, values)
            if isinstance(outcome, SqlFail):
                log.warning("drill-down failed: source=%s row=%d sqlstate=%s", src.id, resolved.row, outcome.sqlstate)
                await fail(DRILL_FAILED.format(reason=outcome.sqlstate or "query error"))
                return
            label = src.spec.labels[resolved.row]
            # The label is database text; the client renders Text as markdown (the title part was filled by Cite).
            title = f"{spec.title} · {escape_markdown(label)}"
            result = conv.store.add(
                sql=display_sql(spec.sql, values), purpose=f"{spec.title} · {label}", columns=outcome.columns,
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
        plain = f"{unescape_markdown(spec.title)} · {label}"  # the tab is plain text; `_fill` escaped its values
        await send(level, ui, Tab("drill", plain, f"Drill-down: {plain}"), model_run=False)

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
        instance_id = f"b{len(conv.boards) + 1}"
        sources = conv.actions.board_sources(level, ui.sources, ui.drills)
        instance = BoardInstance(
            instance_id, board, level.surface_id, results, sources, level=level, failed=dict(failed),
            options=options, state=state, defaults=dict(state),
            deps=panel_dependencies(board, layout_components(board, self._boards.pipeline)),
            panel_sources=panel_sources(ui.messages),
        )
        conv.boards[instance_id] = instance
        log.info("board opened: board=%s instance=%s failed=%d", board.name, instance_id, len(failed))
        await send(level, ui, Tab("board", board.title, f"Opened {board.title}"), model_run=False, board=instance)
        self._start_prefetch(conv, list(sources.values()))

    async def _controls(self, conv: Conversation, action: dict[str, Any], reply, fail) -> None:
        """A Control change: re-query the Board instance in place (SPEC §18 slice 2). Logs name
        the instance, `seq` and queries only, never Control values or labels."""
        context = action.get("context")
        if not isinstance(context, dict) or set(context) != CONTROLS_KEYS or not isinstance(context["board"], str):
            await fail("The action is malformed.")
            return
        seq = context["seq"]
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
            outcomes = await self._boards.renderer.outcomes(board, bound, only=queries) if queries else {}
            new_results, new_failed = instance_results(outcomes, conv.store.reserve_id)
            results = {**{n: r for n, r in instance.results.items() if n not in queries}, **new_results}
            failed = {**{n: f for n, f in instance.failed.items() if n not in queries}, **new_failed}
            assert instance.level is not None
            try:
                ui = build_board(board, results, failed, self._boards.pipeline, instance.surface_id, conv.actions.ids,
                                 instance.options, state)
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
            for src in sources if src.drill is not None
            for row in src.spec.rows[: prefetcher.rows]
        ]
        prefetcher.start(conv.id, jobs)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancel is not supported")
