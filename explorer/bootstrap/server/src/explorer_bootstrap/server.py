"""Local HTTP server for the bootstrap UI (SPEC.md phase 2c). One person, in memory, 127.0.0.1 only.

It wraps the phase 2b functions. Claim results only ever come from the server's own runs.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from psycopg_pool import AsyncConnectionPool
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from explorer_agent.config import SCHEMA_NAME, ConfigError, Dataset, load_dataset
from explorer_agent.schema import SchemaCatalog
from explorer_agent.sql import SqlRunner

from explorer_bootstrap.boards import (
    board_files_problems,
    boards_runner,
    boards_state,
    check_boards,
    estimate_boards,
    run_boards,
)
from explorer_bootstrap.claims import verify_claims
from explorer_bootstrap.draft import Completion, DraftError, estimate_cost, run_draft
from explorer_bootstrap.draft_schema import claim_problems
from explorer_bootstrap.drafts import FILE_NAMES, DraftFileError, draft_dataset, new_draft, validate_files
from explorer_bootstrap.export import ExportError, export_dataset
from explorer_bootstrap.report import OverlayDriftError, build_report
from explorer_bootstrap.settings import ALLOWED_MODELS, SettingsError, load_draft_settings
from explorer_bootstrap.stats import visible_schemas

log = logging.getLogger(__name__)


@dataclass
class _Run:
    id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    error: str | None = None
    task: asyncio.Task | None = None
    kind: str = "draft"  # or "boards" (phase 3): one run at a time across both loops


@dataclass
class _State:
    report: dict[str, Any] | None = None
    draft: dict[str, Any] | None = None
    run: _Run | None = None
    boards: dict[str, Any] | None = None  # phase 3: the Boards state; a new draft clears it

    @property
    def running(self) -> bool:
        return self.run is not None and not self.run.done


class _Guard:
    """Refuse requests a page on another site could make: wrong Host (DNS rebinding), or a
    POST that isn't JSON (a cross-site JSON POST needs a CORS preflight, which we never answer)."""

    def __init__(self, app: ASGIApp, port: int):
        self.app = app
        self.hosts = {f"127.0.0.1:{port}".encode(), f"localhost:{port}".encode()}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            ok = headers.get(b"host", b"") in self.hosts
            if ok and scope["method"] not in ("GET", "HEAD"):
                ctype = headers.get(b"content-type", b"").split(b";")[0].strip().lower()
                ok = ctype == b"application/json"
            if not ok:
                await PlainTextResponse("forbidden", status_code=403)(scope, receive, send)
                return
        try:
            await self.app(scope, receive, send)
        except Exception:
            # Starlette has already sent the 500 from server_error() and re-raises for the server
            # to log; uvicorn would print the message and traceback, which may quote a query.
            pass


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


# A dataset name crosses the wire; the directory never does. Plain names only: no
# separators, no "..", so resolution cannot escape the server's datasets directory.
_DATASET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class DatasetError(ValueError):
    """A bad dataset name (400) or one the server cannot load."""


def list_datasets(datasets_root: Path) -> list[str]:
    """Dataset names the server can report on: subdirectories holding a dataset.toml."""
    try:
        entries = sorted(p.name for p in datasets_root.iterdir() if (p / "dataset.toml").is_file())
    except OSError:
        return []
    return [n for n in entries if _DATASET_NAME.fullmatch(n)]


def load_server_dataset(datasets_root: Path, name: object) -> Dataset:
    """Resolve a dataset name against the server's own datasets directory.

    The web UI has no filesystem access, so callers name a dataset; the directory
    stays on the server. Raises DatasetError with a message safe for the client."""
    if not isinstance(name, str) or not _DATASET_NAME.fullmatch(name):
        raise DatasetError("dataset must be a plain dataset name")
    if not (datasets_root / name).is_dir():
        raise DatasetError(f"unknown dataset: {name}")
    try:
        return load_dataset(datasets_root / name)
    except ConfigError as e:
        raise DatasetError(str(e)) from None


