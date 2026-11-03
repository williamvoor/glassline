"""`python -m explorer_agent`: start the agent against the dataset database.

Loads the repo root's `.env` (or `EXPLORER_ENV_FILE`) without overriding the environment, checks the
login is read-only, checks the Dataset's Boards, builds the system prompt once, and serves A2A on
AGENT_HOST:AGENT_PORT. A configuration or startup problem prints one line, `explorer: <message>`,
and exits 1.

`python -m explorer_agent boards` checks and renders each Board cold, printing its time, and exits
1 when one misses the cold target (SPEC §18).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
import time
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from psycopg_pool import PoolTimeout

from explorer_agent.boards import (
    BOARD_COLD_TARGET_S,
    BoardError,
    BoardRenderer,
    build_board,
    check_board,
    instance_results,
)
from explorer_agent.config import ConfigError, Settings, load_settings
from explorer_agent.drill import Prefetcher
from explorer_agent.executor import BoardViews, Conversations, DrillDowns
from explorer_agent.llm import LlmLoop, LoopConfig
from explorer_agent.macros import PANEL_UNDRAWABLE
from explorer_agent.prompt import build_system_prompt, dataset_context
from explorer_agent.schema import SchemaCatalog, overlay_problems, render_schema_section
from explorer_agent.server import build_app
from explorer_agent.sql import SqlRunner, StartupError, open_pool
from explorer_agent.ui import UiPipeline

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
    )


async def _check_boards(settings: Settings, runner: SqlRunner, pipeline: UiPipeline) -> None:
    """Every Board's queries as `LIMIT 0` and its layout's columns (SPEC §18). No rows fetched."""
    try:
        for board in settings.dataset.boards.values():
            await check_board(board, runner, pipeline)
    except BoardError as e:
        raise ConfigError(str(e)) from None


async def _open_pool(settings: Settings):
    pool = open_pool(settings.database_url)
    try:
        await pool.open(wait=True, timeout=DB_CONNECT_TIMEOUT_S)
    except PoolTimeout:
        raise _Fatal("cannot connect to the database (check DATABASE_URL and that the database is running)") from None
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
            start = time.perf_counter()
            outcomes = await renderer.outcomes(board, fresh=True)
            results, failed_states = instance_results(outcomes, iter(f"r{i}" for i in range(1, 10_000)).__next__)
            ui = build_board(board, results, failed_states, pipeline, "turn-0-1-check")
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


async def _serve(settings: Settings) -> None:
    pool = await _open_pool(settings)
    renderer: BoardRenderer | None = None
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
        loop = LlmLoop(LoopConfig.from_settings(settings), prompt, pipeline, runner, catalog)
        url = f"http://{settings.agent_host}:{settings.agent_port}"
        prefetcher = Prefetcher(
            runner, rows=settings.drill_prefetch_rows, timeout_ms=settings.drill_prefetch_timeout_ms,
            max_held=settings.max_prefetched,
        )
        conversations = Conversations(
            max_action_sources=settings.max_action_sources, max_pending_pairs=settings.max_pending_pairs
        )
        app = build_app(
            loop, url=url, client_origin=settings.client_origin, conversations=conversations,
            description=settings.dataset.description, examples=settings.dataset.examples,
            drills=DrillDowns(runner, pipeline, prefetcher, settings.rows_to_model), model=settings.model,
            boards=BoardViews(renderer := _renderer(settings, runner), pipeline) if settings.dataset.boards else None,
        )
        server = uvicorn.Server(
            uvicorn.Config(app, host=settings.agent_host, port=settings.agent_port, log_level="warning")
        )
        task = asyncio.create_task(server.serve())
        while not server.started:
            if task.done():
                await task
                raise _Fatal(f"could not listen on {settings.agent_host}:{settings.agent_port}")
            await asyncio.sleep(0.05)
        print(f"explorer: listening on {url}", flush=True)
        if renderer is not None:
            renderer.start_warm()  # the first view of every Conversation is cached
        await task
    finally:
        if renderer is not None:
            renderer.stop()
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in ([], ["boards"]):
        print("usage: python -m explorer_agent [boards]", file=sys.stderr)
        return 2
    env_file = Path(os.environ.get("EXPLORER_ENV_FILE") or ENV_FILE)
    load_dotenv(env_file, override=False)
    try:
        settings = load_settings(os.environ, EXPLORER_ROOT)
        _configure_logging(settings.log_level)
        if args == ["boards"]:
            return asyncio.run(_boards_check(settings))
        asyncio.run(_serve(settings))
    except (ConfigError, StartupError, _Fatal) as e:
        print(f"explorer: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
