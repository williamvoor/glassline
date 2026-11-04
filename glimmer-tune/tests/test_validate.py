"""Validator layers 1-3 (distill Phase 3, used early by the Phase 1 teacher ceiling).

Layer 2 drives a local headless Chromium on the built review page (`review/dist/render.html`).
No network: the page is served from disk on 127.0.0.1."""

import pytest

from a2ui_eval.catalog_check import CatalogCheck
from a2ui_eval.catalog_model import CatalogModel
from a2ui_eval.io import read_jsonl, wrap
from a2ui_eval.render import DIST, Renderer
from a2ui_eval.seeds import ROOT
from a2ui_eval.validate import Validator, pick

DEV = read_jsonl(ROOT / "data" / "dev" / "cases.jsonl")
needs_dist = pytest.mark.skipif(not (DIST / "render.html").exists(), reason="build review/ first (npm run build)")

GOOD = '''surface("s")
$/title = "Enable notifications"
root = Card(col)
col = Column([title, go])
title = Text($/title, "h2")
go_text = Text("Turn on")
go = Button(go_text, _, Event("enable", {}))
'''


@pytest.fixture(scope="module")
def validator(sdk_v0_9):
    return Validator(sdk_v0_9, renderer=None)


@pytest.fixture(scope="module")
def renderer():
    if not (DIST / "render.html").exists():
        pytest.skip("build review/ first (npm run build)")
    with Renderer() as r:
        yield r


@pytest.fixture(scope="module")
def full(sdk_v0_9, renderer):
    return Validator(sdk_v0_9, renderer=renderer)


# --- layer 1: mechanical ------------------------------------------------------------------------


def test_a_dev_reference_passes_mechanical_and_lint(validator):
    v = validator.check(wrap(DEV[0]["reference_express"]), "stop")
    assert v["layers"]["mechanical"] == {"pass": True, "reasons": []}
    assert v["layers"]["lint"]["pass"] and v["layers"]["render"] == {"pass": None, "reasons": ["not run"]}


@pytest.mark.parametrize(
    "text, reason",
    [
        (None, "no response"),
        ("I can't draw that.", "no a2ui block"),
        ("<a2ui>\nsurface(\"s\")\nroot = Text(", "truncated"),
        ("<a2ui>\nsurface(\"s\")\nroot = Text(\n</a2ui>", "does not compile"),
        (wrap('surface("s")\n$/x = 1\nroot = Text(formatString("${$/x > 0}"))'), "outside catalog"),  # invented operator
        (wrap('surface("s")\nroot = Column([a])\na = Text($/missing/name)\n'), "unresolved binding"),
        (wrap('surface("s")\nroot = Text("hi")\nlost = Text("never attached")\n'), "structure"),
    ],
)
def test_mechanical_failures_name_their_reason(validator, text, reason):
    v = validator.check(text, "length" if text and "</a2ui>" not in text else "stop")
    m = v["layers"]["mechanical"]
    assert m["pass"] is False and any(reason in r for r in m["reasons"]), m
    assert v["pass"] is False


def test_required_on_a_checkbox_is_mechanical(validator):
    src = 'surface("s")\n$/ok = false\nroot = CheckBox("Agree", $/ok, checks=[{condition: required($/ok), message: "Tick"}])\n'
    v = validator.check(wrap(src), "stop")
    assert any("required on a boolean input" in r for r in v["layers"]["mechanical"]["reasons"])


# --- layer 3: lint --------------------------------------------------------------------------------


def test_lint_names_the_s8_rule(validator):
    src = 'surface("s")\nroot = Column([go])\ngo_text = Text("Go")\ngo = Button(go_text, _, Event("go", {}))\nlorem = Text("lorem ipsum dolor")\n'
    src = src.replace("Column([go])", "Column([go, lorem])")
    v = validator.check(wrap(src), "stop")
    assert v["layers"]["mechanical"]["pass"], v
    assert v["layers"]["lint"]["pass"] is False and "lorem" in v["layers"]["lint"]["reasons"]


# --- layer 2: render ------------------------------------------------------------------------------


@needs_dist
def test_render_passes_a_clean_surface_at_both_widths(full, tmp_path):
    v = full.check(wrap(GOOD), "stop", screenshot=tmp_path / "good.png")
    assert v["layers"]["render"] == {"pass": True, "reasons": []}, v
    assert v["pass"] is True
    assert (tmp_path / "good.375.png").exists() and (tmp_path / "good.1280.png").exists()


@needs_dist
def test_render_flags_an_empty_container(full):
    src = GOOD.replace("Column([title, go])", "Column([title, go, nothing])") + "nothing = Row([])\n"
    v = full.check(wrap(src), "stop")
    assert v["layers"]["mechanical"]["pass"], v
    assert v["layers"]["render"]["pass"] is False and any("empty" in r for r in v["layers"]["render"]["reasons"])


@needs_dist
def test_render_flags_overflow_at_phone_width(full):
    wide = ", ".join(f"b{i}" for i in range(14))
    buttons = "".join(f'b{i}_t = Text("Option number {i}")\nb{i} = Button(b{i}_t, _, Event("e{i}", {{}}))\n' for i in range(14))
    src = f'surface("s")\nroot = Row([{wide}])\n{buttons}'
    v = full.check(wrap(src), "stop")
    assert v["layers"]["mechanical"]["pass"], v
    reasons = v["layers"]["render"]["reasons"]
    assert v["layers"]["render"]["pass"] is False and any("overflow at 375px" in r for r in reasons), reasons


# Gallery dev references that fail a layer, found when the validator was calibrated (2026-11-04).
# Each is a finding about the reference, listed so it stays visible: a new failure, or one of
# these disappearing, fails the test.
KNOWN_REFERENCE_FAILURES = {
    # Four columns of long market caps run 80px past a 375px phone screen, out of the Card.
    "33_financial-data-grid": {"render": ["overflow at 375px"]},
}


@needs_dist
def test_every_gallery_dev_reference_renders(full):
    """Calibration: the references the scorer trusts must pass layers 1-3, apart from the known
    failures above. A failure here is a validator bug or a reference finding, never a reason to
    loosen a check silently."""
    failed = {}
    for case in {c["family"]: c for c in DEV}.values():  # one row per family keeps it quick
        v = full.check(wrap(case["reference_express"]), "stop")
        if not v["pass"]:
            failed[case["family"]] = {
                k: sorted({r.split(":")[0] for r in l["reasons"]}) for k, l in v["layers"].items() if l["pass"] is False
            }
    assert failed == KNOWN_REFERENCE_FAILURES


# --- selection --------------------------------------------------------------------------------------


def test_pick_takes_the_passing_candidate_with_fewest_tokens():
    cands = [
        {"sample": 0, "pass": False, "tokens": 10},
        {"sample": 1, "pass": True, "tokens": 300},
        {"sample": 2, "pass": True, "tokens": 120},
        {"sample": 3, "pass": True, "tokens": 120},
    ]
    assert pick(cands)["sample"] == 2  # ties go to the earlier sample
    assert pick([{"sample": 0, "pass": False, "tokens": 1}]) is None
