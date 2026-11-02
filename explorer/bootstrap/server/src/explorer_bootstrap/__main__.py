"""CLI: `python -m explorer_bootstrap report (--dataset-dir PATH | --schemas a,b) ...` (SPEC.md)."""

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

from explorer_bootstrap.report import OverlayDriftError, build_report, render_text
from explorer_bootstrap.stats import QUERY_CANCELED, open_report_pool

REPO_ROOT = Path(__file__).resolve().parents[5]  # genUI/
ENV_FILE = REPO_ROOT / ".env"
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


async def _report(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    dsn = env.get(URL_VAR, "").strip()
    if not dsn:
        raise _Fatal(f"{URL_VAR} is not set (put it in {ENV_FILE} or the environment)")
    if args.timeout_ms <= 0:
        raise _Fatal("--timeout-ms must be positive")
    dataset = None
    if args.dataset_dir is not None:
        try:
            dataset = load_dataset(args.dataset_dir)
        except ConfigError as e:
            raise _Fatal(str(e)) from None
        schemas = list(dataset.allowed_schemas)
    else:
        schemas = _schemas(args.schemas)
    try:
        pool = open_report_pool(dsn, args.timeout_ms)
        database = _database_label(dsn)
    except ValueError as e:
        raise _Fatal(str(e)) from None
    try:
        await pool.open(wait=True, timeout=CONNECT_TIMEOUT_S)
    except PoolTimeout:
        await pool.close()
        raise _Fatal(f"cannot connect to the database (check {URL_VAR} and that the database is running)") from None
    try:
        report = await build_report(
            pool, schemas, database=database, dataset=dataset, timeout_ms=args.timeout_ms, minmax=not args.no_minmax
        )
    except OverlayDriftError as e:
        raise _Fatal(str(e)) from None
    except psycopg.Error as e:
        if e.sqlstate == QUERY_CANCELED:
            raise _Fatal("a catalog query timed out; raise --timeout-ms") from None
        raise _Fatal(f"database error: {e.diag.message_primary or type(e).__name__}") from None
    finally:
        await pool.close()
    if not report.objects:
        raise _Fatal(f"no tables or views visible to this login in: {', '.join(schemas)}")
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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # psycopg's pool logs connection errors, which name the user; we print our own message instead.
    logging.getLogger("psycopg").setLevel(logging.CRITICAL)
    env_file = Path(os.environ.get("BOOTSTRAP_ENV_FILE") or ENV_FILE)
    load_dotenv(env_file, override=False)
    try:
        return asyncio.run(_report(args, os.environ))
    except _Fatal as e:
        sys.stderr.write(f"error: {e}\n")
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