def _db_message(e: psycopg.Error) -> str:
    return f"database error: {e.diag.message_primary or type(e).__name__}"


async def _body(request: Request) -> dict[str, Any] | None:
    try:
        data = await request.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _files_ok(files: Any) -> bool:
    return isinstance(files, dict) and all(isinstance(files.get(k), str) for k in FILE_NAMES)


def _comments(raw: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list):
        return None
    out = []
    for c in raw:
        if not isinstance(c, dict):
            return None
        out.append({
            "object": c.get("object"), "column": c.get("column"), "comment": c.get("comment"),
            "kind": c.get("kind"), "accepted": bool(c.get("accepted")),
        })
    return out


def build_app(
    pool: AsyncConnectionPool,
    *,
    database: dict[str, str],
    env: Mapping[str, str],
    port: int,
    timeout_ms: int,
    datasets_root: Path,
    repo_root: Path,
    ui_dir: Path | None = None,
    completion: Completion | None = None,
) -> ASGIApp:
    state = _State()
    # Phase 3: a validate stores its text, then checks and previews it; overlapping ones (the UI's
    # debounce can fire again during a preview) must not finish out of order, nor race an export.
    boards_lock = asyncio.Lock()

    def with_validation(draft: dict[str, Any]) -> dict[str, Any]:
        return {"draft": draft, "validation": validate_files(draft)}

    async def connection(_request: Request) -> JSONResponse:
        try:
            schemas = await visible_schemas(pool)
        except psycopg.Error as e:
            return _err(_db_message(e), 500)
        return JSONResponse({**database, "schemas": schemas})

    async def list_dataset_names(_request: Request) -> JSONResponse:
        return JSONResponse({"datasets": list_datasets(datasets_root)})

    async def list_models(_request: Request) -> JSONResponse:
        """Models the server allows for drafting: the allowlist and the configured default.

        The UI builds its model picker from this instead of hardcoding the list."""
        default = env.get("BOOTSTRAP_MODEL", "").strip() or ALLOWED_MODELS[0]
        if default not in ALLOWED_MODELS:
            default = ALLOWED_MODELS[0]
        return JSONResponse({"models": list(ALLOWED_MODELS), "default": default})

    async def dump_schema(request: Request) -> JSONResponse:
        """The readable catalog (schemas, tables, columns, comments) without statistics.

        For looking at, and downloading, the database shape before running a report.
        `?schemas=a,b` narrows it; without it, every schema the login can use."""
        raw = request.query_params.get("schemas", "")
        if raw.strip():
            schemas = [s.strip() for s in raw.split(",") if s.strip()]
            if not schemas or any(not SCHEMA_NAME.fullmatch(s) for s in schemas):
                return _err("schemas must be a comma-separated list of schema names")
        else:
            try:
                schemas = [s["name"] for s in await visible_schemas(pool)]
            except psycopg.Error as e:
                return _err(_db_message(e), 500)
        try:
            objects = await SchemaCatalog(pool, schemas).objects()
        except psycopg.Error as e:
            return _err(_db_message(e), 500)
        by_schema: dict[str, list[dict[str, Any]]] = {s: [] for s in sorted(set(schemas))}
        for o in objects:
            by_schema[o.schema].append({
                "schema": o.schema, "name": o.name, "kind": o.kind, "comment": o.comment,
                "columns": [
                    {"name": c.name, "type": c.type, "nullable": c.nullable, "comment": c.comment}
                    for c in o.columns
                ],
            })
        return JSONResponse({"schemas": [{"name": s, "tables": by_schema[s]} for s in sorted(by_schema)]})

    async def report(request: Request) -> JSONResponse:
        if state.running:
            return _err("a draft is running; wait for it to finish", 409)
        body = await _body(request)
        params = body if body else {}
        has_schemas, has_dataset = "schemas" in params, "dataset" in params
        if has_schemas == has_dataset:
            return _err("give exactly one of schemas or dataset")
        dataset = None
        if has_dataset:
            try:
                dataset = load_server_dataset(datasets_root, params["dataset"])
            except DatasetError as e:
                return _err(str(e), 404 if str(e).startswith("unknown dataset") else 400)
            schemas: list[str] = list(dataset.allowed_schemas)
        else:
            schemas = params["schemas"]
            if not isinstance(schemas, list) or not schemas or not all(
                isinstance(s, str) and SCHEMA_NAME.fullmatch(s) for s in schemas
            ):
                return _err("schemas must be a non-empty list of schema names")
        try:
            built = await build_report(pool, schemas, database=database, dataset=dataset,
                                       timeout_ms=timeout_ms)
        except OverlayDriftError as e:
            return _err(str(e))
        except psycopg.Error as e:
            return _err(_db_message(e), 500)
        if not built.objects:
            return _err(f"no tables or views visible to this login in: {', '.join(schemas)}")
        state.report = built.to_dict()
        return JSONResponse(state.report)

    async def settings_for(request: Request):
        """Draft settings from a JSON object body; SettingsError for anything else."""
        body = await _body(request)
        if body is None:
            raise SettingsError("the body must be a JSON object")
        model, effort = body.get("model"), body.get("effort")
        if not isinstance(model, str | None) or not isinstance(effort, str | None):
            raise SettingsError("model and effort must be strings")
        return load_draft_settings(env, model=model or None, effort=effort or None)

    async def estimate(request: Request) -> JSONResponse:
        if state.report is None:
            return _err("run the report first")
        try:
            settings = await settings_for(request)
        except SettingsError as e:
            return _err(str(e))
        return JSONResponse(estimate_cost(state.report, settings))

    async def start_draft(request: Request) -> JSONResponse:
        if state.running:
            return _err("a draft is already running", 409)
        if state.report is None:
            return _err("run the report first")
        try:
            settings = await settings_for(request)
        except SettingsError as e:
            return _err(str(e))
        run = _Run(id=uuid.uuid4().hex[:12])
        report_now = state.report

        async def go() -> None:
            try:
                runner = SqlRunner(pool, report_now["schemas"], timeout_ms, settings.sql_row_cap)
                args, usage = await run_draft(report_now, runner, settings, completion=completion,
                                              on_event=run.events.append)
                draft = new_draft(report_now, args, model=settings.model, effort=settings.effort, usage=usage)
                draft["claims"] = await verify_claims(runner, draft["claims"])
                state.draft = draft
                state.boards = None  # Boards are drafted from the draft's files
            except DraftError as e:
                run.error = str(e)
            except Exception as e:  # never leak a traceback (or a query) to the page
                log.warning("draft run failed: %s", type(e).__name__)
                run.error = f"the draft failed unexpectedly ({type(e).__name__})"
            finally:
                run.done = True

        state.run = run
        run.task = asyncio.create_task(go())
        return JSONResponse({"run_id": run.id})

    async def draft_events(request: Request) -> JSONResponse:
        run = state.run
        if run is None or run.id != request.path_params["run_id"]:
            return _err("unknown run", 404)
        try:
            after = max(0, int(request.query_params.get("after", "0")))
        except ValueError:
            return _err("after must be an integer")
        out: dict[str, Any] = {"events": run.events[after:], "next": len(run.events), "done": run.done}
        if run.done:
            if run.error:
                out["error"] = run.error
            elif run.kind == "boards":
                out["boards"] = state.boards
            elif state.draft is not None:
                out.update(with_validation(state.draft))
        return JSONResponse(out)

    async def current_draft(_request: Request) -> JSONResponse:
        if state.draft is None:
            return _err("no draft yet", 404)
        return JSONResponse(with_validation(state.draft))

    async def verify(request: Request) -> JSONResponse:
        if state.draft is None:
            return _err("no draft yet", 404)
        body = await _body(request)
        claims = (body or {}).get("claims")
        problems = claim_problems(claims, extra_keys=frozenset({"dismissed", "result"}))
        if problems:
            return JSONResponse({"reasons": problems}, status_code=400)
        cleaned = [
            {"text": c["text"], "file": c["file"], "sql": c["sql"], "expect": c["expect"],
             "dismissed": bool(c.get("dismissed"))}
            for c in claims
        ]
        try:
            runner = SqlRunner(pool, state.draft["report"]["schemas"], timeout_ms,
                               load_draft_settings(env).sql_row_cap)
        except SettingsError as e:
            return _err(str(e))
        state.draft["claims"] = await verify_claims(runner, cleaned)
        return JSONResponse({"claims": state.draft["claims"]})

    def apply_edits(body: dict[str, Any] | None) -> str | None:
        if state.draft is None:
            return "no draft yet"
        files, comments = (body or {}).get("files"), _comments((body or {}).get("comments"))
        if not _files_ok(files) or comments is None:
            return "files must hold dataset_toml, notes_md and glossary_md strings; comments must be a list"
        state.draft["files"] = {k: files[k] for k in FILE_NAMES}
        state.draft["comments"] = comments
        return None

    async def validate(request: Request) -> JSONResponse:
        problem = apply_edits(await _body(request))
        if problem:
            return _err(problem, 404 if state.draft is None else 400)
        return JSONResponse(validate_files(state.draft))

    async def export(request: Request) -> JSONResponse:
        body = await _body(request)
        problem = apply_edits(body)
        if problem:
            return _err(problem, 404 if state.draft is None else 400)
        dismissed = body.get("dismissed", [])
        if not isinstance(dismissed, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in dismissed):
            return _err("dismissed must be a list of claim indexes")
        for i, claim in enumerate(state.draft["claims"]):
            claim["dismissed"] = i in dismissed
        force = body.get("force", False)
        if not isinstance(force, bool):
            return _err("force must be a boolean")
        boards, board_problems = None, []
        if state.boards is not None:
            async with boards_lock:
                try:
                    await recheck_boards()
                except DraftFileError:
                    pass  # export_dataset refuses with the files' own errors
                except SettingsError as e:
                    return _err(str(e))
                boards = [{"name": b["name"], "toml": b["toml"]} for b in state.boards["boards"]]
                board_problems = [*state.boards["set_problems"],
                                  *(p for ps in state.boards["problems"].values() for p in ps)]
        try:
            result = export_dataset(state.draft, str(body.get("name") or ""), str(body.get("target") or ""),
                                    datasets_root, force=force, boards=boards, board_problems=board_problems)
        except ExportError as e:
            return JSONResponse({"reasons": e.reasons}, status_code=422)
        try:
            shown = result.dest.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            shown = result.dest.name
        out: dict[str, Any] = {"path": shown, "overwrote": result.overwrote, "backup": None}
        if result.backup is not None:
            try:
                out["backup"] = result.backup.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                out["backup"] = result.backup.name
        return JSONResponse(out)

    # --- phase 3: Boards ---------------------------------------------------------------------

    async def recheck_boards(*, preview: bool = False) -> None:
        """The stored Boards checked (and previewed) against the draft's files as they are now.
        Raises DraftFileError when the files don't validate."""
        assert state.draft is not None and state.boards is not None
        settings = load_draft_settings(env)
        dataset = draft_dataset(state.draft)
        runner = boards_runner(pool, dataset, timeout_ms, settings.sql_row_cap)
        files = state.boards["boards"]
        check = await check_boards(files, dataset, runner, settings.max_entity_boards)
        if preview:
            state.boards = await boards_state(files, check, dataset, runner, state.draft["report"], settings,
                                              state.boards.get("usage"))
        else:
            state.boards.update(check.to_dict())

    async def boards_estimate(request: Request) -> JSONResponse:
        if state.draft is None:
            return _err("no draft yet", 404)
        try:
            settings = await settings_for(request)
            return JSONResponse(estimate_boards(state.draft, settings))
        except SettingsError as e:
            return _err(str(e))
        except DraftFileError as e:
            return JSONResponse({"reasons": str(e).split("; ")}, status_code=400)

    async def start_boards(request: Request) -> JSONResponse:
        if state.running:
            return _err("a draft is already running", 409)
        if state.draft is None:
            return _err("no draft yet", 404)
        try:
            settings = await settings_for(request)
            dataset = draft_dataset(state.draft)
        except SettingsError as e:
            return _err(str(e))
        except DraftFileError as e:
            return JSONResponse({"reasons": str(e).split("; ")}, status_code=400)
        if state.running:  # checked again: another start may have taken the slot during the await
            return _err("a draft is already running", 409)
        run = _Run(id=uuid.uuid4().hex[:12], kind="boards")
        draft_now = copy.deepcopy(state.draft)  # review edits during the run don't change its input

        async def go() -> None:
            try:
                runner = boards_runner(pool, dataset, timeout_ms, settings.sql_row_cap)
                result = await run_boards(draft_now, runner, settings, completion=completion,
                                          on_event=run.events.append)
                state.boards = await boards_state(result.files, result.check, dataset, runner,
                                                  draft_now["report"], settings, result.usage)
            except (DraftError, DraftFileError) as e:
                run.error = str(e)
            except Exception as e:  # never leak a traceback (or a query) to the page
                log.warning("boards run failed: %s", type(e).__name__)
                run.error = f"the Boards draft failed unexpectedly ({type(e).__name__})"
            finally:
                run.done = True

        state.run = run
        run.task = asyncio.create_task(go())
        return JSONResponse({"run_id": run.id})

    async def current_boards(_request: Request) -> JSONResponse:
        if state.boards is None:
            return _err("no Boards yet", 404)
        return JSONResponse(state.boards)

    async def validate_boards(request: Request) -> JSONResponse:
        if state.running:
            return _err("a draft is running; wait for it to finish", 409)
        if state.draft is None or state.boards is None:
            return _err("no Boards yet", 404)
        boards = ((await _body(request)) or {}).get("boards")
        problems = board_files_problems(boards)
        if problems:
            return JSONResponse({"reasons": problems}, status_code=400)
        async with boards_lock:  # in arrival order: the newest edit is the one stored
            if state.boards is None:
                return _err("no Boards yet", 404)
            state.boards["boards"] = [{"name": b["name"], "toml": b["toml"], "why": b["why"].strip()} for b in boards]
            try:
                await recheck_boards(preview=True)
            except DraftFileError as e:
                return JSONResponse({"reasons": str(e).split("; ")}, status_code=400)
            except SettingsError as e:
                return _err(str(e))
            return JSONResponse(state.boards)

    async def no_ui(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("The UI isn't built: run `npm ci && npm run build` in explorer/bootstrap/ui.\n")

    routes = [
        Route("/api/connection", connection),
        Route("/api/datasets", list_dataset_names),
        Route("/api/models", list_models),
        Route("/api/schema", dump_schema),
        Route("/api/report", report, methods=["POST"]),
        Route("/api/estimate", estimate, methods=["POST"]),
        Route("/api/draft", start_draft, methods=["POST"]),
        Route("/api/draft", current_draft, methods=["GET"]),
        Route("/api/draft/{run_id}/events", draft_events),
        Route("/api/claims/verify", verify, methods=["POST"]),
        Route("/api/validate", validate, methods=["POST"]),
        Route("/api/export", export, methods=["POST"]),
        Route("/api/boards/estimate", boards_estimate, methods=["POST"]),
        Route("/api/boards/draft", start_boards, methods=["POST"]),
        Route("/api/boards", current_boards, methods=["GET"]),
        Route("/api/boards/validate", validate_boards, methods=["POST"]),
    ]
    if ui_dir is not None and (ui_dir / "index.html").is_file():
        routes.append(Mount("/", StaticFiles(directory=ui_dir, html=True)))
    else:
        routes.append(Route("/", no_ui))

    async def server_error(_request: Request, exc: Exception) -> JSONResponse:
        log.warning("request failed: %s", type(exc).__name__)  # the class only: messages may quote SQL
        return _err(f"unexpected {type(exc).__name__}", 500)

    app = Starlette(routes=routes, exception_handlers={Exception: server_error})
    app.state.bootstrap = state
    return _Guard(app, port)
