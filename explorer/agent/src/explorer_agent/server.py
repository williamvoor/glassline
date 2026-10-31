"""Agent card and Starlette app. The real entry point (`python -m explorer_agent`) comes with config."""

from __future__ import annotations

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard
from a2ui.a2a import get_a2ui_agent_extension
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware

from explorer_agent.catalog import EXPLORER_CATALOG_ID
from explorer_agent.executor import Answerer, Conversations, ExplorerExecutor
from explorer_agent.ui import UiPipeline


def agent_card(url: str) -> AgentCard:
    return AgentCard(
        name="Explorer",
        description="Answers questions about a database with prose, charts and tables.",
        url=url,
        version="0.1.0",
        capabilities=AgentCapabilities(
            streaming=True,
            extensions=[get_a2ui_agent_extension("0.9", supported_catalog_ids=[EXPLORER_CATALOG_ID])],
        ),
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=[],
    )


def build_app(
    answerer: Answerer,
    *,
    url: str,
    client_origin: str,
    pipeline: UiPipeline | None = None,
    conversations: Conversations | None = None,
) -> Starlette:
    card = agent_card(url)
    executor = ExplorerExecutor(answerer, conversations or Conversations(), pipeline or UiPipeline(), card)
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore())
    app = A2AStarletteApplication(agent_card=card, http_handler=handler).build()
    # The browser calls the agent directly (ADR 0007).
    app.add_middleware(
        CORSMiddleware, allow_origins=[client_origin], allow_methods=["*"], allow_headers=["*"]
    )
    return app
