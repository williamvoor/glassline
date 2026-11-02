"""CLI: `python -m explorer_bootstrap {report,draft,verify,export} ...` (SPEC.md)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import PoolTimeout

from explorer_agent.config import SCHEMA_NAME, ConfigError, load_dataset
from explorer_agent.sql import SqlRunner

from explorer_bootstrap.claims import export_blockers, verify_claims
from explorer_bootstrap.draft import Completion, DraftError, run_draft
from explorer_bootstrap.drafts import DraftFileError, draft_json, load_draft, new_draft, validate_files
from explorer_bootstrap.export import ExportError, export_dataset
from explorer_bootstrap.report import OverlayDriftError, build_report, render_text
from explorer_bootstrap.settings import SettingsError, load_draft_settings
from explorer_bootstrap.stats import QUERY_CANCELED, open_report_pool

REPO_ROOT = Path(__file__).resolve().parents[5]  # genUI/
ENV_FILE = REPO_ROOT / ".env"
DATASETS_ROOT = REPO_ROOT / "explorer" / "datasets"
UI_DIST = REPO_ROOT / "explorer" / "bootstrap" / "ui" / "dist"
# Tests set this to a stub (explorer_agent.testing.stub_model); None means litellm.acompletion.
COMPLETION: Completion | None = None
URL_VAR = "BOOTSTRAP_DATABASE_URL"
CONNECT_TIMEOUT_S = 10.0

EXIT_OK, EXIT_CHECK_FAILED, EXIT_USAGE = 0, 1, 2


class _Fatal(Exception):
    pass


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="explorer_bootstrap")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("report", help="report comment coverage, statistics and context size")
    which = r.add_mutually_exclusive_group(required=True)
    which.add_argument("--dataset-dir", type=Path, help="an existing dataset directory (with dataset.toml)")
    which.add_argument("--schemas", help="comma-separated schema names, for a database with no dataset yet")
    r.add_argument("--json", type=Path, dest="json_out", help="also write the full report as JSON here")
    r.add_argument("--no-minmax", action="store_true", help="skip min/max queries")
    r.add_argument("--timeout-ms", type=int, default=5000, help="statement_timeout per query (default 5000)")
    r.add_argument("--check", action="store_true", help="exit 1 if a comment is missing or the context is over target")

    d = sub.add_parser("draft", help="draft dataset files, comments and claims with the model")
    which = d.add_mutually_exclusive_group(required=True)
    which.add_argument("--dataset-dir", type=Path, help="an existing dataset directory (with dataset.toml)")
    which.add_argument("--schemas", help="comma-separated schema names")
    d.add_argument("--out", type=Path, required=True, help="where to write draft.json")
    d.add_argument("--model", help="meta/muse-wonder-1.0 (default) or meta/muse-spark-1.3")
    d.add_argument("--effort", help="reasoning effort (default low)")
    d.add_argument("--timeout-ms", type=int, default=5000, help="statement_timeout per query (default 5000)")

    v = sub.add_parser("verify", help="rerun every claim in a draft and rewrite it")
    v.add_argument("--draft", type=Path, required=True)
    v.add_argument("--timeout-ms", type=int, default=5000, help="statement_timeout per query (default 5000)")

    e = sub.add_parser("export", help="write a reviewed draft to a new dataset directory")
    e.add_argument("--draft", type=Path, required=True)
    e.add_argument("--name", required=True, help="the new dataset directory's name")
    e.add_argument("--target", required=True, choices=("toml", "sql"), help="comments.toml (overlay) or comments.sql")
    e.add_argument("--root", type=Path, default=DATASETS_ROOT, help="parent directory (default explorer/datasets)")

    s = sub.add_parser("serve", help="run the local UI and its API on 127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--timeout-ms", type=int, default=5000, help="statement_timeout per query (default 5000)")
    return p


def _database_label(dsn: str) -> dict[str, str]:
    """Host and database name only: never the user or password."""
    info = conninfo_to_dict(dsn)
    return {"host": str(info.get("host") or "localhost"), "name": str(info.get("dbname") or "")}


def _schemas(raw: str) -> list[str]:
    names = [s.strip() for s in raw.split(",") if s.strip()]
    if not names:
        raise _Fatal("--schemas needs at least one schema name")
    for s in names:
        if not SCHEMA_NAME.fullmatch(s):
            raise _Fatal(f"invalid schema name: {s!r}")
    return names


def _selection(args: argparse.Namespace):
    if args.timeout_ms <= 0:
        raise _Fatal("--timeout-ms must be positive")
    if args.dataset_dir is not None:
        try:
            dataset = load_dataset(args.dataset_dir)
        except ConfigError as e:
            raise _Fatal(str(e)) from None
        return dataset, list(dataset.allowed_schemas)
    return None, _schemas(args.schemas)


async def _open(env: Mapping[str, str], timeout_ms: int):
    dsn = env.get(URL_VAR, "").strip()
    if not dsn:
        raise _Fatal(f"{URL_VAR} is not set (put it in {ENV_FILE} or the environment)")
    try:
        pool = open_report_pool(dsn, timeout_ms)
        database = _database_label(dsn)
    except ValueError as e:
        raise _Fatal(str(e)) from None
    try:
        await pool.open(wait=True, timeout=CONNECT_TIMEOUT_S)
    except PoolTimeout:
        await pool.close()
        raise _Fatal(f"cannot connect to the database (check {URL_VAR} and that the database is running)") from None
    return pool, database


def _db_fatal(e: psycopg.Error) -> _Fatal:
    if e.sqlstate == QUERY_CANCELED:
        return _Fatal("a catalog query timed out; raise --timeout-ms")
    return _Fatal(f"database error: {e.diag.message_primary or type(e).__name__}")


async def _build(pool, schemas, database, dataset, timeout_ms: int, minmax: bool):
    try:
        report = await build_report(pool, schemas, database=database, dataset=dataset, timeout_ms=timeout_ms,
                                    minmax=minmax)
    except OverlayDriftError as e:
        raise _Fatal(str(e)) from None
    except psycopg.Error as e:
        raise _db_fatal(e) from None
    if not report.objects:
        raise _Fatal(f"no tables or views visible to this login in: {', '.join(schemas)}")
    return report


async def _report(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    dataset, schemas = _selection(args)
    pool, database = await _open(env, args.timeout_ms)
    try:
        report = await _build(pool, schemas, database, dataset, args.timeout_ms, not args.no_minmax)
    finally:
        await pool.close()
    text = json.dumps(report.to_dict(), indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    sys.stdout.write(render_text(report))
    if args.json_out is not None:
        try:
            args.json_out.write_text(text, encoding="utf-8")
        except OSError as e:
            raise _Fatal(f"cannot write {args.json_out}: {e.strerror}") from None
        sys.stdout.write(f"\nJSON written to {args.json_out}\n")
    if args.check:
        s = report.summary
        if report.missing_comments() or s.context_chars > s.context_target:
            return EXIT_CHECK_FAILED
    return EXIT_OK


def _write(path: Path, text: str) -> None:
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as e:
        raise _Fatal(f"cannot write {path}: {e.strerror}") from None


def _print_claims(claims: list[dict]) -> None:
    sys.stdout.write(f"\nClaims ({len(claims)}):\n")
    for i, c in enumerate(claims, 1):
        r = c.get("result") or {}
        mark = " (dismissed)" if c.get("dismissed") else ""
        sys.stdout.write(f"  {i}. [{r.get('state', 'not checked')}]{mark} {c['text']}\n")
        if r.get("message"):
            sys.stdout.write(f"     {r['message']}\n")


def _print_validation(validation: dict) -> None:
    if validation["errors"]:
        sys.stdout.write("\nFiles do not validate:\n" + "".join(f"  {e}\n" for e in validation["errors"]))
    else:
        sys.stdout.write(f"\nFiles validate. Context size: {validation['context_chars']:,} chars\n")


def _print_event(event: dict) -> None:
    if event["type"] == "sql":
        sys.stdout.write(f"  run_sql ({event['purpose']})\n")
    elif event["type"] == "call":
        u = event["usage"]
        sys.stdout.write(f"  model call: {u['prompt_tokens']} prompt ({u['cached_tokens']} cached), "
                         f"{u['completion_tokens']} completion, ${u['cost_usd']:.4f}\n")
    elif event["type"] == "invalid_draft":
        sys.stdout.write(f"  draft rejected ({len(event['problems'])} problems); the model is fixing it\n")
    sys.stdout.flush()


async def _draft(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    try:
        settings = load_draft_settings(env, model=args.model, effort=args.effort)
    except SettingsError as e:
        raise _Fatal(str(e)) from None
    dataset, schemas = _selection(args)
    pool, database = await _open(env, args.timeout_ms)
    try:
        report = (await _build(pool, schemas, database, dataset, args.timeout_ms, True)).to_dict()
        runner = SqlRunner(pool, schemas, args.timeout_ms, settings.sql_row_cap)
        sys.stdout.write(f"Drafting with {settings.model} (effort {settings.effort})\n")
        try:
            draft_args, usage = await run_draft(report, runner, settings, completion=COMPLETION,
                                                on_event=_print_event)
        except DraftError as e:
            raise _Fatal(str(e)) from None
        draft = new_draft(report, draft_args, model=settings.model, effort=settings.effort, usage=usage)
        draft["claims"] = await verify_claims(runner, draft["claims"])
    finally:
        await pool.close()
    _write(args.out, draft_json(draft))
    _print_claims(draft["claims"])
    _print_validation(validate_files(draft))
    t = usage["totals"]
    sys.stdout.write(f"\nUsage: {t['calls']} calls, {t['prompt_tokens']} prompt ({t['cached_tokens']} cached), "
                     f"{t['completion_tokens']} completion tokens, ${t['cost_usd']:.4f}\n")
    sys.stdout.write(f"Draft written to {args.out}\n")
    return EXIT_OK


def _load(path: Path) -> dict:
    try:
        return load_draft(path)
    except DraftFileError as e:
        raise _Fatal(str(e)) from None


async def _verify(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    if args.timeout_ms <= 0:
        raise _Fatal("--timeout-ms must be positive")
    draft = _load(args.draft)
    try:
        row_cap = load_draft_settings(env).sql_row_cap
    except SettingsError as e:
        raise _Fatal(str(e)) from None
    pool, _ = await _open(env, args.timeout_ms)
    try:
        runner = SqlRunner(pool, draft["report"]["schemas"], args.timeout_ms, row_cap)
        draft["claims"] = await verify_claims(runner, draft["claims"])
    finally:
        await pool.close()
    _write(args.draft, draft_json(draft))
    _print_claims(draft["claims"])
    _print_validation(validate_files(draft))
    blockers = export_blockers(draft["claims"])
    if blockers:
        sys.stdout.write(f"\n{len(blockers)} claim(s) block export: fix and verify again, or dismiss them.\n")
        return EXIT_CHECK_FAILED
    return EXIT_OK


def _export(args: argparse.Namespace) -> int:
    draft = _load(args.draft)
    try:
        dest = export_dataset(draft, args.name, args.target, args.root)
    except ExportError as e:
        sys.stdout.write("Export refused:\n" + "".join(f"  {r}\n" for r in e.reasons))
        return EXIT_CHECK_FAILED
    sys.stdout.write(f"Exported to {dest}\n")
    if args.target == "sql":
        sys.stdout.write("Apply comments.sql as the objects' owner, then start the explorer with this dataset.\n")
    return EXIT_OK


async def _serve(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    import uvicorn

    from explorer_bootstrap.server import build_app

    if args.timeout_ms <= 0 or not 0 < args.port < 65536:
        raise _Fatal("--timeout-ms and --port must be positive (port below 65536)")
    try:
        load_draft_settings(env)  # fail now on a bad BOOTSTRAP_* setting, not on the first draft
    except SettingsError as e:
        raise _Fatal(str(e)) from None
    pool, database = await _open(env, args.timeout_ms)
    try:
        app = build_app(
            pool, database=database, env=env, port=args.port, timeout_ms=args.timeout_ms,
            datasets_root=DATASETS_ROOT, repo_root=REPO_ROOT, ui_dir=UI_DIST, completion=COMPLETION,
        )
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning"))
        sys.stdout.write(f"bootstrap: http://127.0.0.1:{args.port}/ (database {database['name']} on "
                         f"{database['host']})\n")
        sys.stdout.flush()
        await server.serve()
    finally:
        await pool.close()
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # psycopg's pool logs connection errors, which name the user; we print our own message instead.
    logging.getLogger("psycopg").setLevel(logging.CRITICAL)
    env_file = Path(os.environ.get("BOOTSTRAP_ENV_FILE") or ENV_FILE)
    load_dotenv(env_file, override=False)
    try:
        if args.command == "export":
            return _export(args)
        command = {"report": _report, "draft": _draft, "verify": _verify, "serve": _serve}[args.command]
        return asyncio.run(command(args, os.environ))
    except _Fatal as e:
        sys.stderr.write(f"error: {e}\n")
        return EXIT_USAGE
    except Exception as e:  # never a traceback (it could hold query text); never exit 1, which means "refused"
        sys.stderr.write(f"error: unexpected {type(e).__name__}; rerun with a smaller scope or report it\n")
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
