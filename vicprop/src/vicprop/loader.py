"""Parse the committed workbook and load it into mart.

Header layout (verified against the real file, but located by label so row offsets may drift):
a row whose first cell is "Locality" heads the table; year columns hold an integer year,
the preliminary column holds "Prelim" above a 2026 cell, and the summary columns hold
"24-25", "15-25" and "PA". Each year price column is followed by a flag column.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import psycopg
from openpyxl import load_workbook

PRELIM_YEAR = 2026


@dataclass(frozen=True)
class PriceRow:
    locality: str
    year: int
    median_price: int
    is_prelim: bool
    flagged: bool


@dataclass(frozen=True)
class TrendRow:
    locality: str
    change_24_25_pct: float | None
    growth_15_25_pct: float | None
    growth_pa_pct: float | None


@dataclass(frozen=True)
class WorkbookData:
    prices: list[PriceRow]
    trends: list[TrendRow]


def _num(text: object) -> int | None:
    """A price cell: '-' means unpublished (None). Anything else must be an integer."""
    if text is None:
        return None
    s = str(text).strip().replace(",", "")
    if s == "" or s == "-" or s.upper() == "NA":
        return None
    return int(s)


def _pct(text: object) -> float | None:
    """A summary cell: 'NA' means not computable (None)."""
    if text is None:
        return None
    s = str(text).strip()
    if s == "" or s.upper() == "NA":
        return None
    return float(s)


def _flagged(text: object) -> bool:
    return str(text).strip() in ("^", "*") if text is not None else False


def parse_workbook(path: Path) -> WorkbookData:
    """Pure parse of the workbook into price and trend rows. No database involved."""
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    assert ws is not None
    rows = list(ws.iter_rows(values_only=True))

    header_idx = next(
        i for i, r in enumerate(rows)
        if r and r[0] is not None and str(r[0]).strip().lower() == "locality"
    )
    header = rows[header_idx]
    prelim_row = rows[header_idx + 1]

    year_cols: dict[int, tuple[int, int]] = {}  # year -> (price col, flag col)
    prelim_col = prelim_flag_col = -1
    ch_col = g_col = pa_col = -1
    for j, cell in enumerate(header):
        if cell is None:
            continue
        s = str(cell).strip()
        if s.isdigit() and 1900 <= int(s) <= 2100:
            year_cols[int(s)] = (j, j + 1)
        elif s.lower() == "prelim":
            prelim_col, prelim_flag_col = j, j + 1
        elif s == "24-25":
            ch_col = j
        elif s == "15-25":
            g_col = j
        elif s.upper() == "PA":
            pa_col = j
    if prelim_col < 0 or prelim_row[prelim_col] != PRELIM_YEAR:
        raise ValueError("preliminary 2026 column not found where expected")
    if ch_col < 0 or g_col < 0 or pa_col < 0:
        raise ValueError("summary columns (24-25, 15-25, PA) not found where expected")
    if len(year_cols) == 0:
        raise ValueError("no year columns found")

    prices: list[PriceRow] = []
    trends: list[TrendRow] = []
    for r in rows[header_idx + 1:]:
        if not r or r[0] is None or str(r[0]).strip() == "":
            continue
        locality = str(r[0]).strip()
        for year in sorted(year_cols):
            price_col, flag_col = year_cols[year]
            median = _num(r[price_col]) if price_col < len(r) else None
            if median is None:
                continue  # '-' : no published median, so no row (never zero or NULL)
            flag = r[flag_col] if flag_col < len(r) else None
            prices.append(PriceRow(locality, year, median, False, _flagged(flag)))
        prelim = _num(r[prelim_col]) if prelim_col < len(r) else None
        if prelim is not None:
            flag = r[prelim_flag_col] if prelim_flag_col < len(r) else None
            prices.append(PriceRow(locality, PRELIM_YEAR, prelim, True, _flagged(flag)))
        trends.append(TrendRow(
            locality,
            _pct(r[ch_col]) if ch_col < len(r) else None,
            _pct(r[g_col]) if g_col < len(r) else None,
            _pct(r[pa_col]) if pa_col < len(r) else None,
        ))
    return WorkbookData(prices, trends)


def load(conn: psycopg.Connection, data: WorkbookData) -> dict[str, int]:
    """Refill mart from parsed workbook data in one transaction (DELETE, not TRUNCATE),
    so readers keep seeing the old data until it commits. Idempotent: loading twice
    gives identical tables."""
    with conn.transaction():
        conn.execute("SET LOCAL ROLE vicprop_loader")
        conn.execute("DELETE FROM mart.suburb_price")
        conn.execute("DELETE FROM mart.suburb_trend")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO mart.suburb_price (locality, year, median_price, is_prelim, flagged)"
                " VALUES (%s, %s, %s, %s, %s)",
                [(p.locality, p.year, p.median_price, p.is_prelim, p.flagged) for p in data.prices],
            )
            cur.executemany(
                "INSERT INTO mart.suburb_trend (locality, change_24_25_pct, growth_15_25_pct, growth_pa_pct)"
                " VALUES (%s, %s, %s, %s)",
                [(t.locality, t.change_24_25_pct, t.growth_15_25_pct, t.growth_pa_pct) for t in data.trends],
            )
    return {"price_rows": len(data.prices), "localities": len(data.trends)}
