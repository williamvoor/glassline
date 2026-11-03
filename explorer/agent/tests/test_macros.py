import pytest
from pydantic import TypeAdapter

from a2ui.core.processing.message_processor import MessageProcessor, MessageProcessorOptions
from a2ui.core.schema import AgentToRendererMessage
from a2ui.core.validation.payload_validator import STRICT_VALIDATION
from a2ui.inference_formats.express import ExpressFormat
from a2ui.parser import A2uiPart

from explorer_agent.catalog import EXPLORER_CATALOG_ID, explorer_catalog
from explorer_agent.macros import (
    MacroError,
    column_label,
    fill_prose,
    format_value,
    macro_context,
    macro_expander,
)
from explorer_agent.results import ResultStore
from tests.conftest import SUPPLIERS_SQL


def expand(name: str, **args) -> list[dict]:
    return macro_expander().processor.expand(name, args, instance_id="m")


def kinds(components: list[dict]) -> list[str]:
    return sorted(c["component"] for c in components)


def root(components: list[dict]) -> dict:
    return next(c for c in components if c["id"] == "m")


def by_type(components: list[dict], kind: str) -> list[dict]:
    return [c for c in components if c["component"] == kind]


def to_transport(reply: str) -> list[dict]:
    expander = macro_expander()
    inference = expander.transform_to_inference_catalog(explorer_catalog())
    fmt = ExpressFormat(catalogs=[inference], surface_id="main", version="v0.9.1")
    parts = fmt.create_parser().parse_response(reply)
    msgs = [m for p in parts if isinstance(p, A2uiPart) for m in p.a2ui]
    typed = [TypeAdapter(AgentToRendererMessage).validate_python(m) for m in msgs]
    return [m.model_dump(by_alias=True, exclude_none=True) for m in expander.transform_to_transport(typed)]


# --- formatting ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "fmt", "column_type", "expected"),
    [
        (1234, "auto", "bigint", "1,234"),
        (1234.0, "auto", "bigint", "1,234"),
        (1234.567, "auto", "numeric", "1,234.57"),
        ("Acme", "auto", "text", "Acme"),
        (None, "auto", "numeric", "—"),
        (3_441_812_603.4, "currency", "numeric", "$3,441,812,603"),
        (-1500.0, "currency", "numeric", "-$1,500"),
        (3_441_812_603.0, "currency_m", "numeric", "$3,441.8M"),
        (38_414_946_047.02, "currency_compact", "numeric", "$38.4B"),
        (812_300_000.0, "currency_compact", "numeric", "$812.3M"),
        (45_200, "currency_compact", "bigint", "$45.2K"),
        (812.4, "currency_compact", "numeric", "$812"),
        (0, "currency_compact", "bigint", "$0"),
        (-1_500_000_000.0, "currency_compact", "numeric", "-$1.5B"),
        (999_960_000.0, "currency_compact", "numeric", "$1.0B"),
        (999.96, "currency_compact", "numeric", "$1.0K"),
        (1_234_500_000_000.0, "currency_compact", "numeric", "$1,234.5B"),
        (None, "currency_compact", "numeric", "—"),
        (12.345, "pct", "numeric", "12.3%"),
        (0.1234, "fraction_pct", "numeric", "12.3%"),
        (1999.6, "int", "numeric", "2,000"),
        ("2024-07-01T00:00:00", "date", "timestamp", "2024-07-01"),
    ],
)
def test_format_value(value, fmt, column_type, expected):
    assert format_value(value, fmt, column_type) == expected


def test_currency_symbol_comes_from_the_caller_not_the_code():
    assert format_value(3_441_812_603.0, "currency_m", "numeric", currency_symbol="€") == "€3,441.8M"
    assert format_value(-1500.0, "currency", "numeric", currency_symbol="€") == "-€1,500"
    assert format_value(-2_500_000.0, "currency_compact", "numeric", currency_symbol="€") == "-€2.5M"


def test_currency_compact_needs_a_number():
    with pytest.raises(MacroError, match="needs a number"):
        format_value("Acme", "currency_compact", "text")


@pytest.mark.parametrize(
    ("column", "label"),
    [
        ("value", "Value"),
        ("total_value", "Total value"),
        ("abn", "Abn"),
        ("Value (AUD)", "Value (AUD)"),
        ("fy2024", "Fy2024"),
    ],
)
def test_column_label(column, label):
    assert column_label(column) == label


