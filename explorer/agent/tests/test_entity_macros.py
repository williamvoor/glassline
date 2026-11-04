"""Slice 3 Macros (SPEC §18): rows that open an Entity Board (B9) and `Timeline` (B15)."""

import datetime as dt
import json
import os
from pathlib import Path

import pytest

from explorer_agent.boards import _Named
from explorer_agent.macros import (
    MAX_TIMELINE_ROWS,
    MacroError,
    check_columns,
    key_fits,
    macro_context,
    macro_expander,
)
from explorer_agent.results import ResultStore

DRILL = "SELECT 1 AS n FROM fixture.contracts WHERE supplier IS NOT DISTINCT FROM :supplier"
TODAY = dt.date(2026, 1, 31)


def expand(name: str, **args) -> list[dict]:
    return macro_expander().processor.expand(name, args, instance_id="m")


def action_of(components: list[dict]) -> dict | None:
    return next((c["action"] for c in components if "action" in c), None)


def spec_of(components: list[dict]) -> dict:
    return next(c["spec"] for c in components if c["component"] == "VegaChart")


def captions(components: list[dict]) -> list[str]:
    return [c["text"] for c in components if c.get("component") == "Text" and c.get("variant") == "caption"]


def _store(columns, types, rows) -> ResultStore:
    store = ResultStore()
    store.add(sql="SELECT 1", purpose="p", columns=columns, column_types=types, rows=rows, truncated=False)
    return store


def _suppliers(buyers=("B1", None, "B3")) -> ResultStore:
    rows = [{"supplier": f"S{i}", "buyer": b, "value": 10.0 - i} for i, b in enumerate(buyers)]
    return _store(["supplier", "buyer", "value"], ["text", "text", "numeric"], rows)


ENTITY = {"buyer": "buyer", "supplier": "supplier"}
KEY_TYPES = {"buyer": "text", "supplier": "text"}


# --- B9: Board rows --------------------------------------------------------------------------------


def test_rows_with_a_key_value_open_the_board_and_the_first_key_column_decides():
    store = _suppliers()
    with macro_context(store, entity=ENTITY, key_types=KEY_TYPES) as ctx:
        comps = expand("DataTable", result_id="r1", title="Suppliers")
    action = action_of(comps)
    assert action["boards"] == [0, 1, 2]  # `supplier` comes first in the Result: every row has one
    assert (ctx.sources[0].board, ctx.sources[0].key_column) == ("supplier", "supplier")


def _named_suppliers() -> ResultStore:
    rows = [{"supplier_name": f"S{i}", "supplier_key": f"name:s{i}", "value": 10.0 - i} for i in range(2)]
    return _store(["supplier_name", "supplier_key", "value"], ["text", "text", "numeric"], rows)


def grid_of(components: list[dict]) -> dict:
    return next(c for c in components if c["component"] == "DataGrid")


def test_a_table_hides_board_key_columns_by_default_and_still_opens_the_board():
    # A key is for navigation, not reading: `name:s0` and ABNs showed in answers and tab titles.
    with macro_context(_named_suppliers(), entity={"supplier_key": "supplier"}, key_types=KEY_TYPES) as ctx:
        comps = expand("DataTable", result_id="r1", title="Suppliers")
    assert [c["key"] for c in grid_of(comps)["columns"]] == ["supplier_name", "value"]
    assert action_of(comps)["boards"] == [0, 1]
    assert ctx.sources[0].labels == {0: "S0", 1: "S1"}


def test_a_listed_key_column_shows_but_stays_out_of_the_row_label():
    with macro_context(_named_suppliers(), entity={"supplier_key": "supplier"}, key_types=KEY_TYPES) as ctx:
        comps = expand("DataTable", result_id="r1", title="Suppliers", columns="supplier_name,supplier_key,value")
    assert [c["key"] for c in grid_of(comps)["columns"]] == ["supplier_name", "supplier_key", "value"]
    assert ctx.sources[0].labels == {0: "S0", 1: "S1"}


def test_a_key_column_that_is_the_only_text_column_stays_in_table_and_label():
    with macro_context(_suppliers(), entity={"supplier": "supplier"}, key_types=KEY_TYPES) as ctx:
        comps = expand("DataTable", result_id="r1", title="Suppliers", columns="supplier,value")
    assert [c["key"] for c in grid_of(comps)["columns"]] == ["supplier", "value"]
    assert ctx.sources[0].labels[0] == "S0"


