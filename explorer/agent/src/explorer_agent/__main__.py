"""`python -m explorer_agent`: serve the Workspaces in `EXPLORER_DATA_DIR`.

Loads the repo root's `.env` (or `EXPLORER_ENV_FILE`) without overriding the environment, reads the
Workspace list in `EXPLORER_DATA_DIR` (required: there is one mode, ADR 0048), builds each
Workspace (read-only login check, Boards check, system prompt), mounts each available one under
`/w/<name>/`, serves `/workspaces` on AGENT_HOST:AGENT_PORT, and saves Conversations. A
configuration problem prints one line, `explorer: <message>`, and exits 1.

`python -m explorer_agent boards <workspace>` checks and renders each of that Workspace's Boards
cold, printing its time, and exits 1 when one misses the cold target (SPEC §18).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from psycopg_pool import PoolTimeout

from explorer_agent.boards import (
    BOARD_COLD_TARGET_S,
    BoardError,
    BoardRenderer,
    OptionsError,
    build_board,
    check_board,
    instance_results,
)
from explorer_agent.budget import TurnBudget
from explorer_agent.config import (
    ConfigError,
    ProcessSettings,
    Settings,
    WorkspaceEntry,
    WorkspaceUnavailable,
    named_workspace_settings,
    process_settings,
    required_data_dir,
)
from explorer_agent.drill import Prefetcher
from explorer_agent.executor import BoardViews, Conversations, DrillDowns, SavedViews
from explorer_agent.llm import LlmLoop, LoopConfig
from explorer_agent.macros import PANEL_UNDRAWABLE
from explorer_agent.prompt import build_system_prompt, dataset_context
from explorer_agent.saved import SavedStore
from explorer_agent.schema import SchemaCatalog, overlay_problems, render_schema_section
from explorer_agent.server import build_app
from explorer_agent.sql import NO_TABLES_PREFIX, SqlRunner, StartupError, open_pool
from explorer_agent.ui import UiPipeline
from explorer_agent.workspaces import Built, Registry, workspace_app

EXPLORER_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = EXPLORER_ROOT.parent / ".env"  # the repo root's .env holds the real settings
# These can log request headers or bodies (prompts, history) at DEBUG or INFO.
QUIET_LOGGERS = ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "openai", "httpx", "httpcore", "urllib3", "aiohttp")
DB_CONNECT_TIMEOUT_S = 10

log = logging.getLogger("explorer_agent")


class _Fatal(Exception):
    """A startup failure to report as one line."""


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _renderer(settings: Settings, runner: SqlRunner) -> BoardRenderer:
    return BoardRenderer(
        settings.dataset.boards, runner, concurrency=settings.board_query_concurrency,
        row_cap=settings.board_row_cap, ttl_s=settings.board_cache_ttl_s, max_cache=settings.max_board_cache,
        max_options=settings.max_control_options,
    )


async def _check_boards(settings: Settings, runner: SqlRunner, pipeline: UiPipeline) -> None:
    """Every Board's queries as `LIMIT 0` and its layout's columns (SPEC §18). No rows fetched."""
    try:
        for board in settings.dataset.boards.values():
            await check_board(board, runner, pipeline)
    except BoardError as e:
        raise ConfigError(str(e)) from None


async def _open_pool(settings: Settings, timeout_s: float | None = None):
    pool = open_pool(settings.database_url)
    try:
        await pool.open(wait=True, timeout=timeout_s or DB_CONNECT_TIMEOUT_S)
    except PoolTimeout:
        await pool.close()  # it would keep retrying in the background
        raise _Fatal("cannot connect to the database (check the login and that the database is running)") from None
    return pool


async def _boards_check(settings: Settings, target_s: float = BOARD_COLD_TARGET_S) -> int:
    """`boards`: check, then render each Board cold (no cache: queries and the layout build) and
    time it against the cold target."""
    pool = await _open_pool(settings)
    try:
        runner = SqlRunner(pool, settings.dataset.allowed_schemas, settings.sql_timeout_ms, settings.sql_row_cap)
        await runner.check_privileges()
        pipeline = UiPipeline.from_settings(settings)
        await _check_boards(settings, runner, pipeline)
        if not settings.dataset.boards:
            print("explorer: the dataset has no Boards")
            return 0
        renderer = _renderer(settings, runner)
        slow = []
        for board in settings.dataset.boards.values():
            if board.key:  # rendered per clicked row: the startup check covered it (SPEC §18 slice 3)
                print(f"skip {board.name}: an Entity Board renders per row; not timed")
                continue
            start = time.perf_counter()
            try:
                options, state, bound = await renderer.defaults(board, fresh=True)
            except OptionsError as e:  # a Control's option query failed when rendered
                slow.append(board.name)
                print(f"FAIL {board.name}: {time.perf_counter() - start:.2f} s cold, failed queries: {e}")
                continue
            outcomes = await renderer.outcomes(board, bound, fresh=True)
            results, failed_states = instance_results(outcomes, iter(f"r{i}" for i in range(1, 10_000)).__next__)
            ui = build_board(board, results, failed_states, pipeline, "turn-0-1-check", options=options, state=state)
            seconds = time.perf_counter() - start
            failed = list(failed_states)
            undrawn = sum(1 for m in ui.messages for c in (m.get("updateComponents") or {}).get("components", [])
                          if c.get("component") == "Text" and c.get("text") == PANEL_UNDRAWABLE)
            status = "ok" if seconds < target_s and not failed and not undrawn else "FAIL"
            if status == "FAIL":
                slow.append(board.name)
            extra = f", failed queries: {', '.join(failed)}" if failed else ""
            extra += f", panels not drawn: {undrawn}" if undrawn else ""
            print(f"{status} {board.name}: {seconds:.2f} s cold (target < {target_s} s){extra}")
        return 1 if slow else 0
    finally:
        await pool.close()


@dataclass
class Running:
    """One Dataset's agent: its app and what to start and stop with it."""

    app: Any
    pool: Any
    renderer: BoardRenderer | None
    conversations: Conversations
    saved: SavedStore | None

    def started(self) -> None:
        if self.renderer is not None:
            self.renderer.start_warm()  # the first view of every Conversation is cached

    async def close(self) -> None:
        if self.renderer is not None:
            self.renderer.stop()
        try:
            if self.saved is not None:
                await self.saved.close()
        finally:
            await self.pool.close()


