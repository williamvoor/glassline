import json

import pytest

from a2ui_eval.checkpoints import CheckpointError, export_dev, score_predictions
from a2ui_eval.generate import read_jsonl, wrap
from a2ui_eval.seeds import ROOT

DATA = ROOT / "data"
PROMPT = "frozen system text"


@pytest.fixture(scope="module")
def dev_rows():
    return export_dev(PROMPT, DATA)


def test_export_dev_rows_and_subset(dev_rows):
    """T3: every dev case once, a fixed 32+32 subset, the frozen system text, no answer turn."""
    gallery = read_jsonl(DATA / "dev" / "cases.jsonl")
    synth = read_jsonl(DATA / "synth" / "dev" / "cases.jsonl")
    assert len(dev_rows) == len(gallery) + len(synth) == 284
    assert sorted(r["id"] for r in dev_rows) == sorted(c["id"] for c in gallery + synth)
    by_source = {s: [r for r in dev_rows if r["source"] == s] for s in ("gallery", "synthetic")}
    assert len(by_source["gallery"]) == 84 and len(by_source["synthetic"]) == 200
    for s in by_source.values():
        assert sum(r["subset"] for r in s) == 32
    intents = {c["id"]: c["intent"] for c in gallery + synth}
    for r in dev_rows:
        assert r["messages"] == [{"role": "system", "content": PROMPT}, {"role": "user", "content": intents[r["id"]]}]
    again = export_dev(PROMPT, DATA)
    assert again == dev_rows
    assert [r["id"] for r in export_dev(PROMPT, DATA, seed=1) if r["subset"]] != [r["id"] for r in dev_rows if r["subset"]]


def _cases():
    return {c["id"]: c for c in read_jsonl(DATA / "dev" / "cases.jsonl") + read_jsonl(DATA / "synth" / "dev" / "cases.jsonl")}


def test_score_summary_per_source(dev_rows):
    """T4: per-source summary; selection is the macro mean of row F1; length and error rows."""
    cases = _cases()
    g = [r["id"] for r in dev_rows if r["source"] == "gallery"][:2]
    s = [r["id"] for r in dev_rows if r["source"] == "synthetic"][:2]
    preds = [
        {"id": g[0], "checkpoint": "s025", "text": wrap(cases[g[0]]["reference_express"]), "finish": "stop"},
        {"id": g[1], "checkpoint": "s025", "text": None, "finish": "error"},
        {"id": s[0], "checkpoint": "s025", "text": wrap(cases[s[0]]["reference_express"]), "finish": "stop"},
        # cut mid-block: the scorer must see it as truncated
        {"id": s[1], "checkpoint": "s025", "text": "<a2ui>\n" + cases[s[1]]["reference_express"][:40], "finish": "length"},
    ]
    out = score_predictions(preds, dev_rows, DATA)
    assert out["checkpoint"] == "s025"
    gal, syn = out["sources"]["gallery"], out["sources"]["synthetic"]
    assert gal["rows"] == syn["rows"] == 2
    assert gal["slot_f1"] == pytest.approx(0.5) and gal["compiles"] == 0.5 and gal["exact_match"] == 0.5
    assert syn["truncated"] == 0.5 and 0.5 <= syn["slot_f1"] < 1.0
    assert out["selection"] == pytest.approx((gal["slot_f1"] + syn["slot_f1"]) / 2)
    assert {r["id"] for r in out["rows"]} == set(g + s)
    json.dumps(out)  # serialisable


def test_score_rejects_unknown_and_missing(dev_rows):
    """T5: an id outside dev, a duplicate, or a row with no prediction is an error, not a silent skip."""
    some = [r["id"] for r in dev_rows][:2]
    ok = [{"id": i, "checkpoint": "s000", "text": None, "finish": "error"} for i in some]
    with pytest.raises(CheckpointError, match="unknown"):
        score_predictions(ok + [{"id": "nope", "checkpoint": "s000", "text": None, "finish": "error"}], dev_rows, DATA)
    with pytest.raises(CheckpointError, match="duplicate"):
        score_predictions(ok + ok[:1], dev_rows, DATA)
    with pytest.raises(CheckpointError, match="missing"):
        score_predictions(ok, dev_rows, DATA, expect=set(some) | {[r["id"] for r in dev_rows][5]})
    with pytest.raises(CheckpointError, match="checkpoint"):
        score_predictions([ok[0], {**ok[1], "checkpoint": "s025"}], dev_rows, DATA)