def test_a_null_key_falls_back_and_in_answers_drill_sql_is_ignored_unchecked():
    store = _suppliers()
    entity = {"buyer": "buyer"}
    bad_drill = "SELECT nonsense FROM secret.table WHERE x = :nope"  # would fail every check
    with macro_context(store, entity=entity, key_types=KEY_TYPES, max_drills=0) as ctx:
        comps = expand("DataTable", result_id="r1", title="Suppliers", drill_sql=bad_drill)
    action = action_of(comps)
    assert action["boards"] == [0, 2]
    assert action["drillable"] is False and ctx.drills == [] and not ctx.errors
    assert ctx.sources[0].boards == (0, 2)


def test_on_a_board_the_drill_sql_is_checked_and_serves_the_rows_with_no_key():
    store = _suppliers()
    with macro_context(_Named({"q": store.get("r1")}), board=True, entity={"buyer": "buyer"},
                       key_types=KEY_TYPES, allowed_schemas=("fixture",)) as ctx:
        comps = expand("DataTable", result_id="q", title="Suppliers", drill_sql=DRILL)
    action = action_of(comps)
    assert action["boards"] == [0, 2] and action["drillable"] is True
    assert len(ctx.drills) == 1


def test_a_board_skips_its_own_key_columns_and_the_next_one_decides():
    store = _suppliers()
    with macro_context(_Named({"q": store.get("r1")}), board=True, entity=ENTITY, key_types=KEY_TYPES,
                       own_board="supplier") as ctx:
        comps = expand("DataTable", result_id="q", title="Suppliers")
    assert action_of(comps)["boards"] == [0, 2]
    assert ctx.sources[0].board == "buyer"
    with macro_context(_Named({"q": store.get("r1")}), board=True, entity={"supplier": "supplier"},
                       key_types=KEY_TYPES, own_board="supplier"):
        comps = expand("DataTable", result_id="q", title="Suppliers")
    assert "boards" not in action_of(comps)  # no self-link


def test_with_no_board_row_the_visual_behaves_as_before():
    store = _suppliers((None, None, None))
    with macro_context(store, entity={"buyer": "buyer"}, key_types=KEY_TYPES, allowed_schemas=("fixture",)) as ctx:
        comps = expand("DataTable", result_id="r1", title="Suppliers", drill_sql=DRILL)
    action = action_of(comps)
    assert "boards" not in action and action["drillable"] is True and len(ctx.drills) == 1


def test_charts_and_treemaps_carry_board_rows_too():
    store = _suppliers()
    with macro_context(store, entity={"buyer": "buyer"}, key_types=KEY_TYPES):
        bar = expand("BarChart", result_id="r1", x="supplier", y="value", title="By supplier")
        tiles = expand("Treemap", result_id="r1", title="Tiles", label="supplier", value="value")
    assert sorted(action_of(bar)["boards"]) == [0, 2]
    assert sorted(action_of(tiles)["boards"]) == [0, 2]


@pytest.mark.parametrize(
    ("value", "key_type", "fits"),
    [
        ("A", "text", True), (3, "text", False), (None, "text", False),
        (12345678901.0, "int", True), (1.5, "int", False), (True, "int", False), (7, "int", True),
        (1.5, "float", True), ("2024-01-02", "date", True), ("2024-01-02T00:00:00", "date", True),
        ("soon", "date", False), (False, "bool", True), ("yes", "bool", False),
        ("2024-13-45", "date", False),  # refuter F5: the shape alone isn't a date
        (2.0**53, "int", False), (float(2**53 - 1), "int", True),  # refuter F8: floats lose precision above 2^53
    ],
)
def test_key_values_fit_their_type(value, key_type, fits):
    assert key_fits(value, key_type) is fits


def test_an_unfitting_key_value_falls_back():
    store = _store(["agency_id", "value"], ["numeric", "numeric"], [{"agency_id": 7.0, "value": 1.0}, {"agency_id": 7.5, "value": 2.0}])
    with macro_context(store, entity={"agency_id": "agency"}, key_types={"agency": "int"}):
        comps = expand("DataTable", result_id="r1", title="Agencies")
    assert action_of(comps)["boards"] == [0]


