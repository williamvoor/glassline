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
        (999_960_000.0, "currency_compact", "numeric", "$1B"),
        (999.96, "currency_compact", "numeric", "$1K"),
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
    assert spec["data"]["values"][0] == {"supplier": "Acme_[x]", "value": pytest.approx(3441.812603), "_row": 1, "_label": "Acme_[x]"}
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


@pytest.mark.parametrize("name, args", [
    ("Metric", dict(result_id="r1", column="value", label="L")),
    ("BarChart", dict(result_id="r1", x="supplier", y="value", title="T")),
    ("DataTable", dict(result_id="r1", title="T")),
])
def test_visual_cards_share_a_row_evenly(ctx, name, args):
    # Without a weight a Card in a Row keeps its natural width and leaves blank space to its right.
    assert root(expand(name, **args))["weight"] == 1


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
    assert metric["text"] == "$980M"
    assert cite["text"] == r"\$3\.4B and \$980M"


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
    assert spec["data"]["values"][0] == {"agency.name": "A", "kind.x": "k", "value[aud]": 1.0, "_row": 0, "_label": "A · k"}
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


# --- Boards (SPEC §18): Treemap, bar cells, Board mode, check_columns --------------------------

import json
import os
from pathlib import Path

from explorer_agent.macros import MAX_TREEMAP_TILES, NO_ROWS, check_columns
from explorer_agent.results import StoredResult

TREEMAP_FIXTURE = Path(__file__).resolve().parents[2] / "client" / "test" / "fixtures" / "treemap_spec.json"


def _add(store: ResultStore, rows: list[dict], *, columns=("name", "value"), types=("text", "numeric"),
         truncated: bool = False) -> StoredResult:
    return store.add(sql="SELECT name, value FROM t", purpose="tiles", columns=list(columns),
                     column_types=list(types), rows=rows, truncated=truncated)


def _treemap(comps: list[dict]) -> tuple[dict, dict]:
    chart = by_type(comps, "VegaChart")[0]
    return chart, chart["spec"]


def _tiles(spec: dict) -> list[dict]:
    return [d for d in spec["data"][0]["values"] if d["_parent"] is not None]


def test_treemap_is_full_vega_with_one_tile_per_row_and_no_root_mark():
    s = ResultStore()
    _add(s, [{"name": "B", "value": 20.0}, {"name": "A", "value": 50.0}, {"name": "C", "value": 20.0}])
    with macro_context(s):
        comps = expand("Treemap", result_id="r1", title="By name", label="name", value="value", format="int")
    assert kinds(comps) == ["Card", "Column", "SqlDetails", "Text", "VegaChart"]
    chart, spec = _treemap(comps)
    assert spec["$schema"].endswith("/vega/v5.json")
    root_datum = spec["data"][0]["values"][0]
    assert root_datum == {"_id": "root", "_parent": None}  # the root has no _row, and isn't drawn:
    assert spec["data"][1]["transform"] == [{"type": "filter", "expr": "datum._parent != null"}]
    assert all(m["from"]["data"] == "tiles" for m in spec["marks"])
    tiles = _tiles(spec)
    # Displayed order: by value descending, ties by label ascending.
    assert [(t["_name"], t["_row"], t["_label"], t["_value"]) for t in tiles] == [
        ("A", 1, "A", "50"), ("B", 0, "B", "20"), ("C", 2, "C", "20"),
    ]
    assert chart["action"]["rows"] == [1, 0, 2]
    assert "config" not in spec  # ADR 0026
    # The pointer on clickable tiles only, from the item's own cursor (Vega overwrites a container style).
    assert spec["marks"][0]["encode"]["update"]["cursor"] == {"signal": "isValid(datum._row) ? 'pointer' : null"}


def test_an_unclickable_treemap_has_no_pointer():
    s = ResultStore()
    _add(s, [{"name": "A", "value": 50.0}])
    with macro_context(s):
        comps = expand("Treemap", result_id="r1", title="T", label="name", value="value", clickable=False)
    assert "cursor" not in _treemap(comps)[1]["marks"][0]["encode"]["update"]