def test_score_cli_subset_and_full(dev_rows, tmp_path, capsys):
    """The trainer calls this command by subprocess: subset by default, every row with --full."""
    from a2ui_eval.checkpoints import main

    dev = tmp_path / "dev.jsonl"
    dev.write_text("".join(json.dumps(r) + "\n" for r in dev_rows))
    preds = tmp_path / "p.jsonl"
    preds.write_text("".join(json.dumps({"id": r["id"], "checkpoint": "s025", "text": None, "finish": "error"}) + "\n" for r in dev_rows if r["subset"]))
    out = tmp_path / "s.json"
    assert main(["score", str(preds), "--out", str(out), "--dev", str(dev)]) == 0
    assert json.loads(out.read_text())["selection"] == 0.0
    assert main(["score", str(preds), "--out", str(out), "--dev", str(dev), "--full"]) == 1
    assert "missing predictions for 220" in capsys.readouterr().err


def _rows(gallery: list[float], synthetic: list[float]) -> list[dict]:
    return [{"source": "gallery", "slot_f1": f} for f in gallery] + [{"source": "synthetic", "slot_f1": f} for f in synthetic]


def test_selection_ci_collapses_when_every_row_is_equal():
    from a2ui_eval.checkpoints import selection_ci

    assert selection_ci(_rows([0.7] * 5, [0.7] * 9)) == pytest.approx([0.7, 0.7])


def test_selection_ci_resamples_within_each_source():
    """Stratified: each source keeps its row count, so a source that is all 1.0 next to one that
    is all 0.0 gives exactly 0.5 in every resample. A pooled bootstrap would spread around 0.5."""
    from a2ui_eval.checkpoints import selection_ci

    assert selection_ci(_rows([1.0] * 4, [0.0] * 30)) == pytest.approx([0.5, 0.5])


def test_selection_ci_brackets_selection_is_seeded_and_narrows_with_rows():
    from a2ui_eval.checkpoints import _mean, selection_ci

    small = _rows([0.0, 1.0] * 4, [0.2, 0.9] * 4)
    lo, hi = selection_ci(small)
    point = (_mean([0.0, 1.0]) + _mean([0.2, 0.9])) / 2
    assert lo < point < hi
    assert selection_ci(small) == [lo, hi]
    varied = _rows([i / 37 for i in range(37)], [(i * 7 % 23) / 23 for i in range(23)])
    assert selection_ci(varied, seed=1) != selection_ci(varied)
    big_lo, big_hi = selection_ci(_rows([0.0, 1.0] * 100, [0.2, 0.9] * 100))
    assert big_hi - big_lo < (hi - lo) / 2


def test_selection_ci_with_one_source_and_no_rows():
    from a2ui_eval.checkpoints import selection_ci

    lo, hi = selection_ci(_rows([], [0.0, 1.0] * 10))
    assert 0.0 < lo < 0.5 < hi < 1.0
    assert selection_ci([]) == [0.0, 0.0]


def test_score_output_carries_selection_ci(dev_rows, tmp_path, capsys):
    """The scores file and the CLI's stdout line both carry the interval next to `selection`."""
    from a2ui_eval.checkpoints import main

    cases = _cases()
    picked = [r for r in dev_rows if r["subset"]]
    preds = [
        {"id": r["id"], "checkpoint": "s025", "finish": "stop", "text": wrap(cases[r["id"]]["reference_express"])}
        if i % 2
        else {"id": r["id"], "checkpoint": "s025", "finish": "error", "text": None}
        for i, r in enumerate(picked)
    ]
    out = score_predictions(preds, dev_rows, DATA)
    lo, hi = out["selection_ci"]
    assert lo < out["selection"] < hi
    assert out["bootstrap"] == {"samples": 2000, "level": 0.95, "seed": 0, "stratified_by": ["gallery", "synthetic"]}

    dev = tmp_path / "dev.jsonl"
    dev.write_text("".join(json.dumps(r) + "\n" for r in dev_rows))
    p = tmp_path / "p.jsonl"
    p.write_text("".join(json.dumps(x) + "\n" for x in preds))
    assert main(["score", str(p), "--out", str(tmp_path / "s.json"), "--dev", str(dev)]) == 0
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["selection_ci"] == pytest.approx([lo, hi])
