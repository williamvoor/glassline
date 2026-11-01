"""A2A executor: one task per user message (parent spec §2).

The `Answerer` (the tool loop in `llm.py`, or the stub server's canned one) returns a finished
`UiResult`; the executor only sends it. Repairs happen inside the answerer.
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
from a2ui.a2a import create_a2ui_part, try_activate_a2ui_extension
from a2ui.constants import A2UI_CLIENT_CAPABILITIES_KEY

from explorer_agent.catalog import EXPLORER_CATALOG_ID
from explorer_agent.results import ResultStore
from explorer_agent.ui import UiError, UiResult

log = logging.getLogger(__name__)

UI_ERROR_PREFIX = "I couldn't build the view for this answer:"
UNKNOWN_CONTEXT_PROSE = "Earlier results from this conversation are no longer available."
MAX_CONVERSATIONS = 100
GENERIC_ERROR = "Something went wrong while answering. Please try again."


@dataclass
class Conversation:
    id: str
    store: ResultStore = field(default_factory=ResultStore)
    turns: int = 0
    surface_id: str = ""  # the current Turn's surface, set by the executor
    # Random per Conversation, so surface IDs don't repeat across agent restarts: the client keeps
    # its contextId and surfaces, and rejects a createSurface for an ID it already holds.
    surface_tag: str = field(default_factory=lambda: secrets.token_hex(3))
    history: list[dict[str, Any]] = field(default_factory=list)  # messages for the model, stored in full
    turn_starts: list[int] = field(default_factory=list)  # history index where each Turn begins
    prompt_tokens: int = 0
    cached_tokens: int = 0
    unknown_context: bool = False  # created for a contextId we don't hold (restart or eviction)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)  # one Turn at a time


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

    def __init__(self, max_conversations: int = MAX_CONVERSATIONS) -> None:
        self._max = max_conversations
        self._by_id: OrderedDict[str, Conversation] = OrderedDict()
        self.id_generator = _IssuedIds()

    def get_or_create(self, context_id: str) -> Conversation:
        conv = self._by_id.get(context_id)
        if conv is not None:
            self._by_id.move_to_end(context_id)
            return conv
        conv = Conversation(context_id)
        if context_id in self.id_generator.issued:
            del self.id_generator.issued[context_id]
        else:
            conv.unknown_context = True
        self._by_id[context_id] = conv
        while len(self._by_id) > self._max:
            self._by_id.popitem(last=False)
        return conv


StatusFn = Callable[[str], Awaitable[None]]
Answerer = Callable[[Conversation, str, StatusFn], Awaitable[UiResult]]


def client_supports_catalog(metadata: dict[str, Any] | None) -> bool:
    try:
        ids = metadata[A2UI_CLIENT_CAPABILITIES_KEY]["v0.9"]["supportedCatalogIds"]  # type: ignore[index]
    except (KeyError, TypeError):
        return False
    return isinstance(ids, list) and EXPLORER_CATALOG_ID in ids


class ExplorerExecutor(AgentExecutor):
    def __init__(self, answerer: Answerer, conversations: Conversations, card: AgentCard):
        self._answerer = answerer
        self._conversations = conversations
        self._card = card

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        def text_message(text: str):
            return updater.new_agent_message([Part(root=TextPart(text=text))])

        try:
            ui_on = try_activate_a2ui_extension(context, self._card) is not None and client_supports_catalog(
                context.message.metadata if context.message else None
            )
            conv = self._conversations.get_or_create(task.context_id)

            async def status(text: str) -> None:
                await updater.update_status(TaskState.working, message=text_message(text))

            async with conv.lock:  # overlapping messages on one context run one after the other
                conv.turns += 1
                conv.surface_id = f"turn-{conv.turns}-{conv.surface_tag}"
                unknown = conv.unknown_context
                try:
                    ui = await self._answerer(conv, context.get_user_input(), status)
                except UiError as e:
                    lines = "\n".join(f"- {m}" for m in e.messages)
                    await updater.complete(text_message(f"{UI_ERROR_PREFIX}\n{lines}"))
                    return
                finally:
                    conv.unknown_context = False
            prose = f"{UNKNOWN_CONTEXT_PROSE}\n\n{ui.prose}" if unknown else ui.prose
            parts = [Part(root=TextPart(text=prose))]
            if ui_on:
                parts += [create_a2ui_part(m) for m in ui.messages]
            await updater.complete(updater.new_agent_message(parts))
        except Exception:
            log.exception("answering failed")
            await updater.complete(text_message(GENERIC_ERROR))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancel is not supported")
