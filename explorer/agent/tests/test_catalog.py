from explorer_agent.catalog import EXPLORER_CATALOG_ID, custom_components, explorer_catalog
from explorer_agent.macros import MACROS, PASSTHROUGH_COMPONENTS, macro_expander

CUSTOM = {"VegaChart", "DataGrid", "SqlDetails"}


def test_merged_catalog_has_basic_and_custom_components():
    cat = explorer_catalog()
    assert cat.catalog_id == EXPLORER_CATALOG_ID
    assert set(custom_components()) == CUSTOM
    for name in CUSTOM | {"Column", "Text", "Card", "Button", "List"}:
        assert cat.get_component(name) is not None, name


def test_inference_catalog_exposes_only_macros_and_passthrough():
    inference = macro_expander().transform_to_inference_catalog(explorer_catalog())
    names = set(inference.to_json()["components"])
    assert names == {"BarChart", "LineChart", "Treemap", "Timeline", "DataTable", "Metric", "Cite", "FollowUp"} | set(PASSTHROUGH_COMPONENTS)
    assert len(MACROS) == 8


def test_macro_schemas_keep_parameter_types_and_order():
    comps = macro_expander().transform_to_inference_catalog(explorer_catalog()).to_json()["components"]
    bar = comps["BarChart"]
    assert list(bar["properties"]) == [
        "result_id", "x", "y", "title", "color", "horizontal", "format",
        "clickable", "drill_sql", "drill_as", "drill_x", "drill_y", "drill_title",
    ]
    assert bar["required"] == ["result_id", "x", "y", "title"]
    assert bar["properties"]["horizontal"]["type"] == "boolean"
    assert comps["Metric"]["properties"]["row"]["type"] == "integer"