def test_treemap_folds_the_rest_into_a_non_clickable_other_and_counts_left_out_rows():
    s = ResultStore()
    rows = [{"name": f"n{i:02}", "value": float(100 - i)} for i in range(MAX_TREEMAP_TILES + 3)]
    rows += [{"name": "zero", "value": 0.0}, {"name": "neg", "value": -5.0}, {"name": "null", "value": None}]
    _add(s, rows)
    with macro_context(s):
        comps = expand("Treemap", result_id="r1", title="T", label="name", value="value")
    _, spec = _treemap(comps)
    tiles = _tiles(spec)
    assert len(tiles) == MAX_TREEMAP_TILES + 1
    other = tiles[-1]
    assert other["_name"] == "Other" and "_row" not in other and "_label" not in other
    assert other["_size"] == sum(100 - i for i in range(MAX_TREEMAP_TILES, MAX_TREEMAP_TILES + 3))
    caption = [t["text"] for t in by_type(comps, "Text") if t.get("variant") == "caption"][0]
    assert "3 smaller rows in “Other”" in caption
    assert "3 rows with no positive value left out" in caption


def test_treemap_other_is_incomplete_when_the_result_was_truncated():
    s = ResultStore()
    _add(s, [{"name": f"n{i:02}", "value": float(100 - i)} for i in range(MAX_TREEMAP_TILES + 1)], truncated=True)
    with macro_context(s):
        comps = expand("Treemap", result_id="r1", title="T", label="name", value="value")
    assert _tiles(_treemap(comps)[1])[-1]["_name"] == "Other (incomplete)"
    caption = [t["text"] for t in by_type(comps, "Text") if t.get("variant") == "caption"][0]
    assert "incomplete" in caption


def test_treemap_without_positive_values_is_a_macro_error_in_answers():
    s = ResultStore()
    _add(s, [{"name": "a", "value": 0.0}])
    with macro_context(s), pytest.raises(MacroError, match="no rows with a positive"):
        expand("Treemap", result_id="r1", title="T", label="name", value="value")


def test_treemap_value_must_be_numeric(ctx):
    with pytest.raises(MacroError, match="not a number"):
        expand("Treemap", result_id="r1", title="T", label="value", value="supplier")


def test_treemap_spec_fixture_for_the_client_is_current():
    """The client renders this spec with real Vega (B14, B17)."""
    s = ResultStore()
    _add(s, [{"name": "Alpha", "value": 60.0}, {"name": "Beta", "value": 30.0}, {"name": "Gamma", "value": 10.0}])
    with macro_context(s):
        comps = expand("Treemap", result_id="r1", title="T", label="name", value="value", format="int")
    current = json.dumps(_treemap(comps)[1], indent=2, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_FIXTURES") == "1":
        TREEMAP_FIXTURE.write_text(current, encoding="utf-8")
    assert TREEMAP_FIXTURE.read_text(encoding="utf-8") == current


def test_treemap_passes_strict_validation(store):
    msgs = to_transport_with(store, '<a2ui>\nroot = Column([t])\nt = Treemap("r1", "T", "supplier", "value")\n</a2ui>')
    assert any(c["component"] == "VegaChart" for m in msgs if "updateComponents" in m for c in m["updateComponents"]["components"])


def to_transport_with(store: ResultStore, reply: str) -> list[dict]:
    from explorer_agent.ui import validate

    with macro_context(store):
        msgs = to_transport(reply)
    validate(msgs)
    return msgs


def test_bar_cells_are_shares_of_the_largest_shown_value(store):
    store.add(sql="SELECT 1", purpose="p", columns=["name", "value", "n"], column_types=["text", "numeric", "bigint"],
              rows=[{"name": "a", "value": 50.0, "n": 0}, {"name": "b", "value": None, "n": 0},
                    {"name": "c", "value": 200.0, "n": 0}], truncated=False)
    with macro_context(store):
        comps = expand("DataTable", result_id="r2", title="T", bars="value,n")
    columns = by_type(comps, "DataGrid")[0]["columns"]
    by_key = {c["key"]: c for c in columns}
    assert by_key["value"]["bars"] == [0.25, None, 1.0]
    assert "bars" not in by_key["n"]  # every shown value zero: no bars
    assert "bars" not in by_key["name"]
    rows = by_type(comps, "DataGrid")[0]["rows"]
    assert rows[0][1] == "50.00"  # the text stays the formatted value


def test_bar_cells_reject_negatives_and_columns_not_shown_or_not_numeric(store):
    store.add(sql="SELECT 1", purpose="p", columns=["name", "value"], column_types=["text", "numeric"],
              rows=[{"name": "a", "value": -1.0}], truncated=False)
    with macro_context(store):
        with pytest.raises(MacroError, match="negative"):
            expand("DataTable", result_id="r2", title="T", bars="value")
        with pytest.raises(MacroError, match="not one of the shown columns"):
            expand("DataTable", result_id="r2", title="T", columns="name", bars="value")
        with pytest.raises(MacroError, match="not a number"):
            expand("DataTable", result_id="r2", title="T", bars="name")


def test_bar_cells_pass_strict_validation(store):
    to_transport_with(store, '<a2ui>\nroot = Column([t])\nt = DataTable("r1", "T", "supplier,value", "", true, "", "table", "", "", "", "value")\n</a2ui>')


def _board_store() -> ResultStore:
    s = ResultStore()
    _add(s, [], columns=("name", "value"), types=("text", "numeric"))
    return s


class _Named:
    """A Board's results by query name (what boards.py hands macro_context)."""

    def __init__(self, results: dict[str, StoredResult]):
        self.results = results

    def get(self, key: str):
        return self.results.get(key)

    def ids(self) -> list[str]:
        return list(self.results)


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("Metric", {"column": "value", "label": "Total"}),
        ("BarChart", {"x": "name", "y": "value", "title": "Bars"}),
        ("LineChart", {"x": "name", "y": "value", "title": "Line"}),
        ("Treemap", {"label": "name", "value": "value", "title": "Tiles"}),
        ("Cite", {"template": "Top is {q[0].name}"}),
    ],
)
def test_board_mode_turns_zero_rows_into_a_no_rows_card(name, args):
    empty = _board_store().get("r1")
    with macro_context(_Named({"q": empty}), board=True):
        comps = expand(name, **({"result_id": "q"} if name != "Cite" else {}), **args)
    texts = [c["text"] for c in by_type(comps, "Text")]
    assert NO_ROWS in texts
    assert by_type(comps, "SqlDetails")  # with its SQL
    assert root(comps)["component"] == "Card"


