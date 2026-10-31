"""A2A executor: one task per user message (SPEC §2, thin-slice version).

The `Answerer` is the seam where the tool loop (`llm.py`) plugs in; in the thin slice the stub
server supplies a canned one.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentCard, Part, TaskState, TextPart
from a2a.utils import new_task
from a2ui.a2a import create_a2ui_part, try_activate_a2ui_extension
from a2ui.constants import A2UI_CLIENT_CAPABILITIES_KEY

from explorer_agent.catalog import EXPLORER_CATALOG_ID
from explorer_agent.results import ResultStore
from explorer_agent.ui import UiError, UiPipeline

log = logging.getLogger(__name__)

UI_ERROR_PREFIX = "I couldn't build the view for this answer:"
GENERIC_ERROR = "Something went wrong while answering. Please try again."


@dataclass
class Conversation:
    id: str
    store: ResultStore = field(default_factory=ResultStore)
    turns: int = 0


class Conversations:
    """In-memory conversations by A2A context ID. (No LRU cap yet: a later step.)"""

    def __init__(self) -> None:
        self._by_id: dict[str, Conversation] = {}

    def get_or_create(self, context_id: str) -> Conversation:
        conv = self._by_id.get(context_id)
        if conv is None:
            conv = self._by_id[context_id] = Conversation(context_id)
        return conv


StatusFn = Callable[[str], Awaitable[None]]
Answerer = Callable[[Conversation, str, StatusFn], Awaitable[str]]


def client_supports_catalog(metadata: dict[str, Any] | None) -> bool:
    try:
        ids = metadata[A2UI_CLIENT_CAPABILITIES_KEY]["v0.9"]["supportedCatalogIds"]  # type: ignore[index]
    except (KeyError, TypeError):
        return False
    return isinstance(ids, list) and EXPLORER_CATALOG_ID in ids


class ExplorerExecutor(AgentExecutor):
    def __init__(
        self, answerer: Answerer, conversations: Conversations, pipeline: UiPipeline, card: AgentCard
    ):
        self._answerer = answerer
        self._conversations = conversations
        self._pipeline = pipeline
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
            conv.turns += 1
            surface_id = f"turn-{conv.turns}"

            async def status(text: str) -> None:
                await updater.update_status(TaskState.working, message=text_message(text))

            reply = await self._answerer(conv, context.get_user_input(), status)
            try:
                ui = self._pipeline.build(reply, conv.store, surface_id)
            except UiError as e:
                lines = "\n".join(f"- {m}" for m in e.messages)
                await updater.complete(text_message(f"{UI_ERROR_PREFIX}\n{lines}"))
                return
            parts = [Part(root=TextPart(text=ui.prose))]
            if ui_on:
                parts += [create_a2ui_part(m) for m in ui.messages]
            await updater.complete(updater.new_agent_message(parts))
        except Exception:
            log.exception("answering failed")
            await updater.complete(text_message(GENERIC_ERROR))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancel is not supported")