# --- B15: Timeline ---------------------------------------------------------------------------------


def _contracts(rows):
    return _store(["contract", "start", "end", "kind", "value"], ["text", "date", "date", "text", "numeric"], rows)


def _row(contract, start, end, kind="A", value=1.0):
    return {"contract": contract, "start": start, "end": end, "kind": kind, "value": value}


ROWS = [
    _row("C2", "2025-03-01", "2026-06-30", "A", 2_000_000.0),
    _row("C1", "2024-01-01", "2025-12-31", "B", 1_000_000.0),
    _row("C3", "2025-01-01", "2027-01-01", "A", 3_000_000.0),
]


def _timeline(store, **args):
    with macro_context(store, today=lambda: TODAY) as ctx:
        comps = expand("Timeline", result_id="r1", title="Contracts", label="contract", start="start", end="end", **args)
    return comps, ctx


def _layer(spec, mark):
    return next(layer for layer in spec["layer"] if layer["mark"]["type"] == mark)


def test_one_bar_per_row_in_result_order_with_row_and_label_on_bars_only():
    comps, ctx = _timeline(_contracts(ROWS))
    spec = spec_of(comps)
    bars = _layer(spec, "bar")["data"]["values"]
    assert [b["_lane"] for b in bars] == ["C2", "C1", "C3"]
    assert [b["_row"] for b in bars] == [0, 1, 2] and [b["_label"] for b in bars] == ["C2", "C1", "C3"]
    for layer in spec["layer"]:
        if layer["mark"]["type"] != "bar":
            assert all("_row" not in v and "_label" not in v for v in layer["data"]["values"])
    assert action_of(comps)["rows"] == [0, 1, 2]
    assert _layer(spec, "bar")["mark"]["cursor"] == "pointer"
    assert ctx.visuals == 1


@pytest.mark.parametrize(("sort", "order"), [("result", [0, 1, 2]), ("start", [1, 2, 0]), ("end", [1, 0, 2])])
def test_sort_modes_set_the_displayed_order(sort, order):
    comps, _ = _timeline(_contracts(ROWS), sort=sort)
    assert action_of(comps)["rows"] == order
    assert _layer(spec_of(comps), "bar")["encoding"]["y"]["sort"] == [ROWS[i]["contract"] for i in order]


def test_the_today_rule_and_the_horizon_band():
    comps, _ = _timeline(_contracts(ROWS), horizon_months=1)
    spec = spec_of(comps)
    assert _layer(spec, "rule")["data"]["values"] == [{"_today": "2026-01-31"}]
    assert _layer(spec, "rect")["data"]["values"] == [{"_from": "2026-01-31", "_to": "2026-02-28"}]
    comps, _ = _timeline(_contracts(ROWS))
    assert not any(layer["mark"]["type"] == "rect" for layer in spec_of(comps)["layer"])


def test_colour_folds_by_row_count_and_values_show_at_the_bar_end():
    rows = [_row(f"C{i}", "2025-01-01", "2025-02-01", kind, 1_500_000.0) for i, kind in enumerate("AABBC")]
    with macro_context(_contracts(rows), today=lambda: TODAY, max_color_categories=2):
        comps = expand("Timeline", result_id="r1", title="T", label="contract", start="start", end="end",
                       color="kind", value="value", format="currency_compact")
    spec = spec_of(comps)
    bars = _layer(spec, "bar")["data"]["values"]
    assert [b["_color"] for b in bars] == ["A", "A", "B", "B", "Other"]
    assert _layer(spec, "bar")["encoding"]["color"]["sort"] == ["A", "B", "Other"]
    assert _layer(spec, "text")["data"]["values"][0] == {"_lane": "C0", "_end": "2025-02-01", "_value": "$1.5M"}


