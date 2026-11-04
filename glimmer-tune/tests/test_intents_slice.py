"""The explorer slice (ADR 0038; SPEC "Approach v2 Phase 2 completion" E2-E7). No model calls."""

from __future__ import annotations

from collections import Counter

import pytest

from a2ui_eval.checkpoints import export_dev, family_ci, fewshot_examples, with_fewshot
from a2ui_eval.data_cli import export_eval_cases
from a2ui_eval.fingerprint import fingerprint
from a2ui_eval.intents import slice as S
from a2ui_eval.intents.bank import parse_domain
from a2ui_eval.io import read_jsonl, wrap, write_jsonl
from a2ui_eval.prompt import checked_system_prompt
from a2ui_eval.score import score_response
from a2ui_eval.validate import Validator

from .test_intents import domain_raw


@pytest.fixture
def bank():
    names = ["Hospital ward", "Bike shop", "Dental practice", "Bakery", "Zoo", "Library", "Garage"]
    return [parse_domain(domain_raw(n), "model:x") for n in names]


@pytest.fixture(scope="module")
def validator(sdk):
    return Validator(sdk)


def test_slice_content_kinds_and_domains(bank):
    """E2: one spec of each data kind per held-out domain, nothing from other domains."""
    held = ["bakery", "zoo"]
    rows = S.slice_content(bank, held, seed=0)
    assert {r["content"]["domain"] for r in rows} == set(held)
    for d in held:
        kinds = Counter(r["content"]["kind"] for r in rows if r["content"]["domain"] == d)
        assert set(kinds) == set(S.DATA_KINDS) and set(kinds.values()) == {1}
    assert not any(r["content"]["kind"] in ("update", "text_only") for r in rows)
    assert len({r["id"] for r in rows}) == len(rows)
    assert S.slice_content(bank, held, seed=0) == rows


def test_slice_content_refuses_unknown_domains(bank):
    with pytest.raises(S.SliceError, match="missing"):
        S.slice_content(bank, ["bakery", "nowhere"])


def test_select_five_per_domain(bank):
    """E3: the first 5 ok rows per domain in content order; dropped rows are replaced."""
    rows = S.slice_content(bank, ["bakery", "zoo"], seed=1)
    bad = {rows[0]["id"], rows[9]["id"]}
    verdicts = [{"id": r["id"], "verdict": "register" if r["id"] in bad else "ok"} for r in rows]
    picked = S.select(rows, verdicts)
    for d in ("bakery", "zoo"):
        want = [r for r in rows if r["content"]["domain"] == d and r["id"] not in bad][:5]
        assert [r for r in picked if r["content"]["domain"] == d] == want
    dropped = {picked[0]["id"]}
    again = S.select(rows, verdicts, dropped=dropped)
    assert picked[0]["id"] not in {r["id"] for r in again} and len(again) == 10


def test_select_raises_when_a_domain_runs_short(bank):
    rows = S.slice_content(bank, ["bakery", "zoo"], seed=1)
    verdicts = [{"id": r["id"], "verdict": "ok" if r["content"]["domain"] == "zoo" else "near_dup_set"} for r in rows]
    with pytest.raises(S.SliceError, match="bakery"):
        S.select(rows, verdicts)


def test_export_matches_dev_prefix(sdk, bank):
    """E4: frozen prompt + 3 frozen examples + the intent; the prefix is the dev export's."""
    prompt = checked_system_prompt(sdk)
    rows = S.slice_content(bank, ["bakery"], seed=0)[:2]
    out = S.export_rows(rows, {r["id"]: f"intent {r['id']}" for r in rows}, prompt, fewshot_examples())
    dev = with_fewshot(export_dev(prompt)[:1], fewshot_examples())[0]
    for r, o in zip(rows, out):
        assert o["id"] == r["id"]
        assert o["messages"][:-1] == dev["messages"][:-1]
        assert o["messages"][-1] == {"role": "user", "content": f"intent {r['id']}"}


# ---------------------------------------------------------------- build_cases (E5)

def _express(i: int) -> str:
    """Three distinct valid designs: the frozen few-shot references."""
    return fewshot_examples()[i]["reference_express"]


@pytest.fixture
def built_inputs(bank):
    rows = S.slice_content(bank, ["bakery"], seed=0)[:2]
    intents = {r["id"]: f"intent for {r['id']}" for r in rows}
    cands = {r["id"]: [{"sample": k, "text": wrap(_express(k)), "pass": True, "layers": {}} for k in range(3)]
             for r in rows}
    return rows, intents, cands


