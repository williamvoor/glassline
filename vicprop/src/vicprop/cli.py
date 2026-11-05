"""`uv run vicprop <command>`"""

from __future__ import annotations

import argparse
import sys

from vicprop import db, loader
from vicprop.config import Settings, load_env_file


def _conn(settings: Settings):
    return db.connect(**settings.loader_connect_kwargs(), autocommit=True)


def cmd_migrate(args: argparse.Namespace, settings: Settings) -> int:
    with _conn(settings) as conn:
        db.ensure_roles(
            conn, loader_password=settings.loader_password, explorer_ro_password=settings.explorer_ro_password
        )
        applied = db.migrate(conn)
    print(f"applied: {', '.join(applied)}" if applied else "up to date")
    return 0


def cmd_load(args: argparse.Namespace, settings: Settings) -> int:
    path = args.xlsx or settings.xlsx_file()
    data = loader.parse_workbook(path)
    with _conn(settings) as conn:
        stats = loader.load(conn, data)
    print(f"loaded {stats['price_rows']} price rows for {stats['localities']} localities from {path}")
    return 0


def cmd_stats(args: argparse.Namespace, settings: Settings) -> int:
    with _conn(settings) as conn:
        def one(q: str):
            return conn.execute(q).fetchone()[0]  # type: ignore[arg-type]

        print(f"localities : {one('SELECT count(*) FROM mart.suburb_price')} price rows")
        print(f"localities : {one('SELECT count(DISTINCT locality) FROM mart.suburb_price')} distinct")
        print(f"year range : {one('SELECT min(year) FROM mart.suburb_price')}–{one('SELECT max(year) FROM mart.suburb_price')} "
              f"({one('SELECT count(*) FROM mart.suburb_price WHERE is_prelim')} prelim rows)")
        print(f"flagged    : {one('SELECT count(*) FROM mart.suburb_price WHERE flagged')} rows")
        print(f"trends     : {one('SELECT count(*) FROM mart.suburb_trend')} localities")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    parser = argparse.ArgumentParser(prog="vicprop")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    p_load = sub.add_parser("load")
    p_load.add_argument("--xlsx", default=None)
    sub.add_parser("stats")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.command == "migrate":
        return cmd_migrate(args, settings)
    if args.command == "load":
        return cmd_load(args, settings)
    if args.command == "stats":
        return cmd_stats(args, settings)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