def test_zero_rows_stay_a_repair_in_answers():
    with macro_context(_board_store()), pytest.raises(MacroError, match="row 0 does not exist"):
        expand("Metric", result_id="r1", column="value", label="Total")


def test_board_mode_failed_query_gives_an_error_card_without_values():
    with macro_context(_Named({}), board=True, failed={"q": "57014"}):
        comps = expand("BarChart", result_id="q", x="name", y="value", title="Bars")
    assert [c["text"] for c in by_type(comps, "Text")] == ["This panel couldn't load: 57014"]


def test_board_placeholders_name_queries_and_board_visuals_are_clickable(store):
    with macro_context(_Named({"top": store.get("r1")}), board=True) as c:
        comps = expand("BarChart", result_id="top", x="supplier", y="value", title="Top: {top[0].supplier}")
    assert by_type(comps, "Text")[0]["text"] == "Top: Beta"
    assert by_type(comps, "VegaChart")[0]["action"]["source"] == c.sources[0].id


def _drill_args(sql: str) -> dict:
    return {"result_id": "top", "x": "supplier", "y": "value", "title": "T", "drill_sql": sql}


def test_board_mode_has_no_drill_cap(store):
    """SPEC §18: MAX_DRILLS_PER_SURFACE doesn't apply to a Board, which a person reviewed."""
    sql = "SELECT * FROM fixture.contracts WHERE supplier IS NOT DISTINCT FROM :supplier"
    with macro_context(_Named({"top": store.get("r1")}), board=True, max_drills=1, allowed_schemas=("fixture",)) as c:
        for _ in range(3):
            expand("BarChart", **_drill_args(sql))
    assert len(c.drills) == 3
    with macro_context(store, max_drills=1, allowed_schemas=("fixture",)), pytest.raises(MacroError, match="at most 1 Visuals"):
        expand("BarChart", **{**_drill_args(sql), "result_id": "r1"})
        expand("BarChart", **{**_drill_args(sql), "result_id": "r1"})


