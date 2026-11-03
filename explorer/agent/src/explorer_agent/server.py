"""Agent card and Starlette app. The real entry point (`python -m explorer_agent`) comes with config."""

from __future__ import annotations

from a2a.server.apps import A2AStarletteApplication
from a2a.server.agent_execution import SimpleRequestContextBuilder
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2ui.a2a import get_a2ui_agent_extension
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware

from explorer_agent.catalog import EXPLORER_CATALOG_ID
from explorer_agent.executor import Answerer, Conversations, DrillDowns, ExplorerExecutor


DEFAULT_DESCRIPTION = "Answers questions about a database with prose, charts and tables."


def agent_card(url: str, description: str | None = None, examples: tuple[str, ...] = ()) -> AgentCard:
    # The client shows the description in its header and the examples in its empty state.
    skills = [
        AgentSkill(id="ask", name="Ask", description="Ask a question about the data.", tags=["data"],
                   examples=list(examples))
    ] if examples else []
    return AgentCard(
        name="Explorer",
        description=description or DEFAULT_DESCRIPTION,
        url=url,
        version="0.1.0",
        capabilities=AgentCapabilities(
            streaming=True,
            extensions=[get_a2ui_agent_extension("0.9", supported_catalog_ids=[EXPLORER_CATALOG_ID])],
        ),
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=skills,
    )


def build_app(
    answerer: Answerer,
    *,
    url: str,
    client_origin: str,
    conversations: Conversations | None = None,
    description: str | None = None,
    examples: tuple[str, ...] = (),
    drills: DrillDowns | None = None,
    model: str | None = None,
) -> Starlette:
    card = agent_card(url, description, examples)
    conversations = executor_conversations = conversations or Conversations()
    executor = ExplorerExecutor(answerer, executor_conversations, card, drills, model)
    task_store = InMemoryTaskStore()
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
        # New context IDs come from Conversations, so it can tell a new conversation from an
        # unknown one (parent spec §2.3).
        request_context_builder=SimpleRequestContextBuilder(
            task_store=task_store, context_id_generator=conversations.id_generator
        ),
    )
    app = A2AStarletteApplication(agent_card=card, http_handler=handler).build()
    # The browser calls the agent directly (ADR 0007).
    app.add_middleware(
        CORSMiddleware, allow_origins=[client_origin], allow_methods=["*"], allow_headers=["*"]
    )
    return app
