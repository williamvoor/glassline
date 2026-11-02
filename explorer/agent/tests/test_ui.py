import json
import logging
import os
from pathlib import Path

import pytest

from explorer_agent.catalog import EXPLORER_CATALOG_ID
from explorer_agent.results import ResultStore
from explorer_agent.testing.stub_server import FIXED_REPLY, seed_fixture
from explorer_agent.catalog import explorer_catalog
from explorer_agent.ui import BARE_NUMBERS_LOG, UiError, UiPipeline, check_known_props, known_props, validate

CLIENT_FIXTURE = Path(__file__).resolve().parents[2] / "client" / "test" / "fixtures" / "slice_transport.json"
MACRO_NAMES = {"BarChart", "LineChart", "DataTable", "Metric", "Cite"}


@pytest.fixture
def fixture_store() -> ResultStore:
    s = ResultStore()
    seed_fixture(s)
    return s


@pytest.fixture(scope="module")
def pipeline() -> UiPipeline:
    return UiPipeline()


def surface_ids(messages: list[dict]) -> set[str]:
    return {body["surfaceId"] for m in messages for body in m.values() if isinstance(body, dict) and "surfaceId" in body}


def components(messages: list[dict]) -> list[dict]:
    return [c for m in messages if "updateComponents" in m for c in m["updateComponents"]["components"]]


def test_fixed_reply_builds_turn_surface(pipeline, fixture_store):
    ui = pipeline.build(FIXED_REPLY, fixture_store, "turn-1")
    create = next(m for m in ui.messages if "createSurface" in m)
    assert create["createSurface"]["catalogId"] == EXPLORER_CATALOG_ID
    assert surface_ids(ui.messages) == {"turn-1"}
    kinds = {c["component"] for c in components(ui.messages)}
    assert {"VegaChart", "DataGrid", "SqlDetails", "Card"} <= kinds
    assert not kinds & MACRO_NAMES
    assert ui.prose == "**Supplier A** received the most, \\$3,441\\.8M in total."
    assert ui.bare_numbers == []


@pytest.mark.parametrize("surface_line", ['surface("main")\n', 'surface("elsewhere")\n', ""])
def test_surface_is_rewritten_whatever_the_model_names(pipeline, fixture_store, surface_line):
    reply = f'<a2ui>\n{surface_line}root = Column([t])\nt = Text("hello")\n</a2ui>'
    ui = pipeline.build(reply, fixture_store, "turn-7")
    assert ui.messages
    assert surface_ids(ui.messages) == {"turn-7"}


def test_macro_error_raises_ui_error(pipeline, fixture_store):
    reply = '<a2ui>\nroot = Column([c])\nc = BarChart("r9", "supplier", "value", "T")\n</a2ui>'
    with pytest.raises(UiError) as e:
        pipeline.build(reply, fixture_store, "turn-1")
    assert any("'r9'" in m and "r1" in m for m in e.value.messages)


def test_prose_placeholder_error_raises_ui_error(pipeline, fixture_store):
    with pytest.raises(UiError) as e:
        pipeline.build("Top is {r1[0].nope}.", fixture_store, "turn-1")
    assert any("nope" in m for m in e.value.messages)


def test_malformed_express_raises_ui_error(pipeline, fixture_store):
    with pytest.raises(UiError, match="parse"):
        pipeline.build("Hi\n<a2ui>\nroot = Column([a\n</a2ui>", fixture_store, "turn-1")


@pytest.mark.parametrize(
    "reply",
    [
        "<a2ui>\nroot = Column([zzz])\n</a2ui>",  # dangling child
        '<a2ui>\nroot = Text("a")\n</a2ui>\n<a2ui>\nroot = Text("b")\n</a2ui>',  # two `main` surfaces
        '<a2ui>\nroot = Column([c])\nc = BarChart("r1", 5, 6, 7)\n</a2ui>',  # wrong argument types
        '<a2ui>\nsurface("a")\nroot = Text("a")\n</a2ui>\n<a2ui>\nsurface("b")\nroot = Text("b")\n</a2ui>',
    ],
    ids=["dangling-child", "two-blocks", "bad-arg-types", "two-named-surfaces"],
)
def test_any_sdk_error_in_the_reply_raises_ui_error(pipeline, fixture_store, reply):
    """Every model-reachable SDK error must reach the model as feedback, not a generic crash."""
    with pytest.raises(UiError):
        pipeline.build(reply, fixture_store, "turn-1")