def test_board_mode_drops_a_row_dependent_drill_and_keeps_the_ask(caplog):
    """SPEC §18 slice 1b: a NULL under `=` in a clickable row drops only the Drill query."""
    s = ResultStore()
    r = s.add(sql="SELECT 1", purpose="p", columns=["supplier", "value"], column_types=["text", "numeric"],
              rows=[{"supplier": "A", "value": 2.0}, {"supplier": None, "value": 1.0}], truncated=False)
    sql = "SELECT * FROM fixture.contracts WHERE supplier = :supplier"
    with macro_context(_Named({"top": r}), board=True, allowed_schemas=("fixture",)) as c:
        comps = expand("BarChart", **_drill_args(sql))
    action = by_type(comps, "VegaChart")[0]["action"]
    assert action["drillable"] is False and action["source"] == c.sources[0].id
    assert c.drills == [] and c.errors == []
    assert "board drill query dropped: visual=0" in caplog.text
    assert "'A'" not in caplog.text
    with macro_context(s, allowed_schemas=("fixture",)), pytest.raises(MacroError, match="IS NOT DISTINCT FROM"):
        expand("BarChart", **{**_drill_args(sql), "result_id": r.id})


def test_board_mode_drops_a_drill_on_a_list_column_and_keeps_the_ask():
    s = ResultStore()
    r = s.add(sql="SELECT 1", purpose="p", columns=["supplier", "tags", "value"], column_types=["text", "text[]", "numeric"],
              rows=[{"supplier": "A", "tags": ["a", "b"], "value": 2.0}], truncated=False)
    sql = "SELECT * FROM fixture.contracts WHERE tags IS NOT DISTINCT FROM :tags"
    with macro_context(_Named({"top": r}), board=True, allowed_schemas=("fixture",)) as c:
        comps = expand("BarChart", **_drill_args(sql))
    assert by_type(comps, "VegaChart")[0]["action"]["drillable"] is False
    assert c.drills == [] and len(c.sources) == 1


def test_check_columns_checks_drill_title_placeholders():
    """A bad drill_title would blank the panel at render (refuter, slice 1b)."""
    outputs = {"q": (["name", "value"], ["text", "numeric"])}
    comps = [{"id": "a", "component": "BarChart", "result_id": "q", "x": "name", "y": "value", "title": "T",
              "drill_title": "{nope[0].x}"}]
    assert check_columns(comps, outputs) == ["a: placeholder names unknown query 'nope'"]


def test_check_columns_reports_unknown_queries_columns_and_kinds():
    outputs = {"q": (["name", "value", "day"], ["text", "numeric", "date"])}
    comps = [
        {"id": "a", "component": "BarChart", "result_id": "q", "x": "name", "y": "name", "title": "T"},
        {"id": "b", "component": "Treemap", "result_id": "q", "label": "nope", "value": "value", "title": "T"},
        {"id": "c", "component": "DataTable", "result_id": "q", "title": "T", "columns": "name", "bars": "value"},
        {"id": "d", "component": "Metric", "result_id": "other", "column": "value", "label": "L"},
        {"id": "e", "component": "Cite", "template": "{q[0].missing} and {zz[0].value}"},
        {"id": "f", "component": "Text", "text": "Total {q[0].value|currency}"},
        {"id": "g", "component": "LineChart", "result_id": "q", "x": "day", "y": "value", "title": "{q[0].nope}"},
        {"id": "h", "component": "DataTable", "result_id": "q", "title": "T", "bars": "name", "format": "gone:int"},
    ]
    problems = check_columns(comps, outputs)
    assert problems == [
        "a: y column 'name' of query 'q' is text, not a number",
        "b: label column 'nope' is not an output column of query 'q'",
        "c: bars column 'value' is not a shown column",
        "d: Metric names unknown query 'other'; queries are: q",
        "e: placeholder column 'missing' is not an output column of query 'q'",
        "e: placeholder names unknown query 'zz'",
        "g: placeholder column 'nope' is not an output column of query 'q'",
        "h: format column 'gone' is not an output column of query 'q'",
        "h: bars column 'name' of query 'q' is text, not a number",
    ]
    assert check_columns(comps[5:6] + comps[:0], outputs) == []


def test_the_visual_index_counts_treemaps(store):
    with macro_context(store) as c:
        expand("Treemap", result_id="r1", title="T", label="supplier", value="value")
        expand("BarChart", result_id="r1", x="supplier", y="value", title="B")
    assert c.visuals == 2
    assert [s.visual for s in c.sources] == [0, 1]


def test_treemap_drill_links_work_as_for_bar_charts():
    s = ResultStore()
    _add(s, [{"name": "B", "value": 20.0}, {"name": "A", "value": 50.0}])
    with macro_context(s, allowed_schemas=("mart",)) as c:
        comps = expand("Treemap", result_id="r1", title="T", label="name", value="value",
                       drill_sql="SELECT x FROM mart.t WHERE name IS NOT DISTINCT FROM :name")
    action = _treemap(comps)[0]["action"]
    assert action["drillable"] is True
    assert action["rows"] == [1, 0]  # displayed order: by value
    assert c.drills[0].names == ("name",) and c.drills[0].source == action["source"]


