"""Clickable Visuals, Drill query check step 1 and FollowUp in the macros (SPEC §15: A1, A3, A9, A12)."""

import pytest

from explorer_agent.macros import (
    TABLE_ROW_CAP,
    macro_context,
)
from explorer_agent.results import ResultStore
from tests.test_macros import by_type, expand

DRILL = "SELECT agency_name, count(*) AS n FROM mart.contract WHERE agency_name IS NOT DISTINCT FROM :supplier GROUP BY 1"


def _store(rows, columns=("supplier", "value"), types=("text", "numeric")) -> ResultStore:
    s = ResultStore()
    s.add(sql="SELECT 1", purpose="p", columns=list(columns), column_types=list(types), rows=rows, truncated=False)
    return s


SUPPLIERS = [
    {"supplier": "Beta", "value": 80.0},
    {"supplier": "Acme", "value": 300.0},
    {"supplier": "Gamma", "value": 80.0},
]


def _ctx(store, **kw):
    return macro_context(store, allowed_schemas=("mart",), **kw)


def chart(name="BarChart", **args):
    comps = expand(name, **{"result_id": "r1", "x": "supplier", "y": "value", "title": "T", **args})
    return by_type(comps, "VegaChart")[0]


# --- A1 -----------------------------------------------------------------------------------------


def test_bar_chart_is_clickable_with_rows_in_displayed_order():
    rows = [{"supplier": "Gamma", "value": 80.0}, {"supplier": "Acme", "value": 300.0}, {"supplier": "Beta", "value": 80.0}]
    with _ctx(_store(rows)) as ctx:
        vega = chart()
    # By value descending, ties (Gamma, Beta at 80, in that row order) by label ascending.
    assert vega["spec"]["encoding"]["x"]["sort"] == ["Acme", "Beta", "Gamma"]
    assert [v["_row"] for v in vega["spec"]["data"]["values"]] == [1, 2, 0]
    assert vega["action"] == {"source": "v1", "rows": [1, 2, 0], "drillable": False}
    (src,) = ctx.sources
    assert src.labels == {0: "Gamma", 1: "Acme", 2: "Beta"}
    assert src.rows == (1, 2, 0)


def test_clickable_false_removes_the_action():
    with _ctx(_store(SUPPLIERS)) as ctx:
        vega = chart(clickable=False)
    assert "action" not in vega
    assert all("_row" not in v for v in vega["spec"]["data"]["values"])
    assert ctx.sources == []


def test_stacked_labels_and_order_by_bar_total():
    rows = [
        {"supplier": "A", "kind": "x", "value": 1.0},
        {"supplier": "B", "kind": "x", "value": 5.0},
        {"supplier": "A", "kind": "y", "value": 10.0},
    ]
    with _ctx(_store(rows, ("supplier", "kind", "value"), ("text", "text", "numeric"))) as ctx:
        vega = chart(color="kind")
    assert vega["spec"]["encoding"]["x"]["sort"] == ["A", "B"]  # A's bar total 11 > 5
    assert ctx.sources[0].labels == {0: "A · x", 1: "B · x", 2: "A · y"}
    # Within a bar: by colour domain (y has the larger total, so it comes first).
    assert vega["action"]["rows"] == [2, 0, 1]


def test_folded_marks_and_other_are_not_clickable():
    rows = [
        {"supplier": "A", "kind": "x", "value": 1.0},
        {"supplier": "A", "kind": "x", "value": 2.0},  # folded with the row above
        {"supplier": "B", "kind": "z", "value": 0.1},  # beyond the top 1: "Other"
        {"supplier": "C", "kind": "x", "value": 3.0},
    ]
    with _ctx(_store(rows, ("supplier", "kind", "value"), ("text", "text", "numeric")), max_color_categories=1):
        vega = chart(color="kind")
    clickable = {v["supplier"]: v.get("_row") for v in vega["spec"]["data"]["values"]}
    assert clickable == {"A": None, "B": None, "C": 3}
    assert vega["action"]["rows"] == [3]


