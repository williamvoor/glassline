from a2ui_eval.structure import binding_uses, check_structure, unresolved_bindings

from .conftest import comps


def test_refs_cover_lists_templates_nested_and_single_children(index):
    assert [(r.slot, r.target) for r in index.refs({"id": "c", "component": "Column", "children": ["a", "b"]})] == [
        ("children[]", "a"),
        ("children[]", "b"),
    ]
    (tpl,) = index.refs({"id": "l", "component": "List", "children": {"path": "/rows", "componentId": "row"}})
    assert (tpl.slot, tpl.target, tpl.template_path) == ("children.componentId", "row", "/rows")
    tabs = {"id": "t", "component": "Tabs", "tabs": [{"title": "A", "child": "x"}, {"title": "B", "child": "y"}]}
    assert [(r.slot, r.target) for r in index.refs(tabs)] == [("tabs[].child", "x"), ("tabs[].child", "y")]
    modal = {"id": "m", "component": "Modal", "trigger": "open", "content": "body"}
    assert sorted(r.target for r in index.refs(modal)) == ["body", "open"]
    button = {"id": "b", "component": "Button", "child": "label", "action": {"event": {"name": "go"}}}
    assert [r.target for r in index.refs(button)] == ["label"]


def test_text_values_are_not_mistaken_for_references(index):
    assert index.refs({"id": "t", "component": "Text", "text": "root"}) == []


def test_structure_reports_each_problem(index):
    report = check_structure(
        comps(
            {"id": "root", "component": "Column", "children": ["a", "ghost"]},
            {"id": "a", "component": "Card", "child": "b"},
            {"id": "b", "component": "Card", "child": "a"},
            {"id": "lost", "component": "Text", "text": "x"},
        ),
        index,
    )
    assert report.undefined_refs == [("root", "ghost")]
    assert report.orphans == ["lost"]
    assert len(report.cycles) == 1 and set(report.cycles[0]) == {"a", "b"}
    assert not report.missing_root


def test_structure_flags_missing_root(index):
    assert check_structure(comps({"id": "x", "component": "Text", "text": "x"}), index).missing_root


def test_clean_tree_has_no_errors(index):
    report = check_structure(
        comps(
            {"id": "root", "component": "Column", "children": ["a", "b"]},
            {"id": "a", "component": "Text", "text": "x"},
            {"id": "b", "component": "Text", "text": "y"},
        ),
        index,
    )
    assert report.error_count == 0


def test_binding_uses_finds_paths_and_interpolations():
    comp = {
        "id": "t",
        "component": "Text",
        "text": {"@call": "formatString", "args": {"value": "Hi ${/user/name}, ${count} items"}},
        "extra": {"@path": "/a/b"},
    }
    assert sorted(binding_uses(comp)) == ["/a/b", "/user/name", "count"]


def test_relative_bindings_resolve_against_template_items(index):
    components = comps(
        {"id": "root", "component": "List", "children": {"path": "/rows", "componentId": "row"}},
        {"id": "row", "component": "Text", "text": {"@path": "name"}},
        {"id": "other", "component": "Text", "text": {"@path": "missing"}},
    )
    data = {"rows": [{"name": "a"}]}
    assert unresolved_bindings(["root", "row"], components, index, [data]) == []
    no_items = unresolved_bindings(["row"], components, index, [{"rows": []}])
    assert no_items == [("row", "name")]


def test_binding_counts_as_resolved_when_reference_binds_it(index):
    components = comps({"id": "root", "component": "TextField", "label": "Email", "value": {"@path": "/form/email"}})
    assert unresolved_bindings(["root"], components, index, [{}]) == [("root", "/form/email")]
    assert unresolved_bindings(["root"], components, index, [{}], allowed={("form", "email")}) == []