def test_numeric_format_on_text_is_an_error():
    with pytest.raises(MacroError, match="needs a number"):
        format_value("Acme", "currency")


# --- shapes --------------------------------------------------------------------------------


def test_bar_chart_shape_and_sorting(ctx):
    comps = expand("BarChart", result_id="r1", x="supplier", y="value", title="Top", format="currency_m")
    # Every Visual is a Card holding title, Visual and SQL (SPEC §16).
    assert kinds(comps) == ["Card", "Column", "SqlDetails", "Text", "VegaChart"]
    assert root(comps)["component"] == "Card"
    title = by_type(comps, "Text")[0]
    assert (title["text"], title["variant"]) == ("Top", "h4")
    spec = by_type(comps, "VegaChart")[0]["spec"]
    assert spec["mark"] == {"type": "bar"}
    # An explicit domain in displayed order (by value), not "-y" (SPEC §15): server and screen agree.
    assert spec["encoding"]["x"]["sort"] == ["Acme_[x]", "Beta", "Gamma"]
    assert spec["encoding"]["y"]["title"] == "Value ($M)"
    assert spec["encoding"]["x"]["title"] == "Supplier"
    assert spec["encoding"]["y"]["axis"]["format"] == ",.1f"
    assert spec["data"]["values"][0] == {"supplier": "Acme_[x]", "value": pytest.approx(3441.812603), "_row": 1}
    assert "url" not in spec["data"]
    sql = by_type(comps, "SqlDetails")[0]
    assert (sql["sql"], sql["summary"]) == (SUPPLIERS_SQL, "top suppliers")


def test_chart_currency_symbol_comes_from_the_context(store):
    with macro_context(store, currency_symbol="€"):
        spec = by_type(expand("BarChart", result_id="r1", x="supplier", y="value", title="T",
                              format="currency"), "VegaChart")[0]["spec"]
    assert spec["encoding"]["y"]["title"] == "Value (€)"
    assert "$" not in str(spec["encoding"])


def test_horizontal_bar_chart_puts_categories_on_y(ctx):
    spec = by_type(expand("BarChart", result_id="r1", x="supplier", y="value", title="T", horizontal=True),
                   "VegaChart")[0]["spec"]
    assert spec["encoding"]["y"]["field"] == "supplier"
    assert spec["encoding"]["y"]["sort"] == ["Acme_[x]", "Beta", "Gamma"]
    assert spec["encoding"]["x"]["field"] == "value"


def test_line_chart_keeps_row_order_or_uses_time(store):
    store.add(sql="s", purpose="by month", columns=["month", "value"], column_types=["date", "numeric"],
              rows=[{"month": "2024-01-01", "value": 1.0}], truncated=False)
    with macro_context(store):
        ordinal = by_type(expand("LineChart", result_id="r1", x="supplier", y="value", title="T"), "VegaChart")
        temporal = by_type(expand("LineChart", result_id="r2", x="month", y="value", title="T"), "VegaChart")
    assert ordinal[0]["spec"]["encoding"]["x"]["sort"] is None
    assert temporal[0]["spec"]["encoding"]["x"]["type"] == "temporal"


def test_color_keeps_top_groups_and_folds_the_rest_into_other():
    store = ResultStore()
    rows = [{"year": "2024", "agency": f"A{i}", "value": float(i)} for i in range(1, 11)]
    rows.append({"year": "2023", "agency": "A1", "value": 5.0})
    store.add(sql="s", purpose="p", columns=["year", "agency", "value"],
              column_types=["text", "text", "numeric"], rows=rows, truncated=False)
    with macro_context(store, max_color_categories=3):
        spec = by_type(expand("BarChart", result_id="r1", x="year", y="value", title="T", color="agency"),
                       "VegaChart")[0]["spec"]
    # Totals: A10=10, A9=9, A8=8 kept; A1 (1 + 5 = 6) and A2..A7 fold into Other.
    assert spec["encoding"]["color"]["sort"] == ["A10", "A9", "A8", "Other"]
    values = {(v["year"], v["agency"]): v["value"] for v in spec["data"]["values"]}
    assert values[("2024", "Other")] == sum(range(1, 8))
    assert values[("2023", "Other")] == 5.0
    assert len(values) == 5


