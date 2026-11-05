"""Database tests against a disposable Postgres 18 (Testcontainers). Needs Docker, no network."""

from __future__ import annotations

import pytest
import psycopg
from testcontainers.postgres import PostgresContainer

from vicprop import db, loader
from vicprop.testing import XLSX, build_test_db

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def dsn():
    with PostgresContainer("postgres:18", username="postgres", password="postgres") as pg:
        yield pg.get_connection_url().replace("+psycopg2", "")


@pytest.fixture(scope="module")
def loaded(dsn):
    build_test_db(dsn)
    return dsn


def test_full_workbook_counts_match_parser(loaded):
    expected = loader.parse_workbook(XLSX)
    with psycopg.connect(loaded) as conn:
        localities = conn.execute("SELECT count(*) FROM mart.suburb_trend").fetchone()[0]
        prices = conn.execute("SELECT count(*) FROM mart.suburb_price").fetchone()[0]
        ymin, ymax = conn.execute("SELECT min(year), max(year) FROM mart.suburb_price").fetchone()
        prelims = conn.execute("SELECT count(*) FROM mart.suburb_price WHERE is_prelim").fetchone()[0]
        flagged = conn.execute("SELECT count(*) FROM mart.suburb_price WHERE flagged").fetchone()[0]
    assert localities == len(expected.trends) == 797
    assert prices == len(expected.prices) > 9000
    assert (ymin, ymax) == (2015, 2026)
    assert prelims == sum(1 for p in expected.prices if p.is_prelim)
    assert flagged == sum(1 for p in expected.prices if p.flagged)


def test_no_zero_or_null_median_rows(loaded):
    with psycopg.connect(loaded) as conn:
        bad = conn.execute(
            "SELECT count(*) FROM mart.suburb_price WHERE median_price IS NULL OR median_price <= 0"
        ).fetchone()[0]
    assert bad == 0


def test_load_is_idempotent(dsn):
    data = loader.parse_workbook(XLSX)
    with psycopg.connect(dsn, autocommit=True) as conn:
        before = conn.execute("SELECT count(*) FROM mart.suburb_price").fetchone()[0]
        loader.load(conn, data)
        after = conn.execute("SELECT count(*) FROM mart.suburb_price").fetchone()[0]
    assert before == after


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
