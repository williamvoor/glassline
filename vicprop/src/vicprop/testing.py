"""Test helpers, also used by the explorer's tests.

    from vicprop.testing import build_test_db
    build_test_db(dsn)   # dsn: a superuser connection string to an EMPTY database (e.g. Testcontainers)

Migrates, creates the roles and loads the committed workbook into mart. Afterwards
`explorer_ro` (password `explorer_ro_password`, default "explorer_ro") can read schema mart.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from vicprop import db, loader

XLSX = Path(__file__).resolve().parents[2] / "houses-by-suburb-2015-2025.xlsx"


def build_test_db(
    dsn: str,
    *,
    loader_password: str = "vicprop_loader",
    explorer_ro_password: str = "explorer_ro",
    xlsx: Path = XLSX,
) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        db.ensure_roles(conn, loader_password=loader_password, explorer_ro_password=explorer_ro_password)
        db.migrate(conn)
        loader.load(conn, xlsx, role=db.LOADER)
