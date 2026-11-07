"""Database tests against a disposable Postgres 18 (Testcontainers). Needs Docker, no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import psycopg

from vicprop import loader
from vicprop.db import LOADER

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "mart_golden.json").read_text())

pytestmark = pytest.mark.db


@pytest.fixture
def loaded(dsn):
    return dsn


def test_t7_mart_matches_the_pre_dataload_loader_row_for_row(loaded):
    """dataload/SPEC.md T7: the golden snapshot was taken from the old parser before the port."""
    with psycopg.connect(loaded) as conn:
        price = [list(r) for r in conn.execute(
            "SELECT locality, year::text, median_price::text, is_prelim::text, flagged::text "
            "FROM mart.suburb_price ORDER BY 1, 2")]
        trend = [list(r) for r in conn.execute(
            "SELECT locality, change_24_25_pct::text, growth_15_25_pct::text, growth_pa_pct::text "
            "FROM mart.suburb_trend ORDER BY 1")]
    assert len(price) == len(GOLDEN["suburb_price"]) == 9504
    assert price == GOLDEN["suburb_price"]
    assert trend == GOLDEN["suburb_trend"]


def test_explorer_ro_sees_mart_but_not_staging(loaded):
    ro = psycopg.conninfo.make_conninfo(loaded, user="explorer_ro", password="explorer_ro")
    with psycopg.connect(ro) as conn:
        assert conn.execute("SELECT count(*) FROM mart.suburb_trend").fetchone()[0] == 797
    with psycopg.connect(ro) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT * FROM staging.houses_raw")


def test_no_zero_or_null_median_rows(loaded):
    with psycopg.connect(loaded) as conn:
        bad = conn.execute(
            "SELECT count(*) FROM mart.suburb_price WHERE median_price IS NULL OR median_price <= 0"
        ).fetchone()[0]
    assert bad == 0


def test_load_is_idempotent(dsn):
    with psycopg.connect(dsn, autocommit=True) as conn:
        loader.load(conn, role=LOADER)
    test_t7_mart_matches_the_pre_dataload_loader_row_for_row(dsn)  # a second load: still the golden rows


def test_every_mart_object_has_a_comment_and_total_stays_small(loaded):
    with psycopg.connect(loaded) as conn:
        missing = conn.execute(
            """
            SELECT table_name, column_name FROM (
              SELECT c.table_name, c.column_name, col_description(
                (quote_ident(c.table_schema) || '.' || quote_ident(c.table_name))::regclass, c.ordinal_position
              ) AS col_comment,
              obj_description(
                (quote_ident(c.table_schema) || '.' || quote_ident(c.table_name))::regclass
              ) AS tbl_comment
              FROM information_schema.columns c WHERE c.table_schema = 'mart'
            ) s WHERE (col_comment IS NULL OR col_comment = '')
               OR (tbl_comment IS NULL OR tbl_comment = '')
            """
        ).fetchall()
        total = conn.execute(
            """
            SELECT coalesce(sum(length(obj_description(oid))), 0)
              + coalesce(sum(length(col_description(oid, ord))), 0)
            FROM (SELECT ('mart.' || tablename)::regclass AS oid FROM pg_tables WHERE schemaname = 'mart') t
            CROSS JOIN LATERAL (
              SELECT a.attnum AS ord FROM pg_attribute a
              WHERE a.attrelid = t.oid AND a.attnum > 0 AND NOT a.attisdropped
            ) c
            """
        ).fetchone()[0]
    assert missing == []
    assert total < 12000
