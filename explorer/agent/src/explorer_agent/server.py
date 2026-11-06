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
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from explorer_agent.budget import TurnBudget
from explorer_agent.catalog import EXPLORER_CATALOG_ID
from explorer_agent.executor import Answerer, BoardViews, Conversations, DrillDowns, ExplorerExecutor, SavedViews


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
    boards: BoardViews | None = None,
    saved: SavedViews | None = None,
    cors: bool = True,
    budget: TurnBudget | None = None,
) -> Starlette:
    """`saved` (Workspace mode) adds the conversation routes; `cors=False` leaves CORS to the
    Workspace router in front (B1)."""
    card = agent_card(url, description, examples)
    conversations = executor_conversations = conversations or Conversations(saved=saved.store if saved else None)
    if saved is not None:
        conversations.saved = saved.store
    executor = ExplorerExecutor(answerer, executor_conversations, card, drills, model, boards, saved, budget)
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
    app.state.executor = executor
    app.state.conversations = conversations
    if saved is not None:
        add_conversation_routes(app, executor, conversations, saved)
    if cors:
        # The browser calls the agent directly (ADR 0007).
        app.add_middleware(
            CORSMiddleware, allow_origins=[client_origin], allow_methods=["*"], allow_headers=["*"]
        )
    return app


def add_conversation_routes(app: Starlette, executor: ExplorerExecutor, conversations: Conversations,
                            saved: SavedViews) -> None:
    """The saved-Conversation routes of a Workspace (SPEC "HTTP API"). Row data never crosses
    them except inside a rebuilt Level's A2UI, built by the Macros from re-run Results."""

    def busy() -> JSONResponse:
        return JSONResponse({"error": "a model run is in progress"}, status_code=409)

    async def list_all(request: Request) -> Response:
        return JSONResponse(await saved.store.list())

    async def one(request: Request) -> Response:
        conv = await conversations.load(request.path_params["cid"])
        if conv is None:
            return JSONResponse({"error": "no such conversation"}, status_code=404)
        return JSONResponse(executor.view_of(conv))

    async def level(request: Request) -> Response:
        conv = await conversations.load(request.path_params["cid"])
        if conv is None:
            return JSONResponse({"error": "no such conversation"}, status_code=404)
        defaults = request.method == "POST"  # .../defaults changes what is saved: a POST (refuter F2)
        out = await executor.rebuild_level(conv, request.path_params["level"], defaults=defaults)
        if out is None:
            return JSONResponse({"error": "no such level"}, status_code=404)
        return JSONResponse(out)

    async def delete_one(request: Request) -> Response:
        cid = request.path_params["cid"]
        held = conversations.held(cid)
        if held is not None and held.busy:
            return busy()
        conversations.forget(cid)
        found = await saved.store.delete(cid)
        if not found and held is None:
            return JSONResponse({"error": "no such conversation"}, status_code=404)
        return Response(status_code=204)

    async def delete_all(request: Request) -> Response:
        if conversations.running:
            return busy()
        conversations.forget_all()
        await saved.store.delete_all()
        return Response(status_code=204)

    app.add_route("/conversations", list_all, methods=["GET"])
    app.add_route("/conversations", delete_all, methods=["DELETE"])
    app.add_route("/conversations/{cid}", one, methods=["GET"])
    app.add_route("/conversations/{cid}", delete_one, methods=["DELETE"])
    app.add_route("/conversations/{cid}/levels/{level}", level, methods=["GET"])
    app.add_route("/conversations/{cid}/levels/{level}/defaults", level, methods=["POST"])
