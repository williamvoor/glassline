"""Phase 1 report: best-of-n scoring and selection on stubbed predictions (no model, no render)."""

import pytest

from a2ui_eval.checkpoints import CheckpointError, export_dev
from a2ui_eval.export import approx_tokens
from a2ui_eval.io import read_jsonl, wrap
from a2ui_eval.phase1 import analyse, markdown, token_counts
from a2ui_eval.seeds import ROOT
from a2ui_eval.validate import Validator

DATA = ROOT / "data"


@pytest.fixture(scope="module")
def dev_rows():
    return [r for r in export_dev("p", DATA) if r["source"] == "gallery"]


@pytest.fixture(scope="module")
def validator(sdk_v0_9):
    return Validator(sdk_v0_9, renderer=None)


def _cases():
    return {c["id"]: c for c in read_jsonl(DATA / "dev" / "cases.jsonl")}


def test_single_validator_and_oracle_differ_as_designed(dev_rows, validator):
    """Two rows x 3 samples: sample 0 is broken, 1 is the reference (long), 2 is a short valid
    answer that scores lower. The validator keeps the short valid one (fewest tokens); the oracle
    keeps the reference; single is the mean of the three samples."""
    cases = _cases()
    ids = [r["id"] for r in dev_rows[:2]]
    short = wrap('surface("s")\nroot = Text("Something")\n')
    preds = []
    for i in ids:
        ref = wrap(cases[i]["reference_express"])
        preds += [
            {"id": i, "sample": 0, "text": "<a2ui>\nroot = Text(\n</a2ui>", "finish": "stop", "tokens": 10},
            {"id": i, "sample": 1, "text": ref, "finish": "stop", "tokens": 500},
            {"id": i, "sample": 2, "text": short, "finish": "stop", "tokens": 20},
        ]
    out = analyse(preds, dev_rows, validator, approx_tokens, DATA, cost_usd=1.5)
    assert out["rows"] == 2 and out["samples"] == 3
    assert out["oracle_best_of_n"]["gallery_f1_blind"] == pytest.approx(1.0)
    v = out["validator_best_of_n"]
    assert v["covered"] == 1.0 and 0.0 < v["gallery_f1_blind"] < 1.0  # the short answer, not the reference
    assert out["single"]["per_sample"][0] == 0.0 and out["single"]["per_sample"][1] == pytest.approx(1.0)
    assert out["single"]["gallery_f1_blind"] == pytest.approx(sum(out["single"]["per_sample"]) / 3)
    assert out["candidate_pass"] == pytest.approx(4 / 6)
    assert [(r["id"], r["picked_sample"], r["covered"]) for r in out["picked"]] == [(i, 2, True) for i in ids]
    assert out["layer_pass"]["mechanical"] == pytest.approx(4 / 6)
    assert "| t | 3 |" in markdown({"t": out}) and "$1.50" in markdown({"t": out})


def test_a_row_with_no_valid_candidate_scores_sample_0_and_is_uncovered(dev_rows, validator):
    i = dev_rows[0]["id"]
    preds = [{"id": i, "sample": k, "text": None, "finish": "error", "tokens": 0} for k in range(2)]
    out = analyse(preds, dev_rows, validator, approx_tokens, DATA)
    assert out["validator_best_of_n"]["covered"] == 0.0 and out["validator_best_of_n"]["gallery_f1_blind"] == 0.0


def test_samples_must_cover_the_same_rows(dev_rows, validator):
    a, b = dev_rows[0]["id"], dev_rows[1]["id"]
    preds = [{"id": a, "sample": 0, "text": None, "finish": "error"}, {"id": b, "sample": 1, "text": None, "finish": "error"}]
    with pytest.raises(CheckpointError, match="same rows"):
        analyse(preds, dev_rows, validator, approx_tokens, DATA)


def test_token_counts_split_answer_and_reasoning():
    count = lambda t: len(t)  # noqa: E731
    assert token_counts({"text": "abcd", "tokens": 10}, count) == (4, 6)  # Glimmer: everything sampled
    assert token_counts({"text": "abcd", "completion_tokens": 50, "reasoning_tokens": 40}, count) == (4, 40)  # Spark Open
    assert token_counts({"text": "abcd", "completion_tokens": 50}, count) == (4, 46)
    assert token_counts({"text": None, "tokens": 0}, count) == (0, 0)