def test_prose_only_reply(pipeline, fixture_store):
    ui = pipeline.build("Just words, {r1[1].supplier}.", fixture_store, "turn-1")
    assert ui.messages == []
    assert ui.prose == "Just words, Supplier B."


def test_bare_number_is_logged(pipeline, fixture_store, caplog):
    with caplog.at_level(logging.WARNING, logger="explorer_agent.ui"):
        ui = pipeline.build("About 812 million went to {r1[1].supplier}.", fixture_store, "turn-1")
    assert ui.bare_numbers == ["812"]
    assert any(r.getMessage() == BARE_NUMBERS_LOG + "812" for r in caplog.records)


def test_validation_failure_raises_ui_error():
    bad = [
        {"version": "v0.9", "createSurface": {"surfaceId": "s", "catalogId": EXPLORER_CATALOG_ID}},
        {"version": "v0.9", "updateComponents": {"surfaceId": "s", "components": [
            {"id": "root", "component": "DataGrid", "columns": [], "rows": [[1]]}]}},
    ]
    with pytest.raises(UiError, match="invalid UI"):
        validate(bad)


def test_client_fixture_is_current(pipeline, fixture_store):
    """The client's render test uses this transport; regenerate with UPDATE_FIXTURES=1."""
    ui = pipeline.build(FIXED_REPLY, fixture_store, "turn-1")
    current = json.dumps(ui.messages, indent=2, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_FIXTURES") == "1":
        CLIENT_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        CLIENT_FIXTURE.write_text(current, encoding="utf-8")
    assert CLIENT_FIXTURE.read_text(encoding="utf-8") == current


# U1: props the merged catalog doesn't define are rejected (ADR 0005)
def _update(*comps):
    return [{"version": "v0.9", "updateComponents": {"surfaceId": "s", "components": list(comps)}}]


def test_unknown_prop_rejected():
    msgs = _update(
        {"id": "root", "component": "Column", "children": ["name"]},
        {"id": "name", "component": "TextField", "label": "Name", "value": "x", "placeholder": "type here"},
    )
    with pytest.raises(UiError) as e:
        check_known_props(msgs, explorer_catalog())
    assert e.value.messages == ["TextField 'name' has no property 'placeholder'"]


def test_envelope_keys_allowed():
    msgs = _update(
        {"id": "t", "component": "Text", "text": "hi", "accessibility": {"label": "greeting"}, "weight": 1},
        {"id": "b", "component": "Button", "child": "t", "action": {"event": {"name": "go"}}, "checks": []},
        {"id": "g", "component": "DataGrid", "columns": [], "rows": [], "caption": "c"},
    )
    check_known_props(msgs, explorer_catalog())


def test_custom_components_do_not_get_basic_common_props():
    with pytest.raises(UiError, match="DataGrid 'g' has no property 'weight'"):
        check_known_props(_update({"id": "g", "component": "DataGrid", "columns": [], "rows": [], "weight": 1}),
                          explorer_catalog())


def test_every_component_schema_resolves():
    props = known_props(explorer_catalog())
    assert set(props) == set(explorer_catalog().components)
    assert all({"id", "component"} <= p for p in props.values())


# U2
def test_currency_from_dataset(fixture_store):
    ui = UiPipeline(currency_symbol="€").build(FIXED_REPLY, fixture_store, "turn-1")
    assert "€" in ui.prose and "$" not in ui.prose
    rendered = json.dumps(ui.messages, ensure_ascii=False)
    assert "€" in rendered and "$" not in rendered.replace("$schema", "")


# U1 through the pipeline: the parser can't see props that macros add, so the check runs on
# expanded output. A macro that emits an unknown prop must fail the build.
def test_pipeline_runs_unknown_prop_check(monkeypatch, fixture_store):
    from a2ui.transformers.macros import MacroExpander

    real = MacroExpander.transform_to_transport

    def leaky(self, messages):
        out = real(self, messages)
        for m in out:
            comps = getattr(getattr(m, "update_components", None), "components", None)
            if comps:
                comps[0]["placeholder"] = "leaked"
        return out

    monkeypatch.setattr(MacroExpander, "transform_to_transport", leaky)
    with pytest.raises(UiError, match="has no property 'placeholder'"):
        UiPipeline().build(FIXED_REPLY, fixture_store, "turn-1")


# Text the model writes on the Surface follows the prose rules: placeholders are filled from
# Results, and numbers it typed itself are logged (ADR 0001).


def _text(messages: list[dict], cid: str) -> str:
    return next(c["text"] for c in components(messages) if c["id"] == cid)


def test_surface_text_placeholder_is_filled(pipeline, fixture_store):
    reply = '<a2ui>\nroot = Column([t])\nt = Text("Top is {r1[0].supplier} at {r1[0].value|currency_m}.")\n</a2ui>'
    ui = pipeline.build(reply, fixture_store, "turn-1")
    assert _text(ui.messages, "t") == "Top is Supplier A at \\$3,441\\.8M."


def test_surface_text_placeholder_error_raises_ui_error(pipeline, fixture_store):
    reply = '<a2ui>\nroot = Column([t])\nt = Text("Top is {r1[0].nope}.")\n</a2ui>'
    with pytest.raises(UiError) as e:
        pipeline.build(reply, fixture_store, "turn-1")
    assert any("nope" in m and "'t'" in m for m in e.value.messages)


def test_surface_text_bare_number_is_logged(pipeline, fixture_store, caplog):
    reply = 'Hi.\n<a2ui>\nroot = Column([t])\nt = Text("9 of 10 went to {r1[0].supplier}.")\n</a2ui>'
    with caplog.at_level(logging.WARNING, logger="explorer_agent.ui"):
        ui = pipeline.build(reply, fixture_store, "turn-1")
    assert ui.bare_numbers == ["9", "10"]
    assert any("9, 10" in r.getMessage() for r in caplog.records)


def test_macro_output_is_not_counted_as_bare_numbers(pipeline, fixture_store):
    ui = pipeline.build(FIXED_REPLY, fixture_store, "turn-1")
    assert ui.bare_numbers == []


@pytest.mark.parametrize(
    "call",
    [
        'BarChart("r1", "supplier", "value", "Led by {r1[0].supplier}")',
        'LineChart("r1", "supplier", "value", "Led by {r1[0].supplier}")',
        'DataTable("r1", "Led by {r1[0].supplier}")',
        'Metric("r1", "value", "Led by {r1[0].supplier}")',
    ],
    ids=["BarChart", "LineChart", "DataTable", "Metric"],
)
def test_macro_title_placeholder_is_filled(pipeline, fixture_store, call):
    ui = pipeline.build(f"<a2ui>\nroot = Column([c])\nc = {call}\n</a2ui>", fixture_store, "turn-1")
    texts = [c["text"] for c in components(ui.messages) if c["component"] == "Text"]
    assert "Led by Supplier A" in texts
    assert not any("{r1" in t for t in texts)


def test_macro_title_placeholder_error_raises_ui_error(pipeline, fixture_store):
    reply = '<a2ui>\nroot = Column([c])\nc = Metric("r1", "value", "Led by {r1[0].nope}")\n</a2ui>'
    with pytest.raises(UiError) as e:
        pipeline.build(reply, fixture_store, "turn-1")
    assert any("Metric" in m and "nope" in m for m in e.value.messages)


def test_prose_and_surface_bare_numbers_share_one_log_line(pipeline, fixture_store, caplog):
    reply = 'About 812 went to {r1[1].supplier}.\n<a2ui>\nroot = Column([t])\nt = Text("9 of 10")\n</a2ui>'
    with caplog.at_level(logging.WARNING, logger="explorer_agent.ui"):
        ui = pipeline.build(reply, fixture_store, "turn-1")
    assert ui.bare_numbers == ["812", "9", "10"]
    assert [r.getMessage() for r in caplog.records if BARE_NUMBERS_LOG in r.getMessage()] == [
        BARE_NUMBERS_LOG + "812, 9, 10"
    ]
