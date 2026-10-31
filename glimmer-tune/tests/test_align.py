"""Alignment and slot accuracy, on hand-written component JSON."""

import pytest

from a2ui_eval.align import align, score_components

from .conftest import comps


# Run this module under both catalogs (overrides conftest's v0.9 fixtures; ADR 0008).
@pytest.fixture(params=["v0_9", "v1"])
def index(request):
    return request.getfixturevalue(f"index_{request.param}")

REF = comps(
    {"id": "root", "component": "Column", "children": ["title", "email", "send"]},
    {"id": "title", "component": "Text", "text": "Sign up"},
    {"id": "email", "component": "TextField", "label": "Email", "value": {"@path": "/form/email"}, "placeholder": "you@example.com"},
    {"id": "send", "component": "Button", "child": "sendLabel", "action": {"event": {"name": "signup"}}},
    {"id": "sendLabel", "component": "Text", "text": "Send"},
)


def renamed(components, names):
    out = {}
    for cid, comp in components.items():
        c = dict(comp)
        c["id"] = names.get(cid, cid)
        if "children" in c:
            c["children"] = [names.get(x, x) for x in c["children"]]
        if "child" in c:
            c["child"] = names.get(c["child"], c["child"])
        out[c["id"]] = c
    return out


def test_renamed_ids_align_and_match_exactly(index):
    pred = renamed(REF, {"title": "heading", "email": "f1", "send": "btn", "sendLabel": "btnText"})
    result = score_components(pred, REF, index)
    assert result.mapping == {"root": "root", "heading": "title", "f1": "email", "btn": "send", "btnText": "sendLabel"}
    assert result.exact
    assert result.score.f1 == 1.0
    assert result.score.positional_errors == 0


def test_swapped_positional_arguments_count_as_positional_errors(index):
    pred = dict(REF)
    # The model put the placeholder text in `label` and the label in `placeholder`.
    pred["email"] = {**REF["email"], "label": "you@example.com", "placeholder": "Email"}
    result = score_components(pred, REF, index)
    assert result.mapping["email"] == "email"
    assert result.score.positional_errors == 2
    assert not result.exact
    assert result.score.n_ref - result.score.tp == 2  # exactly the two swapped slots are missed
    assert result.score.precision < 1.0


def test_value_in_unrelated_slot_is_not_positional(index):
    pred = dict(REF)
    pred["email"] = {**REF["email"], "label": "E-mail address"}
    result = score_components(pred, REF, index)
    assert result.score.positional_errors == 0
    assert result.score.n_ref - result.score.tp == 1


def test_reordered_children_align_by_type_and_text(index):
    ref = comps(
        {"id": "root", "component": "Row", "children": ["a", "b"]},
        {"id": "a", "component": "Text", "text": "Price"},
        {"id": "b", "component": "Text", "text": "$12.00"},
    )
    pred = comps(
        {"id": "root", "component": "Row", "children": ["x", "y"]},
        {"id": "x", "component": "Text", "text": "$12.00"},
        {"id": "y", "component": "Text", "text": "Price"},
    )
    # Children align in order when types agree, so x->a and y->b, and both texts
    # count as wrong. The values moved between components, not between
    # properties, so this is not a positional error.
    result = score_components(pred, ref, index)
    assert result.mapping == {"root": "root", "x": "a", "y": "b"}
    assert not result.exact
    assert result.score.n_ref - result.score.tp == 2
    assert result.score.positional_errors == 0


def test_unmatched_types_fall_back_to_best_text_match(index):
    ref = comps(
        {"id": "root", "component": "Column", "children": ["hello", "pic"]},
        {"id": "hello", "component": "Text", "text": "Hello there"},
        {"id": "pic", "component": "Image", "url": "https://x/cat.png"},
    )
    # An extra wrapper Card shifts the Text out of its slot.
    pred = comps(
        {"id": "root", "component": "Column", "children": ["wrap", "img"]},
        {"id": "wrap", "component": "Card", "child": "greet"},
        {"id": "greet", "component": "Text", "text": "Hello there!"},
        {"id": "img", "component": "Image", "url": "https://x/cat.png"},
    )
    mapping = align(pred, ref, index)
    assert mapping == {"root": "root", "img": "pic", "greet": "hello"}
    result = score_components(pred, ref, index)
    assert result.unaligned_pred == ["wrap"]
    assert result.unaligned_ref == []
    assert not result.exact


def test_best_match_prefers_more_similar_text(index):
    ref = comps(
        {"id": "a", "component": "Text", "text": "Total due"},
        {"id": "b", "component": "Text", "text": "Thanks for your order"},
    )
    pred = comps(
        {"id": "p", "component": "Text", "text": "Thank you for your order"},
        {"id": "q", "component": "Text", "text": "Total due today"},
    )
    assert align(pred, ref, index) == {"p": "b", "q": "a"}


def test_root_types_differ_root_still_aligns_but_scores_zero(index):
    ref = comps({"id": "root", "component": "Text", "text": "Hi"})
    pred = comps({"id": "root", "component": "Card", "child": "t"}, {"id": "t", "component": "Text", "text": "Hi"})
    result = score_components(pred, ref, index)
    assert result.mapping["root"] == "root"
    # The Text with matching content is left for the global fallback, but root is taken.
    assert result.score.tp == 0


def test_anchored_ids_must_match_by_id(index):
    ref = comps({"id": "title", "component": "Text", "text": "New title"})
    pred = comps({"id": "subtitle", "component": "Text", "text": "New title"}, {"id": "title", "component": "Text", "text": "Old"})
    result = score_components(pred, ref, index, anchors=frozenset({"title", "subtitle"}))
    assert result.mapping["title"] == "title"
    assert result.unaligned_pred == ["subtitle"]


def test_empty_prediction_has_zero_recall(index):
    result = score_components({}, REF, index)
    assert result.score.recall == 0.0
    assert result.score.f1 == 0.0