def test_data_table_columns_alignment_and_formats(ctx):
    comps = expand("DataTable", result_id="r1", title="Suppliers", columns="value, supplier",
                   format="value:currency_m")
    assert kinds(comps) == ["Card", "Column", "DataGrid", "SqlDetails", "Text"]
    assert root(comps)["component"] == "Card"
    grid = by_type(comps, "DataGrid")[0]
    assert grid["columns"] == [{"key": "value", "label": "Value", "align": "end"},
                               {"key": "supplier", "label": "Supplier"}]
    assert grid["rows"][1] == ["$3,441.8M", "Acme_[x]"]
    assert "caption" not in grid


def test_data_table_row_cap_and_truncation_note():
    store = ResultStore()
    store.add(sql="s", purpose="p", columns=["n"], column_types=["integer"],
              rows=[{"n": i} for i in range(5000)], truncated=True)
    with macro_context(store, table_row_cap=200):
        grid = by_type(expand("DataTable", result_id="r1", title="T"), "DataGrid")[0]
    assert len(grid["rows"]) == 200
    assert grid["caption"] == "Showing 200 of 5,000 rows; query truncated at 5,000 rows"


def test_metric_shape(ctx):
    comps = expand("Metric", result_id="r1", column="value", label="Top supplier value", row=1, format="currency")
    assert kinds(comps) == ["Card", "Column", "SqlDetails", "Text", "Text"]
    assert root(comps)["component"] == "Card"
    texts = by_type(comps, "Text")
    assert (texts[0]["text"], texts[0]["variant"]) == ("Top supplier value", "caption")
    assert (texts[1]["text"], texts[1]["variant"]) == ("$3,441,812,603", "h2")


def test_cite_fills_placeholders_and_escapes_markdown(ctx):
    comps = expand("Cite", template="**{r1[1].supplier}** received {r1[1].value|currency_m} over {r1[1].contracts} contracts.")
    assert comps == [{"id": "m", "component": "Text",
                      "text": r"**Acme\_\[x\]** received \$3,441\.8M over 1,234 contracts.",
                      "variant": "body"}]


def test_metric_and_cite_use_the_context_currency(store):
    with macro_context(store, currency_symbol="€"):
        metric = by_type(expand("Metric", result_id="r1", column="value", label="L", row=1,
                                format="currency_m"), "Text")[1]
        cite = expand("Cite", template="{r1[1].value|currency_m}")[0]
    assert metric["text"] == "€3,441.8M"
    assert cite["text"] == r"€3,441\.8M"


# --- prose ---------------------------------------------------------------------------------


def test_prose_placeholders_are_filled_and_escaped(ctx):
    filled = fill_prose("**{r1[1].supplier}** received {r1[1].value|currency_m}.")
    assert filled.text == r"**Acme\_\[x\]** received \$3,441\.8M."
    assert filled.bare_numbers == []
    assert ctx.errors == []


def test_bare_numbers_typed_in_prose_are_reported(ctx):
    filled = fill_prose("In 2024, {r1[1].supplier} won 1,234 contracts worth $3.4B.")
    assert filled.bare_numbers == ["1,234", "3.4"]


def test_a_bad_prose_placeholder_goes_back_to_the_model(ctx):
    filled = fill_prose("{r1[0].nope} and {r9[0].value}")
    assert ctx.errors == [
        "prose: r1 has no column 'nope'; its columns are: supplier, contracts, value",
        "prose: unknown result_id 'r9'; valid IDs: r1",
    ]
    assert filled.text == "{r1[0].nope} and {r9[0].value}"


def test_prose_without_placeholders_is_unchanged(ctx):
    assert fill_prose("Nothing to cite here.").text == "Nothing to cite here."


# --- errors --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "args", "message"),
    [
        ("BarChart", {"result_id": "r9", "x": "supplier", "y": "value", "title": "T"},
         "unknown result_id 'r9'; valid IDs: r1"),
        ("BarChart", {"result_id": "r1", "x": "suplier", "y": "value", "title": "T"},
         "r1 has no column 'suplier'; its columns are: supplier, contracts, value"),
        ("BarChart", {"result_id": "r1", "x": "value", "y": "supplier", "title": "T"},
         "y column 'supplier' of r1 is text, not a number"),
        ("LineChart", {"result_id": "r1", "x": "supplier", "y": "value", "title": "T", "format": "date"},
         "format 'date' does not apply to a chart value"),
        ("DataTable", {"result_id": "r1", "title": "T", "format": "value"},
         "format entry 'value' must look like column:format"),
        ("Metric", {"result_id": "r1", "column": "value", "label": "L", "row": 3},
         "r1 has 3 rows; row 3 does not exist"),
        ("Cite", {"template": "{r1[0].nope}"}, "r1 has no column 'nope'"),
        ("Metric", {"result_id": "r1", "column": "value", "label": "L", "format": "money"},
         "unknown format 'money'"),
    ],
)
def test_macro_errors_are_captured_for_the_model(ctx, name, args, message):
    with pytest.raises(MacroError):
        expand(name, **args)
    assert len(ctx.errors) == 1
    assert ctx.errors[0].startswith(f"{name}: {message}")


