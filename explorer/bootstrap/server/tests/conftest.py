"""Disposable Postgres 18 built by the austender package (ADR 0010), plus a small `bs` schema
with known comments, statistics and a slow view."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest

from austender.testing import build_test_db

from explorer_bootstrap.stats import open_report_pool

RO_PASSWORD = "ro-Secret-9f2"
LOADER_PASSWORD = "loader-Secret-7c1"
EXPLORER_ROOT = Path(__file__).resolve().parents[3]  # genUI/explorer
AUSTENDER_DATASET = EXPLORER_ROOT / "datasets" / "austender"

FIXTURE_SQL = """
CREATE SCHEMA bs;
CREATE TABLE bs.mixed (
    id integer PRIMARY KEY,
    name text,
    amount numeric(10,2) NOT NULL,
    created date,
    grade integer,
    ratio double precision
);
COMMENT ON TABLE bs.mixed IS 'A table with some comments.';
COMMENT ON COLUMN bs.mixed.id IS 'Row id.';
COMMENT ON COLUMN bs.mixed.amount IS 'Amount in dollars.';
COMMENT ON COLUMN bs.mixed.grade IS 'Seven values, unevenly spread.';
COMMENT ON COLUMN bs.mixed.ratio IS 'Has a NaN.';
INSERT INTO bs.mixed
SELECT g,
       CASE WHEN g <= 60 THEN 'a' WHEN g <= 90 THEN 'b' END,
       g * 1.5,
       DATE '2024-01-01' + g,
       CASE WHEN g <= 30 THEN 1 WHEN g <= 50 THEN 2 WHEN g <= 65 THEN 3 WHEN g <= 77 THEN 4
            WHEN g <= 87 THEN 5 WHEN g <= 94 THEN 6 ELSE 7 END,
       CASE WHEN g = 100 THEN 'NaN'::float8 ELSE g / 4.0 END
FROM generate_series(1, 100) g;
ANALYZE bs.mixed;

CREATE TABLE bs.fresh (x integer);
INSERT INTO bs.fresh VALUES (1), (2), (3);

CREATE VIEW bs.slow AS
SELECT g AS n FROM generate_series(1, 3) g CROSS JOIN pg_sleep(3) s;
COMMENT ON VIEW bs.slow IS 'Takes a second to read.';