# --- issue 9: escape filled values only where the text renders as markdown ---------------------
# web_core renders h1–h5 and caption Text (and Tabs titles) as plain text, so escaping there shows
# backslashes. Body Text, Cite, prose and FollowUp labels render markdown and keep escaping.

from explorer_agent.macros import fill_surface_text


def test_visual_titles_show_filled_values_as_written(ctx):
    bar = expand("BarChart", result_id="r1", x="supplier", y="value", title="Top: {r1[1].supplier}")
    table = expand("DataTable", result_id="r1", title="Top: {r1[1].supplier}")
    for comps in (bar, table):
        title = by_type(comps, "Text")[0]
        assert (title["text"], title["variant"]) == ("Top: Acme_[x]", "h4")


def test_metric_label_shows_filled_values_as_written(ctx):
    texts = by_type(expand("Metric", result_id="r1", column="value", label="{r1[1].supplier}", row=1), "Text")
    assert (texts[0]["text"], texts[0]["variant"]) == ("Acme_[x]", "caption")


def test_follow_up_label_stays_escaped(ctx):
    texts = by_type(expand("FollowUp", label="More on {r1[1].supplier}", request="Tell me more"), "Text")
    assert [t["text"] for t in texts] == [r"More on Acme\_\[x\]"]


def test_surface_text_is_escaped_by_variant_and_tabs_titles_are_plain(ctx):
    comps = [
        {"id": "h", "component": "Text", "text": "{r1[1].supplier}", "variant": "h3"},
        {"id": "c", "component": "Text", "text": "{r1[1].supplier}", "variant": "caption"},
        {"id": "b", "component": "Text", "text": "{r1[1].supplier}", "variant": "body"},
        {"id": "d", "component": "Text", "text": "{r1[1].supplier}"},
        {"id": "t", "component": "Tabs", "tabs": [{"title": "{r1[1].supplier}", "child": "h"}]},
    ]
    fill_surface_text(comps)
    assert [c.get("text") for c in comps[:4]] == ["Acme_[x]", "Acme_[x]", r"Acme\_\[x\]", r"Acme\_\[x\]"]
    assert comps[4]["tabs"][0]["title"] == "Acme_[x]"


# M1 (2026-11-05): a Dataset table showed years as "2,015" and numeric(12,0) prices as
# "1,150,000.00" under the default format.
@pytest.mark.parametrize("column", ["year", "financial_year", "calendar_year"])
def test_auto_shows_a_year_column_without_a_separator(column):
    assert format_value(2015, "auto", "smallint", column=column) == "2015"


@pytest.mark.parametrize(("column", "value", "expected"), [
    ("year_count", 2015, "2,015"),  # not a year: a count of something
    ("year", 123_456, "123,456"),   # not a plausible year
    ("total", 2015, "2,015"),
])
def test_auto_keeps_the_separator_for_other_integers(column, value, expected):
    assert format_value(value, "auto", "integer", column=column) == expected


def test_auto_shows_a_whole_numeric_value_without_decimals():
    # Postgres returns numeric(12,0) and SUM(bigint) as a Decimal with no fractional digits.
    from decimal import Decimal

    from explorer_agent.results import to_json_safe

    assert format_value(to_json_safe(Decimal("1150000")), "auto", "numeric") == "1,150,000"
    assert format_value(to_json_safe(Decimal("1150000.50")), "auto", "numeric") == "1,150,000.50"
    assert format_value(to_json_safe(Decimal("12.00")), "auto", "numeric") == "12.00"  # scale 2 is kept


# M1 (2026-11-05): a Metric read "$770.0K".
@pytest.mark.parametrize(("value", "expected"), [
    (770_000, "$770K"), (2_000_000.0, "$2M"), (-3_000_000_000, "-$3B"), (770_500, "$770.5K"),
])
def test_currency_compact_drops_a_trailing_zero_decimal(value, expected):
    assert format_value(value, "currency_compact", "numeric") == expected


def test_auto_shows_a_whole_float_year_without_a_separator():
    # date_part('year', …) returns double precision.
    assert format_value(2015.0, "auto", "double precision", column="year") == "2015"
    assert format_value(2015.5, "auto", "double precision", column="year") == "2,015.50"