def test_errors_are_captured_even_though_the_expander_swallows_them(ctx):
    reply = '<a2ui>\nsurface("main")\nroot = Column([chart])\nchart = BarChart("r7", "supplier", "value", "T")\n</a2ui>'
    to_transport(reply)
    assert ctx.errors == ["BarChart: unknown result_id 'r7'; valid IDs: r1"]


def test_macro_outside_a_request_context_is_a_bug():
    with pytest.raises(RuntimeError, match="outside macro_context"):
        expand("Cite", template="{r1[0].value}")


# --- end to end ----------------------------------------------------------------------------


def test_express_reply_expands_to_transport_that_passes_strict_validation(ctx):
    reply = """Here is what I found.
<a2ui>
surface("main")
root = Column([lead, top, chart, table])
lead = Cite("{r1[1].supplier} received the most.")
top = Metric("r1", "value", "Top supplier", 1, "currency_m")
chart = BarChart("r1", "supplier", "value", "Value by supplier", _, true, "currency_m")
table = DataTable("r1", "Suppliers", "supplier,contracts,value", "value:currency")
</a2ui>"""
    transport = to_transport(reply)
    assert ctx.errors == []
    create = next(m for m in transport if "createSurface" in m)
    assert create["createSurface"]["catalogId"] == EXPLORER_CATALOG_ID
    comps = next(m for m in transport if "updateComponents" in m)["updateComponents"]["components"]
    kinds = {c["component"] for c in comps}
    assert {"VegaChart", "DataGrid", "SqlDetails", "Card"} <= kinds
    assert not kinds & {"BarChart", "LineChart", "DataTable", "Metric", "Cite"}
    ids = [c["id"] for c in comps]
    assert len(ids) == len(set(ids))

    processor = MessageProcessor(
        catalogs=[explorer_catalog()],
        options=MessageProcessorOptions(validation_config=STRICT_VALIDATION),
    )
    processor.process_messages(transport)


def test_strict_validation_rejects_a_bad_custom_component():
    processor = MessageProcessor(
        catalogs=[explorer_catalog()],
        options=MessageProcessorOptions(validation_config=STRICT_VALIDATION),
    )
    bad = [
        {"version": "v0.9", "createSurface": {"surfaceId": "s", "catalogId": EXPLORER_CATALOG_ID}},
        {"version": "v0.9", "updateComponents": {"surfaceId": "s", "components": [
            {"id": "root", "component": "DataGrid", "columns": [], "rows": [[1]]}]}},
    ]
    with pytest.raises(Exception, match="(?i)valid"):
        processor.process_messages(bad)


@pytest.mark.parametrize(
    "date",
    ["2024", "FY 2023-24", "2023–24", "2014-01-01", "1 Jul 2024", "30 June 2025", "July 1, 2024", "June 2025"],
)
def test_dates_are_not_bare_numbers(ctx, date):
    # Dates and years are scope, not data; logging them drowned out the real typed figures.
    filled = fill_prose(f"In {date}, {{r1[1].supplier}} won about 6.6% of value.")
    assert filled.bare_numbers == ["6.6"]


def test_date_range_from_the_live_runs_is_not_bare_numbers(ctx):
    filled = fill_prose("FY 2024-25 (1 Jul 2024–30 Jun 2025): overseas got about 6.6%, from ~$8m to ~$1.57bn.")
    assert filled.bare_numbers == ["6.6", "8", "1.57"]


# --- currency_compact (explorer/specs/names-periods-currency) ------------------------------


def _money_store(values: list, columns=("name", "value", "contracts")) -> ResultStore:
    store = ResultStore()
    rows = [{"name": f"N{i}", "value": v, "contracts": 1000 + i} for i, v in enumerate(values)]
    store.add(sql="s", purpose="p", columns=list(columns), column_types=["text", "numeric", "bigint"],
              rows=rows, truncated=False)
    return store


