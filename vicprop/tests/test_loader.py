"""The load rules (dash, flags, NA, prelim, a moved header) on small workbooks with the real layout,
through dataload and transforms/10_mart.sql. Needs Docker (Testcontainers Postgres 18), no network."""

from __future__ import annotations

import psycopg
import pytest
from openpyxl import Workbook

from dataload import LoadError

from vicprop import loader
from vicprop.db import LOADER

pytestmark = pytest.mark.db
YEARS = range(2015, 2026)
PRELIM_YEAR = 2026


def workbook(path, rows, leading_rows=1, prelim_year=PRELIM_YEAR):
    """The real file's shape: Locality, (price, flag) per year 2015-2025, Prelim + flag, then
    24-25, 15-25, PA; a sub-header row under it (2026 under Prelim, "(%)" under the summaries)."""
    wb = Workbook()
    ws = wb.active
    for _ in range(leading_rows):
        ws.append([])
    hdr = ["Locality"]
    for y in YEARS:
        hdr += [y, None]
    hdr += ["Prelim", None, "24-25", "15-25", "PA"]
    ws.append(hdr)
    sub = [None] * len(hdr)
    sub[1 + 2 * len(YEARS)] = prelim_year
    sub[-3:] = ["(%)", "(%)", "15-25"]
    ws.append(sub)
    for locality, prices, prelim, summary in rows:
        row = [locality]
        for y in YEARS:
            row += list(prices.get(y, ("-", " ")))
        row += list(prelim) + list(summary)
        ws.append(row)
    wb.save(path)
    return path


def mart(dsn):
    with psycopg.connect(dsn) as c:
        prices = c.execute("SELECT locality, year, median_price::int, is_prelim, flagged FROM mart.suburb_price "
                           "ORDER BY 1, 2").fetchall()
        trends = c.execute("SELECT locality, change_24_25_pct::float, growth_15_25_pct::float, growth_pa_pct::float "
                           "FROM mart.suburb_trend ORDER BY 1").fetchall()
    return prices, trends


@pytest.fixture
def fresh(dsn):
    yield dsn
    with psycopg.connect(dsn, autocommit=True) as conn:  # put the committed workbook back
        loader.load(conn, role=LOADER)


def test_years_prelim_flags_and_summaries(fresh, tmp_path):
    path = workbook(tmp_path / "t.xlsx", [
        ("ALPHA", {2015: ("100000", " "), 2016: ("110000", "^")}, ("120000", " "), ("9", "50", "4.1")),
        ("BETA", {2016: ("200000", "*")}, ("-", " "), ("5", "NA", "NA")),
    ])
    with psycopg.connect(fresh, autocommit=True) as conn:
        assert loader.load(conn, path, role=LOADER) == {"price_rows": 4, "localities": 2}
    prices, trends = mart(fresh)
    assert prices == [
        ("ALPHA", 2015, 100000, False, False),
        ("ALPHA", 2016, 110000, False, True),  # '^' flags the median
        ("ALPHA", PRELIM_YEAR, 120000, True, False),
        ("BETA", 2016, 200000, False, True),  # '*' also flags; '-' years give no row
    ]
    assert trends == [("ALPHA", 9.0, 50.0, 4.1), ("BETA", 5.0, None, None)]  # 'NA' becomes NULL


def test_header_found_below_leading_rows(fresh, tmp_path):
    path = workbook(tmp_path / "t2.xlsx", [("GAMMA", {2020: ("300000", " ")}, ("310000", "^"), ("1", "NA", "NA"))],
                    leading_rows=4)
    with psycopg.connect(fresh, autocommit=True) as conn:
        loader.load(conn, path, role=LOADER)
    prices, trends = mart(fresh)
    assert prices == [("GAMMA", 2020, 300000, False, False), ("GAMMA", PRELIM_YEAR, 310000, True, True)]
    assert trends == [("GAMMA", 1.0, None, None)]


# --- layout guards (refuter: the old parser refused these; the transform must too) ---------------

ROW = ("ALPHA", {2015: ("100000", " ")}, ("120000", " "), ("9", "50", "4.1"))


def test_a_prelim_column_for_another_year_is_refused(fresh, tmp_path):
    path = workbook(tmp_path / "t.xlsx", [ROW], prelim_year=2027)
    with psycopg.connect(fresh, autocommit=True) as conn, pytest.raises(LoadError, match="no 2026 under Prelim"):
        loader.load(conn, path, role=LOADER)


def test_a_shifted_flag_column_is_refused(fresh, tmp_path):
    path = workbook(tmp_path / "t.xlsx", [("ALPHA", {2015: ("100000", "100000")}, ("120000", " "), ("9", "50", "4.1"))])
    with psycopg.connect(fresh, autocommit=True) as conn, pytest.raises(LoadError, match="flag columns"):
        loader.load(conn, path, role=LOADER)