def test_line_points_are_labelled_by_x_in_result_order():
    rows = [{"year": 2024.0, "value": 1.0}, {"year": 2023.0, "value": 2.0}]
    with _ctx(_store(rows, ("year", "value"), ("numeric", "numeric"))) as ctx:
        vega = chart("LineChart", x="year")
    assert vega["action"]["rows"] == [0, 1]
    assert ctx.sources[0].labels == {0: "2024", 1: "2023"}


def test_rows_past_the_table_cap_are_not_clickable():
    rows = [{"supplier": f"S{i:03}", "value": float(i)} for i in range(TABLE_ROW_CAP + 5)]
    with _ctx(_store(rows)):
        vega = chart()
        grid = by_type(expand("DataTable", result_id="r1", title="T"), "DataGrid")[0]
    assert len(vega["action"]["rows"]) == TABLE_ROW_CAP
    assert max(vega["action"]["rows"]) == TABLE_ROW_CAP - 1
    assert grid["action"]["rows"] == list(range(TABLE_ROW_CAP))


def test_table_rows_are_clickable_with_text_labels():
    rows = [{"supplier": "Acme", "kind": "x" * 100, "value": 1.0}]
    with _ctx(_store(rows, ("supplier", "kind", "value"), ("text", "text", "numeric"))) as ctx:
        grid = by_type(expand("DataTable", result_id="r1", title="T", columns="supplier,kind,value"), "DataGrid")[0]
    assert grid["action"] == {"source": "v1", "rows": [0], "drillable": False}
    label = ctx.sources[0].labels[0]
    assert label.startswith("Acme · xxx") and len(label) == 80 and label.endswith("…")


def test_metric_and_cite_are_never_clickable():
    with _ctx(_store(SUPPLIERS)) as ctx:
        expand("Metric", result_id="r1", column="value", label="L")
        expand("Cite", template="{r1[0].supplier}")
    assert ctx.sources == []


def test_a_visual_with_no_clickable_rows_ignores_its_drill():
    with _ctx(_store([])) as ctx:
        grid = by_type(expand("DataTable", result_id="r1", title="T", drill_sql="garbage :x"), "DataGrid")[0]
    assert "action" not in grid
    assert ctx.errors == [] and ctx.drills == []


# --- A3: check step 1 -----------------------------------------------------------------------------


def test_a_valid_drill_makes_the_visual_drillable():
    with _ctx(_store(SUPPLIERS)) as ctx:
        vega = chart(drill_sql=DRILL, drill_title="By agency")
    assert vega["action"]["drillable"] is True
    (spec,) = ctx.drills
    assert (spec.source, spec.visual, spec.names, spec.title) == ("v1", 0, ("supplier",), "By agency")
    assert "$1" in spec.sql


@pytest.mark.parametrize(
    ("args", "match"),
    [
        ({"drill_sql": "SELECT 1 FROM mart.contract"}, "at least one :column"),
        ({"drill_sql": "SELECT 1 FROM mart.contract WHERE a IS NOT DISTINCT FROM :nope"}, "not a column"),
        ({"drill_sql": "SELECT 1 FROM mart.contract WHERE a IS NOT DISTINCT FROM :supplier; SELECT 2"}, "exactly one"),
        ({"drill_sql": "DELETE FROM mart.contract WHERE a IS NOT DISTINCT FROM :supplier"}, "only SELECT"),
        ({"drill_sql": "SELECT 1 FROM other.t WHERE a IS NOT DISTINCT FROM :supplier"}, "not allowed"),
        ({"drill_sql": DRILL, "drill_as": "pie"}, "drill_as must be one of"),
        ({"drill_sql": DRILL, "drill_as": "bar"}, "needs drill_x and drill_y"),
        ({"drill_sql": DRILL, "drill_as": "line", "drill_x": "n"}, "needs drill_x and drill_y"),
    ],
)
def test_step1_errors_are_drill_errors(args, match):
    with _ctx(_store(SUPPLIERS)) as ctx:
        with pytest.raises(Exception, match=match):
            chart(**args)
    assert ctx.drill_errors == {0}
    assert ctx.drill_error_count == len(ctx.errors) == 1
    assert ctx.errors[0].startswith("BarChart: drill: ")
    assert ctx.sources == []  # nothing for a failed Visual