def test_cite_and_metric_currency_compact():
    with macro_context(_money_store([3_441_812_603.0, 980_000_000.0])):
        metric = by_type(expand("Metric", result_id="r1", column="value", label="L", row=1,
                                format="currency_compact"), "Text")[1]
        cite = expand("Cite", template="{r1[0].value|currency_compact} and {r1[1].value|currency_compact}")[0]
    assert metric["text"] == "$980.0M"
    assert cite["text"] == r"\$3\.4B and \$980\.0M"


def test_data_table_currency_compact_uses_one_unit_per_column():
    with macro_context(_money_store([3_441_812_603.0, 1_200_000_000.0, 980_000_000.0, None])):
        grid = by_type(expand("DataTable", result_id="r1", title="T",
                              format="value:currency_compact"), "DataGrid")[0]
    assert [r[1] for r in grid["rows"]] == ["$3.4B", "$1.2B", "$1.0B", "—"]
    assert [r[2] for r in grid["rows"]] == ["1,000", "1,001", "1,002", "1,003"]


def test_data_table_currency_compact_unit_from_shown_rows():
    with macro_context(_money_store([5_000_000.0, 2_000_000.0, 7_000_000_000.0]), table_row_cap=2):
        grid = by_type(expand("DataTable", result_id="r1", title="T",
                              format="value:currency_compact"), "DataGrid")[0]
    assert [r[1] for r in grid["rows"]] == ["$5.0M", "$2.0M"]


def test_chart_currency_compact_picks_unit_from_data():
    with macro_context(_money_store([38_414_946_047.0, 22_705_188_409.0])):
        spec = by_type(expand("BarChart", result_id="r1", x="name", y="value", title="T",
                              format="currency_compact"), "VegaChart")[0]["spec"]
    assert spec["encoding"]["y"]["title"] == "Value ($B)"
    assert spec["encoding"]["y"]["axis"]["format"] == ",.1f"
    assert [v["value"] for v in spec["data"]["values"]] == [pytest.approx(38.414946047), pytest.approx(22.705188409)]


def test_chart_currency_compact_unit_after_folding():
    store = ResultStore()
    rows = [{"year": "2024", "agency": f"A{i}", "value": 400_000_000.0} for i in range(5)]
    store.add(sql="s", purpose="p", columns=["year", "agency", "value"],
              column_types=["text", "text", "numeric"], rows=rows, truncated=False)
    with macro_context(store, max_color_categories=2):
        spec = by_type(expand("LineChart", result_id="r1", x="year", y="value", title="T", color="agency",
                              format="currency_compact"), "VegaChart")[0]["spec"]
    # A line chart doesn't stack: no single row reaches 1e9, but "Other" folds three into 1.2e9.
    assert spec["encoding"]["y"]["title"] == "Value ($B)"
    values = {v["agency"]: v["value"] for v in spec["data"]["values"]}
    assert values["Other"] == pytest.approx(1.2)


def test_chart_currency_compact_small_values():
    with macro_context(_money_store([812.0, 15.5])):
        spec = by_type(expand("LineChart", result_id="r1", x="name", y="value", title="T",
                              format="currency_compact"), "VegaChart")[0]["spec"]
    assert spec["encoding"]["y"]["title"] == "Value ($)"
    assert spec["encoding"]["y"]["axis"]["format"] == ",.0f"
    assert [v["value"] for v in spec["data"]["values"]] == [812.0, 15.5]


def test_currency_compact_non_finite_values_do_not_crash():
    assert format_value(float("nan"), "currency_compact", "numeric") == "$nan"
    assert format_value(float("inf"), "currency_compact", "numeric") == "$inf"
    with macro_context(_money_store([float("nan"), 2_000_000.0])):
        grid = by_type(expand("DataTable", result_id="r1", title="T",
                              format="value:currency_compact"), "DataGrid")[0]
    assert [r[1] for r in grid["rows"]] == ["$nan", "$2.0M"]


def test_currency_compact_shared_unit_has_no_negative_zero():
    with macro_context(_money_store([38_000_000_000.0, -20_000_000.0, -400_000_000.0])):
        grid = by_type(expand("DataTable", result_id="r1", title="T",
                              format="value:currency_compact"), "DataGrid")[0]
    assert [r[1] for r in grid["rows"]] == ["$38.0B", "$0.0B", "-$0.4B"]


