"""S1, S2: the catalog model is read from the SDK's tables and the catalog JSON."""

import copy
import json

from a2ui.inference_formats._shared.catalogs import build_catalog_helpers

from a2ui_eval.sdk import ExpressSDK
from a2ui_eval.catalog_model import CatalogModel
from tests.conftest import CATALOG


def test_matches_sdk_helper(sdk_v0_9):
    model = CatalogModel(sdk_v0_9)
    h = next(iter(build_catalog_helpers([sdk_v0_9.catalog]).values()))
    assert set(model.components) == set(h.components)
    for name, comp in model.components.items():
        assert [s.name for s in comp.slots] == h.get_component_properties(name)
        assert set(comp.required) == set(h.get_component_required(name)) - {"component"}
        assert comp.checkable == h.is_checkable(name)
        for s in comp.slots:
            if s.kind == "static-enum":
                assert list(s.enum) == h.get_property_enum(name, s.name)
    assert set(model.functions) == set(sdk_v0_9.catalog_json["functions"])
    for name, fn in model.functions.items():
        assert [a.name for a in fn.args] == h.get_function_properties(name)


def test_function_roles(sdk_v0_9):
    model = CatalogModel(sdk_v0_9)
    assert {f.name for f in model.functions_by_role("action")} == {"openUrl"}
    assert {"formatString", "formatNumber", "formatCurrency", "formatDate", "pluralize"} == {
        f.name for f in model.functions_by_role("value")
    }
    assert {"required", "email", "regex", "length", "numeric", "and", "or", "not"} == {
        f.name for f in model.functions_by_role("check")
    }
    assert model.functions["length"].one_of_required == (("min",), ("max",))


def test_slider_order(sdk_v0_9):
    slider = CatalogModel(sdk_v0_9).components["Slider"]
    assert [s.name for s in slider.slots][:4] == ["label", "min", "max", "value"]
    assert slider.slots[-1].kind == "checks"
    assert set(slider.required) == {"value", "max"}


def test_map_lists_literal(sdk_v0_9):
    model = CatalogModel(sdk_v0_9)
    tabs = model.components["Tabs"].slot("tabs")
    options = model.components["ChoicePicker"].slot("options")
    assert (tabs.kind, tabs.bindable, tabs.item_fields) == ("map-list", False, ("title", "child"))
    assert (options.kind, options.bindable, options.item_fields) == ("map-list", False, ("label", "value"))


def test_slot_kinds(sdk_v0_9):
    model = CatalogModel(sdk_v0_9)
    kind = lambda c, p: model.components[c].slot(p).kind  # noqa: E731
    assert kind("Text", "text") == "text"
    assert kind("Text", "variant") == "static-enum"
    assert kind("Icon", "name") == "static-enum"
    assert kind("Button", "action") == "action"
    assert kind("Button", "child") == "child"
    assert kind("Row", "children") == "children"
    assert kind("CheckBox", "value") == "dynamic"
    assert model.components["CheckBox"].slot("value").value_type == "boolean"
    assert kind("TextField", "validationRegexp") == "static-literal"
    assert "accountCircle" in model.enum("Icon", "name")


def test_reads_catalog_changes(tmp_path):
    catalog = json.loads(CATALOG.read_text())
    changed = copy.deepcopy(catalog)
    gauge = copy.deepcopy(changed["components"]["Divider"])
    own = gauge["allOf"][-1]  # the component's specific sub-schema
    own["properties"]["component"] = {"const": "Gauge"}
    own["properties"]["level"] = {"type": "number", "description": "fake"}
    own["required"] = ["component", "level"]
    changed["components"]["Gauge"] = gauge
    changed["$defs"]["anyComponent"]["oneOf"].append({"$ref": "#/components/Gauge"})
    changed["components"]["Text"]["allOf"][-1]["properties"]["tone"] = {"type": "string", "enum": ["calm", "loud"]}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(changed))

    model = CatalogModel(ExpressSDK(path))
    assert "Gauge" in model.components
    assert model.components["Gauge"].slot("level").required
    assert model.components["Text"].slot("tone").enum == ("calm", "loud")


def test_boolean_inputs(sdk_v0_9):
    """Item 7 amendment: the inputs `required` can never block come from the catalog."""
    assert CatalogModel(sdk_v0_9).boolean_inputs == {"CheckBox"}
