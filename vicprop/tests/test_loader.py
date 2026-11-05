"""Pure workbook-parse tests on in-memory workbooks. No database, no network."""

from __future__ import annotations

from openpyxl import Workbook

from vicprop.loader import PRELIM_YEAR, parse_workbook


def _workbook(path, header_row=0, years=(2015, 2016), data=()):
    """Minimal layout mirroring the real file: locality, then (price, flag) column pairs
    per year, then prelim price + flag, then the three summary columns."""
    wb = Workbook()
    ws = wb.active
    for _ in range(header_row):
        ws.append([])
    hdr = ["Locality"]
    for y in years:
        hdr += [y, None]
    hdr += ["Prelim", None, "24-25", "15-25", "PA"]
    ws.append(hdr)
    second = [None] * len(hdr)
    second[1 + 2 * len(years)] = PRELIM_YEAR  # like the real file's 2026 cell under Prelim
    ws.append(second)
    for row in data:
        ws.append(list(row))
    wb.save(path)
    return path


def test_parse_years_prelim_and_summaries(tmp_path):
    path = _workbook(
        tmp_path / "t.xlsx",
        data=[
            # locality, 2015, flag, 2016, flag, prelim, flag, ch, g, pa
            ["ALPHA", "100000", " ", "110000", "^", "120000", " ", "9", "50", "4.1"],
            ["BETA", "-", " ", "200000", "*", "NA", " ", "5", "NA", "NA"],
        ],
    )
    data = parse_workbook(path)
    assert [(p.locality, p.year, p.median_price, p.is_prelim, p.flagged) for p in data.prices] == [
        ("ALPHA", 2015, 100000, False, False),
        ("ALPHA", 2016, 110000, False, True),   # '^' flags the median
        ("ALPHA", PRELIM_YEAR, 120000, True, False),
        ("BETA", 2016, 200000, False, True),    # '*' also flags; '-' 2015 gives no row
    ]
    assert [(t.locality, t.change_24_25_pct, t.growth_15_25_pct, t.growth_pa_pct) for t in data.trends] == [
        ("ALPHA", 9.0, 50.0, 4.1),
        ("BETA", 5.0, None, None),              # 'NA' summaries become NULL
    ]


def test_header_found_below_leading_rows(tmp_path):
    path = _workbook(
        tmp_path / "t2.xlsx",
        header_row=3,
        years=(2020,),
        data=[["GAMMA", "300000", " ", "310000", "^", "1", "NA", "NA"]],
    )
    data = parse_workbook(path)
    assert len(data.prices) == 2
    assert data.prices[0].locality == "GAMMA"
    assert data.trends[0].change_24_25_pct == 1.0