def test_the_row_cut_and_null_dates_are_reported():
    rows = [_row(f"C{i}", "2025-01-01", "2025-02-01") for i in range(MAX_TIMELINE_ROWS + 2)]
    rows.append(_row("X", None, "2025-01-01"))
    comps, _ = _timeline(_contracts(rows))
    assert len(_layer(spec_of(comps), "bar")["data"]["values"]) == MAX_TIMELINE_ROWS
    assert captions(comps) == [f"Showing {MAX_TIMELINE_ROWS} of {MAX_TIMELINE_ROWS + 2} bars; 1 row with no start or end left out"]


def test_repeated_lanes_label_their_start():
    rows = [_row("C1", "2025-01-01", "2025-02-01"), _row("C1", "2025-03-01", "2025-04-01")]
    comps, _ = _timeline(_contracts(rows))
    assert [b["_label"] for b in _layer(spec_of(comps), "bar")["data"]["values"]] == ["C1 · 2025-01-01", "C1 · 2025-03-01"]


@pytest.mark.parametrize(
    ("rows", "args", "message"),
    [
        ([_row("C1", "2025-02-01", "2025-01-01")], {}, "'end' comes before 'start'"),
        ([_row("C1", None, None)], {}, "no rows with both 'start' and 'end'"),
        (ROWS, {"sort": "value"}, "sort must be one of"),
        (ROWS, {"horizon_months": -1}, "horizon_months must be"),
    ],
)
def test_timeline_macro_errors(rows, args, message):
    with pytest.raises(MacroError, match=message):
        _timeline(_contracts(rows), **args)


def test_start_and_end_must_be_dates():
    store = _store(["contract", "start", "end"], ["text", "text", "date"], [{"contract": "C", "start": "x", "end": "2025-01-01"}])
    with pytest.raises(MacroError, match="start column 'start' of r1 is text, not a date"):
        _timeline(store)


def test_board_mode_shows_no_rows_for_a_timeline_with_nothing_to_draw():
    store = _contracts([])
    with macro_context(_Named({"q": store.get("r1")}), board=True, today=lambda: TODAY):
        comps = expand("Timeline", result_id="q", title="T", label="contract", start="start", end="end")
    assert "No rows" in [c.get("text") for c in comps]


def test_check_columns_knows_timeline():
    outputs = {"q": (["contract", "start", "end", "value"], ["text", "date", "text", "text"])}
    comp = {"id": "t", "component": "Timeline", "result_id": "q", "label": "contract", "start": "start", "end": "end", "value": "value"}
    problems = check_columns([comp], outputs)
    assert problems == [
        "t: end column 'end' of query 'q' is text, not a date",
        "t: value column 'value' of query 'q' is text, not a number",
    ]


TIMELINE_FIXTURE = Path(__file__).resolve().parents[2] / "client" / "test" / "fixtures" / "timeline_spec.json"


def test_timeline_spec_fixture_for_the_client_is_current():
    """The client renders this spec with real Vega-Lite (B17): bars clickable, rule and band not."""
    comps, _ = _timeline(_contracts(ROWS), horizon_months=6, value="value", format="currency_compact")
    current = json.dumps(spec_of(comps), indent=2, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_FIXTURES") == "1":
        TIMELINE_FIXTURE.write_text(current, encoding="utf-8")
    assert TIMELINE_FIXTURE.read_text(encoding="utf-8") == current


def test_timeline_passes_strict_validation():
    from explorer_agent.ui import UiPipeline

    store = _contracts(ROWS)
    reply = '<a2ui>\nroot = Column([t])\nt = Timeline("r1", "T", "contract", "start", "end", horizon_months=3)\n</a2ui>'
    ui = UiPipeline(allowed_schemas=("fixture",)).build(reply, store, "s1")
    assert any(c["component"] == "VegaChart" for m in ui.messages if "updateComponents" in m
               for c in m["updateComponents"]["components"])


def test_board_rows_pass_strict_validation():
    from explorer_agent.ui import UiPipeline

    reply = '<a2ui>\nroot = Column([t])\nt = DataTable("r1", "Suppliers")\n</a2ui>'
    ui = UiPipeline(allowed_schemas=("fixture",), entity={"buyer": "buyer"}, key_types=KEY_TYPES).build(reply, _suppliers(), "s1")
    grid = next(c for m in ui.messages if "updateComponents" in m for c in m["updateComponents"]["components"]
                if c["component"] == "DataGrid")
    assert grid["action"]["boards"] == [0, 2]
