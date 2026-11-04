"""S8: the plausibility linter (SPEC 2c-1, build B)."""

import pytest

from a2ui_eval.seeds import load_seeds
from a2ui_eval.io import read_jsonl
from a2ui_eval.seeds import ROOT
from a2ui_eval.catalog_model import CatalogModel
from a2ui_eval.lint import (
    RULES,
    is_single_turn,
    lint_gallery,
    lint_rows,
    lint_surface,
    load_gallery_allowlist,
    stale_allowlist,
)

CAT = "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"


@pytest.fixture(scope="module")
def model(sdk_v0_9):
    return CatalogModel(sdk_v0_9)


def surface(components, data=None):
    msgs = [
        {"version": "v0.9", "createSurface": {"surfaceId": "s", "catalogId": CAT, "sendDataModel": True}},
        {"version": "v0.9", "updateComponents": {"surfaceId": "s", "components": components}},
    ]
    if data is not None:
        msgs.append({"version": "v0.9", "updateDataModel": {"surfaceId": "s", "path": "/", "value": data}})
    return msgs


def text(cid, value, **extra):
    return {"id": cid, "component": "Text", "text": value, **extra}


def col(*ids, cid="root"):
    return {"id": cid, "component": "Column", "children": list(ids)}


def button(cid="go", label="lbl", action=None):
    comps = [{"id": cid, "component": "Button", "child": label}, text(label, "Save")]
    if action is not None:
        comps[0]["action"] = action
    return comps


SUBMIT = {"event": {"name": "save", "context": {"name": {"path": "/name"}}}}
FIELD = {"id": "f", "component": "TextField", "label": "Name", "value": {"path": "/name"}}
HEAD = text("h", "A clear heading", variant="h2")
BODY = text("b", "This is a full sentence of body text.")

CLEAN = surface([col("h", "b", "f", "go"), HEAD, BODY, FIELD, *button(action=SUBMIT)], {"name": ""})

BAD = {
    "button_action": surface([col("go"), *button()]),
    "form_submit": surface(
        [col("f", "g", "go"), FIELD, {**FIELD, "id": "g", "value": {"path": "/other"}}, *button(action=SUBMIT)],
        {"name": "", "other": ""},
    ),
    "input_bound": surface([col("f", "go"), {**FIELD, "value": "literal"}, *button(action=SUBMIT)], {"name": ""}),
    "modal": surface([col("m"), {"id": "m", "component": "Modal", "trigger": "t", "content": "c"}, text("t", "Open"), text("c", "Body text here")]),
    "tabs": surface([col("tb"), {"id": "tb", "component": "Tabs", "tabs": [{"title": "One", "child": "c1"}]}, text("c1", "Some tab content")]),
    "icon_name": surface([col("i"), {"id": "i", "component": "Icon", "name": "notAnIcon"}]),
    "heading_length": surface([col("h"), text("h", "x" * 61, variant="h1")]),
    "body_words": surface([col("h", "b"), HEAD, text("b", "Too short")]),
    "list_source": surface(
        [col("l"), {"id": "l", "component": "List", "children": {"componentId": "it", "path": "/items"}}, text("it", {"path": "name"})],
        {"items": []},
    ),
    "lorem": surface([col("b"), text("b", "Lorem ipsum dolor sit amet")]),
    "repeated_value": surface([col("a", "b"), text("a", "Support Specialist", variant="caption"), text("b", "Support Specialist", variant="caption")]),
}


def test_rules_are_the_spec_list():
    assert set(RULES) == set(BAD)


def test_clean_surface_passes(model):
    assert lint_surface(CLEAN, model) == []


@pytest.mark.parametrize("rule", sorted(BAD))
def test_rule_fires(rule, model):
    assert rule in lint_surface(BAD[rule], model)