def turn_budget(limit: int, contact: str | None) -> TurnBudget | None:
    return TurnBudget(limit, contact=contact) if limit else None


async def build_workspace(settings: Settings, url: str, *, saved_path: Path,
                          connect_timeout_s: float | None = None, completion: Any = None,
                          budget: TurnBudget | None = None) -> Running:
    """Everything one Workspace needs, checked (ADR 0039): the pool, the read-only check, the
    schema and its overlay, the Boards, the prompt and the app, saving Conversations at
    `saved_path`. CORS is the router's (B1). `budget` is the process's shared one. The pool is
    closed on failure."""
    pool = await _open_pool(settings, connect_timeout_s)
    try:
        schemas = settings.dataset.allowed_schemas
        runner = SqlRunner(pool, schemas, settings.sql_timeout_ms, settings.sql_row_cap)
        await runner.check_privileges()
        overlay = settings.dataset.comment_overlay
        catalog = SchemaCatalog(pool, schemas, overlay)
        objects = await catalog.objects()
        problems = overlay_problems(objects, overlay)
        if problems:
            raise ConfigError(f"comment overlay {overlay.path}: {'; '.join(problems)}")
        section = render_schema_section(objects)
        pipeline = UiPipeline.from_settings(settings)
        await _check_boards(settings, runner, pipeline)
        prompt = build_system_prompt(pipeline.format, dataset_context(settings.dataset, section))
        log.info(
            "system prompt: %d chars, sha256 %s, format %s, dataset %s",
            len(prompt), hashlib.sha256(prompt.encode()).hexdigest()[:16], settings.format, settings.dataset.name,
        )
        loop = LlmLoop(LoopConfig.from_settings(settings), prompt, pipeline, runner, catalog, completion)
        prefetcher = Prefetcher(
            runner, rows=settings.drill_prefetch_rows, timeout_ms=settings.drill_prefetch_timeout_ms,
            max_held=settings.max_prefetched,
        )
        store = SavedStore(saved_path)
        conversations = Conversations(
            max_action_sources=settings.max_action_sources, max_pending_pairs=settings.max_pending_pairs,
            board_rows_to_model=settings.board_rows_to_model, board_pair_max_chars=settings.board_pair_max_chars,
            max_board_instances=settings.max_board_instances, saved=store,
        )
        renderer = _renderer(settings, runner) if settings.dataset.boards else None
        app = build_app(
            loop, url=url, client_origin=settings.client_origin, conversations=conversations,
            description=settings.dataset.description, examples=settings.dataset.examples,
            drills=DrillDowns(runner, pipeline, prefetcher, settings.rows_to_model), model=settings.model,
            boards=BoardViews(renderer, pipeline, settings.board_prefetch_rows) if renderer is not None else None,
            saved=SavedViews(store, runner, pipeline), cors=False, budget=budget,
        )
        return Running(app, pool, renderer, conversations, store)
    except BaseException:
        await pool.close()
        raise