def _build(sdk, index, validator, built_inputs, picks, fps=frozenset()):
    rows, intents, cands = built_inputs
    return S.build_cases(rows, intents, cands, picks, sdk, index, set(fps),
                         lambda text: validator.mechanical(text, "stop"), "william", "model:spark")


def test_build_cases_shape_and_provenance(sdk, index, validator, built_inputs):
    rows, intents, _ = built_inputs
    picks = {rows[0]["id"]: {"pick": 1}, rows[1]["id"]: {"express": _express(2), "from": 0}}
    cases = _build(sdk, index, validator, built_inputs, picks)
    a, b = cases
    assert [a["id"], b["id"]] == ["explorer/bakery/0000", "explorer/bakery/0001"]
    assert a["family"] == "bakery" and a["source"] == "explorer" and a["split"] == "dev"
    assert a["reviewed"] is True and a["expect_ui"] is True and a["existing_surface"] == []
    assert a["intent"] == intents[rows[0]["id"]] and a["picked"] == 1
    assert a["reference"] == sdk.compile_express(_express(1))
    assert a["reference_express"] == sdk.decompile(a["reference"])
    assert a["fingerprint"] == fingerprint(a["reference"], index)
    assert a["provenance"] == {"intent": "model:spark", "reference": "model:glimmer-base"}
    assert b["provenance"]["reference"] == "model:glimmer-base + person:william"
    assert b["reference"] == sdk.compile_express(_express(2))


def test_build_cases_refuses_a_missing_pick(sdk, index, validator, built_inputs):
    rows = built_inputs[0]
    with pytest.raises(S.SliceError, match="no pick"):
        _build(sdk, index, validator, built_inputs, {rows[0]["id"]: {"pick": 0}})


def test_build_cases_refuses_a_layer1_failure(sdk, index, validator, built_inputs):
    rows = built_inputs[0]
    picks = {rows[0]["id"]: {"express": 'surface("s")\nroot = Column([missing])', "from": 0}, rows[1]["id"]: {"pick": 0}}
    with pytest.raises(S.SliceError, match="layer 1"):
        _build(sdk, index, validator, built_inputs, picks)


def test_build_cases_refuses_a_gallery_fingerprint(sdk, index, validator, built_inputs):
    rows = built_inputs[0]
    fp = fingerprint(sdk.compile_express(_express(0)), index)
    with pytest.raises(S.SliceError, match="fingerprint"):
        _build(sdk, index, validator, built_inputs, {r["id"]: {"pick": 0} for r in rows}, fps={fp})


def test_build_cases_refuses_dropped_and_unknown_candidates(sdk, index, validator, built_inputs):
    rows = built_inputs[0]
    with pytest.raises(S.SliceError, match="dropped"):
        _build(sdk, index, validator, built_inputs, {rows[0]["id"]: {"drop": "vague"}, rows[1]["id"]: {"pick": 0}})
    with pytest.raises(S.SliceError, match="doesn't exist"):
        _build(sdk, index, validator, built_inputs, {rows[0]["id"]: {"pick": 7}, rows[1]["id"]: {"pick": 0}})


def test_express_of_needs_one_block(sdk):
    assert S.express_of(sdk, wrap(_express(0))) == _express(0).strip()
    with pytest.raises(S.SliceError):
        S.express_of(sdk, "no block")


# ---------------------------------------------------------------- integration (E6, E7)

def test_export_guard_reads_slice(tmp_path):
    """E6: the export leakage guards see the slice cases."""
    (tmp_path / "explorer_dev").mkdir()
    write_jsonl(tmp_path / "explorer_dev" / "cases.jsonl", [{"id": "explorer/x/0000", "intent": "slice intent", "fingerprint": "f"}])
    assert "slice intent" in {c["intent"] for c in export_eval_cases(tmp_path)}


