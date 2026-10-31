"""`uv run austender <command>`"""

from __future__ import annotations

import argparse
import sys

from austender import db, ingest
from austender.api import AusTenderClient
from austender.config import Settings, load_env_file
from austender.mart import rebuild


def _conn(settings: Settings):
    return db.connect(**settings.loader_connect_kwargs(), autocommit=True)


def _client(settings: Settings) -> AusTenderClient:
    return AusTenderClient(user_agent=settings.user_agent, min_interval_s=settings.min_interval_s)


def cmd_migrate(args: argparse.Namespace, settings: Settings) -> int:
    with _conn(settings) as conn:
        db.ensure_roles(
            conn, loader_password=settings.loader_password, explorer_ro_password=settings.explorer_ro_password
        )
        applied = db.migrate(conn)
    print(f"applied: {', '.join(applied)}" if applied else "up to date")
    return 0


def cmd_fetch_month(args: argparse.Namespace, settings: Settings) -> int:
    with _client(settings) as client, _conn(settings) as conn:
        s = ingest.fetch_month(conn, client, args.month, log=print)
    print(
        f"{s.month}: {s.releases_fetched} releases fetched ({s.distinct_release_ids} distinct release ids, "
        f"{s.distinct_contracts} distinct contracts) in {s.pages} pages ({s.requests} requests), "
        f"{s.elapsed_s:.1f}s; new {s.new}, duplicate {s.duplicate}, rejected {s.rejected}"
    )
    return 0


def cmd_backfill(args: argparse.Namespace, settings: Settings) -> int:
    with _client(settings) as client, _conn(settings) as conn:
        ingest.backfill(conn, client, args.from_month, args.to_month, resume=args.resume)
    return 0


def cmd_rebuild(args: argparse.Namespace, settings: Settings) -> int:
    with _conn(settings) as conn:
        stats = rebuild(conn)
    print(f"rebuilt core and mart from {stats['parsed']} releases ({stats['unparsed']} could not be parsed)")
    return 0


def cmd_stats(args: argparse.Namespace, settings: Settings) -> int:
    with _conn(settings) as conn:
        def one(q: str):
            return conn.execute(q).fetchone()[0]  # type: ignore[arg-type]

        print("raw.release rows:", one("SELECT count(*) FROM raw.release"))
        print("rejected releases:", one("SELECT count(*) FROM ops.rejected_release"))
        row = conn.execute(
            "SELECT month, completed_at, pages, releases_fetched FROM ops.checkpoint ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
        print("latest checkpoint:", f"{row[0]} (completed {row[1]:%Y-%m-%d %H:%M}, {row[2]} pages, {row[3]} releases)" if row else "none")
        print("checkpointed months:", one("SELECT count(*) FROM ops.checkpoint"))
        for schema, tables in (
            ("core", ("contract", "amendment", "contract_supplier", "agency", "supplier")),
            ("mart", ("contract", "contract_supplier", "supplier", "agency", "supplier_fy", "agency_fy", "supplier_agency_fy")),
        ):
            for t in tables:
                print(f"{schema}.{t} rows:", one(f"SELECT count(*) FROM {schema}.{t}"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="austender", description="AusTender database tools")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="create roles and apply pending migrations").set_defaults(func=cmd_migrate)
    fm = sub.add_parser("fetch-month", help="fetch one UTC month (YYYY-MM) into raw")
    fm.add_argument("month")
    fm.set_defaults(func=cmd_fetch_month)
    bf = sub.add_parser("backfill", help="fetch months oldest first (long-running)")
    bf.add_argument("--from", dest="from_month", required=True, metavar="YYYY-MM")
    bf.add_argument("--to", dest="to_month", required=True, metavar="YYYY-MM")
    bf.add_argument("--resume", action="store_true", help="skip months that already have a checkpoint")
    bf.set_defaults(func=cmd_backfill)
    sub.add_parser("rebuild", help="rebuild core and mart from raw").set_defaults(func=cmd_rebuild)
    sub.add_parser("stats", help="row counts, latest checkpoint, rejected count").set_defaults(func=cmd_stats)
    return p


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, Settings.from_env())
    except ValueError as e:  # bad month, missing INGEST_USER_AGENT
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