async def _listen(app: Any, settings: Settings, url: str) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    server = uvicorn.Server(uvicorn.Config(app, host=settings.agent_host, port=settings.agent_port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            await task
            raise _Fatal(f"could not listen on {settings.agent_host}:{settings.agent_port}")
        await asyncio.sleep(0.05)
    print(f"explorer: listening on {url}", flush=True)
    return server, task


async def _workspace_builder(entry: WorkspaceEntry, settings: Settings, url: str, saved_path: Path, *,
                             connect_timeout_s: float | None = None, completion: Any = None,
                             budget: TurnBudget | None = None) -> Built:
    """One Workspace for the registry: a failed check becomes its reason (no DSN in it)."""
    try:
        running = await build_workspace(settings, url, saved_path=saved_path,
                                        connect_timeout_s=connect_timeout_s, completion=completion, budget=budget)
    except _Fatal:
        raise WorkspaceUnavailable("database unreachable") from None
    except StartupError as e:
        if str(e).startswith(NO_TABLES_PREFIX):  # a Dataset problem, not a write right (refuter F6)
            raise WorkspaceUnavailable(f"dataset invalid: {e}") from None
        raise WorkspaceUnavailable("login can write") from None
    except ConfigError as e:
        raise WorkspaceUnavailable(f"dataset invalid: {e}") from None
    return Built(running.app, running.close, settings.dataset.description,
                 lambda: running.conversations.running, running.started, running.pool)


def workspace_builder(base: ProcessSettings, *, completion: Any = None,
                      connect_timeout_s: float | None = None) -> Any:
    """The Registry's builder for Workspace mode: every Workspace shares one daily budget, built
    from the process's MAX_TURNS_PER_DAY and TURN_LIMIT_CONTACT. `completion(name)` gives a
    Workspace's stub model in tests."""
    budget = turn_budget(base.max_turns_per_day, base.turn_limit_contact)

    async def builder(entry: WorkspaceEntry, settings: Settings, ws_url: str, saved_path: Path) -> Built:
        return await _workspace_builder(entry, settings, ws_url, saved_path, budget=budget,
                                        connect_timeout_s=connect_timeout_s,
                                        completion=completion(entry.name) if completion else None)

    return builder


async def _serve_workspaces(env: Mapping[str, str], data: Path, base: Settings) -> None:
    """Workspace mode (SPEC phase 1): every Workspace mounted under /w/<name>/, the list at
    /workspaces. Starts even when none is available."""
    url = f"http://{base.agent_host}:{base.agent_port}"
    public = base.agent_public_url.rstrip("/") if base.agent_public_url else url  # what the listing and cards name
    registry = Registry(data, EXPLORER_ROOT.parent, EXPLORER_ROOT, env, workspace_builder(base), public)
    await registry.start()
    try:
        app = workspace_app(registry, base.client_origin, base.agent_host, base.agent_port)
        _, task = await _listen(app, base, url)
        await task
    finally:
        await registry.close()


USAGE = "usage: python -m explorer_agent [boards <workspace>]"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not (args == [] or (len(args) == 2 and args[0] == "boards")):
        print(USAGE, file=sys.stderr)
        return 2
    env_file = Path(os.environ.get("EXPLORER_ENV_FILE") or ENV_FILE)
    load_dotenv(env_file, override=False)
    try:
        if args:
            settings = named_workspace_settings(os.environ, args[1], EXPLORER_ROOT.parent, EXPLORER_ROOT)
            _configure_logging(settings.log_level)
            return asyncio.run(_boards_check(settings))
        data = required_data_dir(os.environ, EXPLORER_ROOT.parent)
        base = process_settings(os.environ)
        _configure_logging(base.log_level)
        asyncio.run(_serve_workspaces(os.environ, data, base))
    except (ConfigError, StartupError, _Fatal) as e:
        print(f"explorer: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