def test_slice_case_scores(sdk, index, validator, built_inputs):
    """E7: a slice case scores with score_response, and family_ci clusters by domain slug."""
    rows = built_inputs[0]
    cases = _build(sdk, index, validator, built_inputs, {r["id"]: {"pick": i} for i, r in enumerate(rows)})
    scored = []
    for c in cases:
        r = score_response(sdk, index, c, wrap(c["reference_express"]), "stop")
        assert r["compiles"] and r["slot_blind"]["f1"] == 1.0
        scored.append({"family": c["family"], "slot_f1": r["slot_blind"]["f1"]})
    scored.append({"family": "03_calendar-day", "slot_f1": 0.5})
    lo, hi = family_ci(scored, samples=200)
    assert 0.5 <= lo <= hi <= 1.0


def test_candidates_groups_by_case_in_sample_order():
    preds = [{"id": "b", "sample": 1, "text": "x", "tokens": 5}, {"id": "a", "sample": 0, "text": "y", "tokens": 3},
             {"id": "b", "sample": 0, "text": "z", "tokens": 4}]
    out = S.candidates(preds, lambda p: {"pass": p["tokens"] > 3, "layers": {}})
    assert list(out) == ["a", "b"] and [c["sample"] for c in out["b"]] == [0, 1]
    assert [c["pass"] for c in out["b"]] == [True, True] and out["a"][0]["pass"] is False


def test_page_lists_every_candidate(bank):
    rows = S.slice_content(bank, ["bakery"], seed=0)[:1]
    cands = {rows[0]["id"]: [{"sample": k, "text": f"<a2ui>c{k}</a2ui>", "pass": k == 0, "tokens": 9,
                              "layers": {"mechanical": {"pass": k == 0, "reasons": [] if k == 0 else ["nope"]}}}
                             for k in range(2)]}
    html = S.page(rows, {rows[0]["id"]: "Show <me>"}, cands, lambda cid, k: [f"s{k}.png"])
    assert "Show &lt;me&gt;" in html and "k=0" in html and "k=1" in html and "s1.png" in html and "nope" in html


# ---------------------------------------------------------------- refuter round 1 (2026-11-04)

def test_cmd_export_guards_against_slice_cases(tmp_path, monkeypatch):
    """Refuter MAJOR (E6): prove cmd_export itself passes the slice cases to export_sft."""
    import argparse
    import shutil

    from a2ui_eval import data_cli
    from a2ui_eval.seeds import ROOT

    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data, ignore=shutil.ignore_patterns("intents", "explorer_dev", "synth"))
    (data / "explorer_dev").mkdir()
    write_jsonl(data / "explorer_dev" / "cases.jsonl", [{"id": "explorer/x/0000", "intent": "slice intent", "fingerprint": "f0"}])
    write_jsonl(tmp_path / "train.jsonl", [])
    seen = {}

    def fake_export(train, eval_cases, *a, **k):
        seen["eval"] = list(eval_cases)
        return [], type("R", (), {"to_dict": lambda self: {}})()

    monkeypatch.setattr(data_cli, "export_sft", fake_export)
    data_cli.cmd_export(argparse.Namespace(data=data, train=tmp_path / "train.jsonl", out=tmp_path / "o.jsonl",
                                           approx_tokens=True, max_seq_len=8192))
    assert "slice intent" in {c["intent"] for c in seen["eval"]}


def test_build_cases_refuses_a_pick_that_is_not_a_mapping(sdk, index, validator, built_inputs):
    rows = built_inputs[0]
    with pytest.raises(S.SliceError, match="mapping"):
        _build(sdk, index, validator, built_inputs, {rows[0]["id"]: 3, rows[1]["id"]: {"pick": 0}})


def test_slice_select_needs_every_train_row_worded(tmp_path):
    """Refuter MAJOR: the slice/train near-dup guard must see every train intent."""
    content = [{"id": "a"}, {"id": "b"}]
    with pytest.raises(S.SliceError, match="not worded"):
        S.require_complete(content, [{"id": "a", "finish": "stop"}], partial_exists=False)
    with pytest.raises(S.SliceError, match="partial"):
        S.require_complete(content, [{"id": "a", "finish": "stop"}, {"id": "b", "finish": "stop"}], partial_exists=True)
    S.require_complete(content, [{"id": "a", "finish": "stop"}, {"id": "b", "finish": "stop"}], partial_exists=False)


def test_slice_references_exclude_the_slices_own_cases():
    """Refuter MINOR: after slice-build, rerunning slice-select rejected every slice row as a
    near-duplicate of its own case."""
    refs = S.references_for_slice(["gallery intent"], [{"intent": "own case", "source": "explorer"}], ["train intent"])
    assert refs == ["gallery intent", "train intent"]
