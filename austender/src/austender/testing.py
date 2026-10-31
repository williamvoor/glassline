"""Test helpers, also used by the explorer's tests.

    from austender.testing import build_test_db
    build_test_db(dsn)   # dsn: a superuser connection string to an EMPTY database (e.g. Testcontainers)

Migrates, creates the roles, loads the hand-written seed releases and rebuilds core and mart. Afterwards
`explorer_ro` (password `explorer_ro_password`, default "explorer_ro") can read schema mart.
"""

from __future__ import annotations

import json
from pathlib import Path

import psycopg

from austender import db, ingest
from austender.mart import rebuild

SEED_DIR = Path(__file__).resolve().parents[2] / "tests" / "seed"


def seed_files(seed_dir: Path = SEED_DIR) -> list[Path]:
    return sorted(seed_dir.glob("*.json"))


def load_seed(conn: psycopg.Connection, seed_dir: Path = SEED_DIR) -> ingest.StoreResult:
    """Store the seed files in file-name order through the same path as live ingestion. A file holds one
    release object, or a list of them."""
    run_id = ingest.start_run(conn, "seed")
    total = ingest.StoreResult()
    for path in seed_files(seed_dir):
        data = json.loads(path.read_text())
        res = ingest.store_releases(conn, data if isinstance(data, list) else [data], run_id)
        total.new += res.new
        total.duplicate += res.duplicate
        total.rejected += res.rejected
    return total


def build_test_db(
    dsn: str,
    *,
    loader_password: str = "austender_loader",
    explorer_ro_password: str = "explorer_ro",
) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        db.ensure_roles(conn, loader_password=loader_password, explorer_ro_password=explorer_ro_password)
        db.migrate(conn)
        load_seed(conn)
        rebuild(conn)