@pytest.mark.parametrize("op", ["= :supplier", "<> :supplier", "IN (:supplier)", "= ANY(ARRAY[:supplier])"])
def test_null_dropping_comparison_on_a_null_column_is_rejected(op):
    rows = [*SUPPLIERS, {"supplier": None, "value": 1.0}]
    with _ctx(_store(rows)) as ctx:
        with pytest.raises(Exception, match="IS NOT DISTINCT FROM"):
            chart(drill_sql=f"SELECT 1 FROM mart.contract WHERE agency_name {op}")
    assert ctx.drill_errors == {0}


def test_null_dropping_comparison_is_fine_when_no_clickable_row_is_null():
    with _ctx(_store(SUPPLIERS)) as ctx:
        chart(drill_sql="SELECT 1 FROM mart.contract WHERE agency_name = :supplier")
    assert len(ctx.drills) == 1


def test_list_columns_cannot_be_placeholders():
    rows = [{"supplier": "A", "tags": ["a", "b"], "value": 1.0}]
    with _ctx(_store(rows, ("supplier", "tags", "value"), ("text", "text[]", "numeric"))):
        with pytest.raises(Exception, match="can't be bound"):
            expand("DataTable", result_id="r1", title="T",
                   drill_sql="SELECT 1 FROM mart.contract WHERE agency_name IS NOT DISTINCT FROM :tags")


def test_a_third_drill_query_is_an_error():
    with _ctx(_store(SUPPLIERS)) as ctx:
        chart(drill_sql=DRILL)
        chart(drill_sql=DRILL)
        with pytest.raises(Exception, match="at most 2"):
            chart(drill_sql=DRILL)
    assert ctx.drill_errors == {2}


def test_ignored_drills_leave_the_visual_clickable_for_asks():
    with _ctx(_store(SUPPLIERS), ignore_drills=frozenset({0})) as ctx:
        vega = chart(drill_sql="broken :nope")
    assert vega["action"]["drillable"] is False
    assert ctx.errors == [] and len(ctx.sources) == 1


# --- A12: FollowUp --------------------------------------------------------------------------------


def test_follow_up_is_a_button_with_an_ask_action():
    with _ctx(_store(SUPPLIERS)) as ctx:
        comps = expand("FollowUp", label="By year", request="Show the same by year")
    button = next(c for c in comps if c["component"] == "Button")
    text = next(c for c in comps if c["component"] == "Text")
    assert button["child"] == text["id"] and text["text"] == "By year"
    assert button["action"] == {"event": {"name": "ask", "context": {"follow_up": "f1"}}}
    assert [(f.id, f.request) for f in ctx.follow_ups] == [("f1", "Show the same by year")]


@pytest.mark.parametrize(("label", "req", "match"), [
    ("L", " ", "needs a request"),
    (" ", "R", "needs a label"),
    ("L", "x" * 501, "at most 500"),
])
def test_follow_up_errors(label, req, match):
    with _ctx(_store(SUPPLIERS)):
        with pytest.raises(Exception, match=match):
            expand("FollowUp", label=label, request=req)


def test_a_fourth_follow_up_is_an_error():
    with _ctx(_store(SUPPLIERS)):
        for i in range(3):
            expand("FollowUp", label=f"L{i}", request="R")
        with pytest.raises(Exception, match="at most 3"):
            expand("FollowUp", label="L", request="R")