GRANT USAGE ON SCHEMA bs TO explorer_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA bs TO explorer_ro;
"""


@dataclass(frozen=True)
class SeedDb:
    ro_dsn: str
    loader_dsn: str
    superuser_dsn: str


@pytest.fixture(scope="session")
def pg():
    try:
        import docker.errors
        from testcontainers.community.postgres import PostgresContainer

        container = PostgresContainer("postgres:18", driver=None)
        container.start()
    except docker.errors.DockerException as e:
        pytest.fail(f"database tests need Docker (Testcontainers Postgres 18), and it isn't available: {e}")
    try:
        superuser = container.get_connection_url()
        build_test_db(superuser, loader_password=LOADER_PASSWORD, explorer_ro_password=RO_PASSWORD)
        with psycopg.connect(superuser, autocommit=True) as conn:
            conn.execute("SET client_min_messages = warning")
            for stmt in FIXTURE_SQL.split(";\n"):
                if stmt.strip():
                    conn.execute(stmt)
        host, port = container.get_container_host_ip(), container.get_exposed_port(5432)
        base = f"{host}:{port}/{container.dbname}"
        yield SeedDb(
            ro_dsn=f"postgresql://explorer_ro:{RO_PASSWORD}@{base}",
            loader_dsn=f"postgresql://austender_loader:{LOADER_PASSWORD}@{base}",
            superuser_dsn=superuser,
        )
    finally:
        container.stop()


async def _open(dsn: str, timeout_ms: int):
    pool = open_report_pool(dsn, timeout_ms)
    await pool.open(wait=True)
    return pool


@pytest.fixture
async def ro_pool(pg):
    pool = await _open(pg.ro_dsn, 5000)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def loader_pool(pg):
    pool = await _open(pg.loader_dsn, 5000)
    try:
        yield pool
    finally:
        await pool.close()


BS_OVERLAY = """
["bs.mixed"]
comment = "OVERLAY mixed"
["bs.mixed".columns]
id = "OVERLAY id"
name = "OVERLAY name"
["bs.fresh"]
comment = "OVERLAY fresh"
["bs.slow".columns]
n = "OVERLAY n"
"""


def write_overlay_dataset(root: Path, overlay: str = BS_OVERLAY) -> Path:
    """A Dataset over the `bs` schema with a Comment overlay (explorer SPEC §13)."""
    d = root / "bs_dataset"
    d.mkdir()
    (d / "dataset.toml").write_text(
        'name = "bs"\ndescription = "Bootstrap fixture"\nallowed_schemas = ["bs"]\n'
        'glossary = "glossary.md"\nnotes = "notes.md"\n'
        '[comments]\noverlay = "comments.toml"\n[currency]\ncode = "AUD"\nsymbol = "$"\n',
        encoding="utf-8",
    )
    (d / "notes.md").write_text("- Notes.\n", encoding="utf-8")
    (d / "glossary.md").write_text("- term: meaning\n", encoding="utf-8")
    (d / "comments.toml").write_text(overlay, encoding="utf-8")
    return d


# Phase 2b: a valid submit_draft for the `bs` schema. Missing comments in bs: mixed.name,
# mixed.created, fresh, fresh.x, slow.n; mixed.amount gets a proposed rewrite.
BS_DATASET_TOML = (
    'name = "bs"\ndescription = "Bootstrap fixture."\nallowed_schemas = ["bs"]\n'
    'glossary = "glossary.md"\nnotes = "notes.md"\n\n[currency]\ncode = "AUD"\nsymbol = "$"\n'
)


def valid_args() -> dict:
    return {
        "files": {"dataset_toml": BS_DATASET_TOML, "notes_md": "- Prefer bs.mixed.\n",
                  "glossary_md": "- **grade**: a 1-7 score.\n"},
        "comments": [
            {"object": "bs.mixed", "column": "name", "comment": "Name code, 'a' or 'b'; NULL when unknown."},
            {"object": "bs.mixed", "column": "created", "comment": "Creation date."},
            {"object": "bs.mixed", "column": "amount", "comment": "Amount in Australian dollars."},
            {"object": "bs.fresh", "column": "", "comment": "A small table that was never analysed."},
            {"object": "bs.fresh", "column": "x", "comment": "An integer."},
            {"object": "bs.slow", "column": "n", "comment": "Row number."},
        ],
        "claims": [
            {"text": "bs.mixed has 100 rows", "file": "notes.md", "sql": "SELECT count(*) FROM bs.mixed",
             "expect": {"kind": "scalar", "value": 100, "abs_tol": 0, "rel_tol": 0}},
            {"text": "grade is 1 to 7", "file": "glossary.md", "sql": "SELECT DISTINCT grade::text FROM bs.mixed",
             "expect": {"kind": "set", "values": ["1", "2", "3", "4", "5", "6", "7"]}},
            {"text": "Name code, 'a' or 'b'", "file": "comments", "sql": "",
             "expect": {"kind": "none"}},
        ],
    }


@pytest.fixture
async def bs_report(ro_pool):
    from explorer_bootstrap.report import build_report

    return (await build_report(ro_pool, ["bs"], database={"host": "h", "name": "d"}, minmax=False)).to_dict()


@pytest.fixture
def bs_report_sync(pg):
    """The bs report for sync tests (which can't use the async ro_pool)."""
    import asyncio

    from explorer_bootstrap.report import build_report

    async def build():
        pool = await _open(pg.ro_dsn, 5000)
        try:
            return (await build_report(pool, ["bs"], database={"host": "h", "name": "d"}, minmax=False)).to_dict()
        finally:
            await pool.close()

    return asyncio.run(build())