@pytest.mark.parametrize("rule", sorted(BAD))
def test_rule_fires_alone(rule, model):
    # Each bad surface breaks its own rule only, so a failure points at one cause.
    assert lint_surface(BAD[rule], model) == [rule]


def test_heading_boundary(model):
    ok = surface([col("h"), text("h", "x" * 60, variant="h1")])
    markdown = surface([col("h"), text("h", "## " + "x" * 61)])
    assert lint_surface(ok, model) == []
    assert lint_surface(markdown, model) == ["heading_length"]
    # A Markdown document is judged by its first line.
    assert lint_surface(surface([col("h"), text("h", "# Title\n\n" + "word " * 40)]), model) == []


def test_body_words_scope(model):
    three = surface([col("h", "b"), HEAD, text("b", "Exactly three words")])
    label = surface([col("h", "b", "go"), HEAD, BODY, *button(action=SUBMIT)])  # "Save" is a Button label
    cell = surface([col("a", "b"), text("a", "Item"), text("b", "6")])  # record cells have no heading beside them
    caption = surface([col("h", "c"), HEAD, text("c", "Hint", variant="caption")])
    bound = surface([col("h", "c"), HEAD, text("c", {"path": "/x"})], {"x": "a"})
    for s in (three, label, cell, caption, bound):
        assert lint_surface(s, model) == []


def test_form_submit_cases(model):
    two_inputs = [
        {"id": "a", "component": "TextField", "label": "A", "value": {"path": "/a"}},
        {"id": "b", "component": "TextField", "label": "B", "value": {"path": "/b"}},
    ]

    def form(context, tags=()):
        action = {"event": {"name": "send", "context": context}}
        return lint_surface(surface([col("a", "b", "go"), *two_inputs, *button(action=action)], {"a": "", "b": ""}), model, tags)

    assert form({"a": {"path": "/a"}, "b": {"path": "/b"}}) == []
    assert form({"a": {"path": "/a"}}) == ["form_submit"]
    assert form({"all": {"path": "/"}}) == []  # a parent path covers the inputs under it
    # One input is not a form, unless the skeleton says so.
    one = surface([col("f", "go"), FIELD, *button(action={"event": {"name": "x", "context": {}}})], {"name": ""})
    assert lint_surface(one, model) == []
    assert lint_surface(one, model, ["form"]) == ["form_submit"]
    no_button = surface([col("f"), FIELD], {"name": ""})
    assert lint_surface(no_button, model, ["form"]) == ["form_submit"]


def test_modal_and_tabs_content_must_exist(model):
    empty_content = surface([col("m"), {"id": "m", "component": "Modal", "trigger": "t", "content": "c"}, *button("t", "tl", SUBMIT), text("c", "")])
    assert lint_surface(empty_content, model) == ["modal"]
    good = surface([col("m"), {"id": "m", "component": "Modal", "trigger": "t", "content": "c"}, *button("t", "tl", SUBMIT), text("c", "Details here")])
    assert lint_surface(good, model) == []
    tabs = surface([col("tb"), {"id": "tb", "component": "Tabs", "tabs": [{"title": "A", "child": "c1"}, {"title": "B", "child": "gone"}]}, text("c1", "Some content")])
    assert lint_surface(tabs, model) == ["tabs"]


def test_gallery_passes(model):
    seeds = load_seeds()
    assert lint_gallery(seeds, model, load_gallery_allowlist()) == {}


def test_gallery_fails_without_allowlist(model):
    # The allowlist is doing real work: it is not an empty file.
    assert lint_gallery(load_seeds(), model, {})


def test_allowlist_entries_have_reasons_and_fire(model):
    allow = load_gallery_allowlist()
    assert all(isinstance(r, str) and len(r.split()) >= 4 for rules in allow.values() for r in rules.values())
    assert stale_allowlist(load_seeds(), model, allow) == []