def test_stacked_bar_currency_compact_unit_from_stack_totals():
    store = ResultStore()
    rows = [{"year": "2024", "agency": f"A{i}", "value": 400_000_000.0} for i in range(5)]
    store.add(sql="s", purpose="p", columns=["year", "agency", "value"],
              column_types=["text", "text", "numeric"], rows=rows, truncated=False)
    with macro_context(store):  # all five groups kept: each segment 400M, the stack 2B
        bar = by_type(expand("BarChart", result_id="r1", x="year", y="value", title="T", color="agency",
                             format="currency_compact"), "VegaChart")[0]["spec"]
        line = by_type(expand("LineChart", result_id="r1", x="year", y="value", title="T", color="agency",
                              format="currency_compact"), "VegaChart")[0]["spec"]
    assert bar["encoding"]["y"]["title"] == "Value ($B)"
    assert line["encoding"]["y"]["title"] == "Value ($M)"  # lines don't stack


def test_horizontal_negative_chart_currency_compact():
    with macro_context(_money_store([-2_500_000_000.0, 300_000_000.0]), currency_symbol="€"):
        spec = by_type(expand("BarChart", result_id="r1", x="name", y="value", title="T", horizontal=True,
                              format="currency_compact"), "VegaChart")[0]["spec"]
    assert spec["encoding"]["x"]["title"] == "Value (€B)"
    assert spec["encoding"]["x"]["axis"]["format"] == ",.1f"
    assert spec["encoding"]["tooltip"][-1]["format"] == ",.1f"
    assert [v["value"] for v in spec["data"]["values"]] == [0.3, -2.5]  # displayed order: by value



# Regression: Vega-Lite reads "." and "[]" in a field as a nested path, so a column aliased
# "agency.name" plotted as undefined. Field references are escaped; the data keys are not.
def test_chart_field_references_are_escaped():
    store = ResultStore()
    store.add(sql="s", purpose="p", columns=["agency.name", "value[aud]", "kind.x"],
              column_types=["text", "numeric", "text"],
              rows=[{"agency.name": "A", "value[aud]": 1.0, "kind.x": "k"}], truncated=False)
    with macro_context(store):
        spec = by_type(expand("BarChart", result_id="r1", x="agency.name", y="value[aud]", title="T",
                              color="kind.x"), "VegaChart")[0]["spec"]
    enc = spec["encoding"]
    assert enc["x"]["field"] == "agency\\.name"
    assert enc["y"]["field"] == "value\\[aud\\]"
    assert enc["color"]["field"] == "kind\\.x"
    assert [t["field"] for t in enc["tooltip"]] == ["agency\\.name", "kind\\.x", "value\\[aud\\]"]
    assert spec["data"]["values"][0] == {"agency.name": "A", "kind.x": "k", "value[aud]": 1.0, "_row": 0}
    assert enc["x"]["title"] == "Agency.name"  # titles are labels, not field references


# Regression (scenario review 2026-11-02): "FY2024-25" was counted as typed numbers, because "\b"
# doesn't fire between "FY" and the digits.
@pytest.mark.parametrize("text", ["FY2024-25", "FY24-25", "fy2023–24"])
def test_fy_prefixed_years_are_not_bare_numbers(ctx, text):
    assert fill_prose(f"In {text}, about 6.6% went overseas.").bare_numbers == ["6.6"]


def test_line_chart_is_a_card(ctx):
    comps = expand("LineChart", result_id="r1", x="supplier", y="value", title="T")
    assert kinds(comps) == ["Card", "Column", "SqlDetails", "Text", "VegaChart"]
    card = root(comps)
    column = next(c for c in comps if c["id"] == card["child"])
    assert [next(c for c in comps if c["id"] == i)["component"] for i in column["children"]] == [
        "Text", "VegaChart", "SqlDetails"]



def test_visual_specs_carry_no_vega_config(ctx):
    # Look and feel lives in the client (ADR 0026): the agent sends no colours or fonts.
    for name, args in [("BarChart", {"x": "supplier", "y": "value"}), ("LineChart", {"x": "supplier", "y": "value"})]:
        spec = by_type(expand(name, result_id="r1", title="T", **args), "VegaChart")[0]["spec"]
        assert "config" not in spec