def test_single_turn_detection():
    seeds = load_seeds()
    assert is_single_turn(seeds["09_login-form"].messages)
    assert not is_single_turn(CLEAN + [{"version": "v0.9", "deleteSurface": {"surfaceId": "s"}}])
    assert not is_single_turn(CLEAN + CLEAN)


def test_lint_rows_uses_skeleton_tags(model):
    one = surface([col("f", "go"), FIELD, *button(action={"event": {"name": "x", "context": {}}})], {"name": ""})
    rows = [{"id": "r1", "skeleton": "formy", "reference": one}, {"id": "r2", "skeleton": "plain", "reference": one}]
    assert lint_rows(rows, model, {"formy": ["form"], "plain": ["list"]}) == {"r1": ["form_submit"]}


def test_cli_lint_gallery(monkeypatch, capsys):
    from a2ui_eval import data_cli
    from a2ui_eval import lint

    assert data_cli.main(["lint", "--gallery"]) == 0
    real = lint.load_gallery_allowlist()
    monkeypatch.setattr(lint, "load_gallery_allowlist", lambda: {**real, "09_login-form": {**real["09_login-form"], "tabs": "stale"}})
    assert data_cli.main(["lint", "--gallery"]) == 1
    assert "stale allowlist entry 09_login-form:tabs" in capsys.readouterr().err
    monkeypatch.setattr(lint, "load_gallery_allowlist", lambda: {})
    assert data_cli.main(["lint", "--gallery"]) == 1
    assert "gallery 09_login-form: form_submit" in capsys.readouterr().err


def test_synthetic_dev_passes(model):
    """The frozen synthetic regression set stays lint-clean (its generator is archived)."""
    rows = read_jsonl(ROOT / "data" / "synth" / "dev" / "cases.jsonl")
    assert len(rows) == 200 and lint_rows(rows, model, {}) == {}


def test_repeated_value_scope(model):
    """SPEC "Sample review fixes D3-D7", D3: a literal equal to a bound value fires; short, letterless
    and template-relative strings don't; heading marks and case are ignored."""
    def two(a, b, data=None):
        return surface([col("a", "b"), text("a", a, variant="caption"), text("b", b, variant="caption")], data)

    assert lint_surface(two("Housing", {"path": "/cat"}, {"cat": "Housing"}), model) == ["repeated_value"]
    assert lint_surface(two("### Fresh Produce", "fresh produce"), model) == ["repeated_value"]
    assert lint_surface(two("Yes", "Yes"), model) == []
    assert lint_surface(two("2026-11-03", "2026-11-03"), model) == []
    assert lint_surface(two({"path": "name"}, {"path": "name"}), model) == []
    assert lint_surface(two("Housing", "Groceries"), model) == []


def test_repeated_value_ignores_list_cells_and_template_items(model):
    """Refuter finding on D3: a static list's rows may share a cell value, and a literal inside a
    template item isn't a second display of a value."""
    def row(i, status):
        return [{"id": f"r{i}", "component": "Row", "children": [f"n{i}", f"s{i}"]},
                text(f"n{i}", f"Claim number {i}", variant="caption"), text(f"s{i}", status, variant="caption")]
    rows = surface([{"id": "root", "component": "Column", "children": ["r1", "r2"]}, *row(1, "Pending"), *row(2, "Pending")])
    assert lint_surface(rows, model) == []
    templated = surface(
        [col("h", "l"), text("h", "### Details"),
         {"id": "l", "component": "List", "children": {"componentId": "it", "path": "/items"}},
         {"id": "it", "component": "Column", "children": ["d"]}, text("d", "Details", variant="caption")],
        {"items": [{"x": 1}]},
    )
    assert lint_surface(templated, model) == []
    # A label echoing a value elsewhere still fires, even inside a row.
    echo = surface([{"id": "root", "component": "Column", "children": ["r1", "v"]}, *row(1, "Housing"), text("v", "Housing", variant="caption")])
    assert lint_surface(echo, model) == ["repeated_value"]
