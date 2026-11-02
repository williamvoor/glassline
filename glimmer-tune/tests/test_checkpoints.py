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


# --- test split (one-time evaluation of the final candidate) ---------------------------------


@pytest.fixture(scope="module")
def test_rows():
    from a2ui_eval.checkpoints import export_test

    return export_test(PROMPT, DATA)


def _test_cases():
    return {c["id"]: c for c in read_jsonl(DATA / "test" / "cases.jsonl")}


def test_export_test_rows(test_rows):
    """Every frozen test case once, id-sorted, gallery source, its family, the frozen system text."""
    from a2ui_eval.checkpoints import export_test

    cases = _test_cases()
    assert len(test_rows) == len(cases) == 136
    assert [r["id"] for r in test_rows] == sorted(cases)
    assert {r["source"] for r in test_rows} == {"gallery"}
    assert not any(r["subset"] for r in test_rows)
    for r in test_rows:
        assert r["family"] == cases[r["id"]]["family"]
        assert r["messages"] == [{"role": "system", "content": PROMPT}, {"role": "user", "content": cases[r["id"]]["intent"]}]
    assert len({r["family"] for r in test_rows}) == 8
    assert export_test(PROMPT, DATA) == test_rows


def test_score_test_split_uses_test_references(test_rows, dev_rows):
    """--split test scores against test references and rejects dev ids."""
    cases = _test_cases()
    ids = [r["id"] for r in test_rows][:2]
    preds = [
        {"id": ids[0], "checkpoint": "s250", "text": wrap(cases[ids[0]]["reference_express"]), "finish": "stop"},
        {"id": ids[1], "checkpoint": "s250", "text": None, "finish": "error"},
    ]
    out = score_predictions(preds, test_rows, DATA, split="test")
    assert out["selection"] == pytest.approx(0.5)
    assert {r["family"] for r in out["rows"]} == {cases[i]["family"] for i in ids}
    assert out["bootstrap"]["clustered_by"] == "family"
    with pytest.raises(CheckpointError, match="unknown"):
        score_predictions([{"id": dev_rows[0]["id"], "checkpoint": "s250", "text": None, "finish": "error"}], test_rows, DATA, split="test")


def _fam(values: dict[str, list[float]]) -> list[dict]:
    return [{"id": f"{f}/{i}", "family": f, "slot_f1": v} for f, vs in values.items() for i, v in enumerate(vs)]


def test_family_ci_resamples_whole_families():
    """Rows in a family are correlated, so families are resampled, not rows. Two families, one
    all 1.0 and one all 0.0: a family resample can draw the same family twice, so the interval
    spans 0..1. A row bootstrap would sit tightly around the mean."""
    from a2ui_eval.checkpoints import family_ci

    assert family_ci(_fam({"a": [1.0] * 20, "b": [0.0] * 20})) == pytest.approx([0.0, 1.0])
    assert family_ci(_fam({"a": [0.6] * 3, "b": [0.6] * 9})) == pytest.approx([0.6, 0.6])
    assert family_ci([]) == [0.0, 0.0]


def test_family_ci_brackets_the_row_mean():
    from a2ui_eval.checkpoints import _mean, family_ci

    rows = _fam({f: [(i * 7 + k) % 10 / 10 for k in range(5 + i)] for i, f in enumerate("abcdefgh")})
    lo, hi = family_ci(rows)
    assert lo < _mean([r["slot_f1"] for r in rows]) < hi
    assert family_ci(rows) == [lo, hi]


def test_compare_ci_is_paired_by_row_and_clustered_by_family():
    """The difference b - a is taken per row and families are resampled, so a constant
    improvement gives a zero-width interval even when families differ a lot."""
    from a2ui_eval.checkpoints import compare_ci

    a = _fam({"a": [0.1, 0.2], "b": [0.8, 0.9, 0.7], "c": [0.4]})
    b = [{**r, "slot_f1": r["slot_f1"] + 0.1} for r in a]
    out = compare_ci(a, b)
    assert out["diff"] == pytest.approx(0.1)
    assert out["diff_ci"] == pytest.approx([0.1, 0.1])
    with pytest.raises(CheckpointError, match="same rows"):
        compare_ci(a, b[:-1])


def test_score_and_compare_cli_on_test(test_rows, tmp_path, capsys):
    """`score --split test` needs every test row; `compare` reads two score files."""
    from a2ui_eval.checkpoints import main

    cases = _test_cases()
    test = tmp_path / "test.jsonl"
    test.write_text("".join(json.dumps(r) + "\n" for r in test_rows))

    def preds(ckpt, good):
        p = tmp_path / f"{ckpt}.jsonl"
        p.write_text("".join(
            json.dumps({"id": r["id"], "checkpoint": ckpt, "finish": "stop", "text": wrap(cases[r["id"]]["reference_express"])}
                       if good(i) else {"id": r["id"], "checkpoint": ckpt, "finish": "error", "text": None}) + "\n"
            for i, r in enumerate(test_rows)))
        return p

    base, tuned = preds("s000", lambda i: i % 4 == 0), preds("s250", lambda i: i % 2 == 0)
    assert main(["score", str(base), "--out", str(tmp_path / "s000.json"), "--split", "test", "--dev", str(test)]) == 0
    assert main(["score", str(tuned), "--out", str(tmp_path / "s250.json"), "--split", "test", "--dev", str(test)]) == 0
    capsys.readouterr()
    assert main(["compare", str(tmp_path / "s000.json"), str(tmp_path / "s250.json")]) == 0
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["a"] == "s000" and line["b"] == "s250"
    assert line["diff"] == pytest.approx(0.25)
    lo, hi = line["diff_ci"]
    assert lo < 0.25 < hi

    partial = tmp_path / "partial.jsonl"
    partial.write_text(base.read_text().splitlines()[0] + "\n")
    assert main(["score", str(partial), "--out", str(tmp_path / "x.json"), "--split", "test", "--dev", str(test)]) == 1
    assert "missing predictions for 135" in capsys.readouterr().err


def test_family_ci_takes_the_row_mean_of_each_draw():
    """Refuter B1: with unequal family sizes, the row mean of a draw differs from the mean of its
    family means. Replays the seeded draws with explicit row-mean arithmetic."""
    import random as _random

    from a2ui_eval.checkpoints import family_ci

    fams = {"a": [1.0], "b": [0.0] * 9, "c": [0.5, 0.5, 0.2]}
    rows = _fam(fams)
    clusters = [fams[f] for f in sorted(fams)]
    rng, n = _random.Random(0), 2000
    stats = []
    for _ in range(n):
        draw = rng.choices(clusters, k=len(clusters))
        stats.append(sum(sum(c) for c in draw) / sum(len(c) for c in draw))
    stats.sort()
    for level in (0.5, 0.95):
        tail = (1 - level) / 2
        assert family_ci(rows, level=level) == pytest.approx([stats[int(tail * n)], stats[min(n - 1, int((1 - tail) * n))]])


def _test_scores(tmp_path, test_rows, name, ids=None, split="test"):
    rows = [{"id": r["id"], "family": r["family"], "slot_f1": 0.5} for r in test_rows if ids is None or r["id"] in ids]
    if split != "test":
        rows = [{k: v for k, v in r.items() if k != "family"} for r in rows]
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"checkpoint": name, "split": split, "selection": 0.5, "rows": rows}))
    return path


def test_score_test_needs_every_frozen_test_row_even_with_a_partial_export(test_rows, tmp_path, capsys):
    """Refuter B2: the test guard is the frozen test cases, not the rows of the export passed in."""
    from a2ui_eval.checkpoints import main

    part = tmp_path / "part.jsonl"
    part.write_text("".join(json.dumps(r) + "\n" for r in test_rows[:3]))
    preds = tmp_path / "p.jsonl"
    preds.write_text("".join(json.dumps({"id": r["id"], "checkpoint": "s250", "text": None, "finish": "error"}) + "\n" for r in test_rows[:3]))
    assert main(["score", str(preds), "--out", str(tmp_path / "s.json"), "--split", "test", "--dev", str(part)]) == 1
    assert "missing predictions for 133" in capsys.readouterr().err
    assert not (tmp_path / "s.json").exists()


def test_compare_cli_rejects_dev_partial_and_mismatched_files(test_rows, tmp_path, capsys):
    from a2ui_eval.checkpoints import main

    full = _test_scores(tmp_path, test_rows, "s000")
    ids = {r["id"] for r in test_rows}
    cases = [
        (_test_scores(tmp_path, test_rows, "dev", split="dev"), "test score files"),
        (_test_scores(tmp_path, test_rows, "part", ids=set(sorted(ids)[:3])), "every test row"),
        (_test_scores(tmp_path, test_rows, "empty", ids=set()), "every test row"),
    ]
    for other, msg in cases:
        assert main(["compare", str(full), str(other)]) == 1
        assert msg in capsys.readouterr().err
    assert main(["compare", str(full), str(_test_scores(tmp_path, test_rows, "s250"))]) == 0


def test_score_reports_blind_next_to_literal(dev_rows):
    """N7: rows, sources and the summary carry the name-blind score; selection is unchanged."""
    cases = _cases()
    g = [r["id"] for r in dev_rows if r["source"] == "gallery"][:2]
    ref = cases[g[0]]["reference_express"]
    preds = [
        {"id": g[0], "checkpoint": "s025", "text": wrap(ref), "finish": "stop"},
        {"id": g[1], "checkpoint": "s025", "text": None, "finish": "error"},
    ]
    out = score_predictions(preds, dev_rows, DATA)
    assert [r["slot_f1_blind"] for r in out["rows"]] == [1.0, 0.0]
    assert out["sources"]["gallery"]["slot_f1_blind"] == pytest.approx(0.5)
    assert out["selection_blind"] == pytest.approx(0.5)
    assert out["selection"] == pytest.approx(0.5)


def test_blind_selection_is_wired_from_slot_blind_as_a_macro_mean(dev_rows):
    """Refuter (N7): a renamed path makes blind > literal on a row, and unequal source counts
    make the macro mean differ from the row mean."""
    import re

    cases = _cases()
    gal = next(r["id"] for r in dev_rows if r["source"] == "gallery" and re.search(r"\$/\w+", cases[r["id"]]["reference_express"]))
    syn = [r["id"] for r in dev_rows if r["source"] == "synthetic"][:2]
    ref = cases[gal]["reference_express"]
    renamed = re.sub(r"\$/(\w+)", r"$/zz\1", ref)
    preds = [
        {"id": gal, "checkpoint": "s025", "text": wrap(renamed), "finish": "stop"},
        {"id": syn[0], "checkpoint": "s025", "text": wrap(cases[syn[0]]["reference_express"]), "finish": "stop"},
        {"id": syn[1], "checkpoint": "s025", "text": None, "finish": "error"},
    ]
    out = score_predictions(preds, dev_rows, DATA)
    row = next(r for r in out["rows"] if r["id"] == gal)
    assert row["slot_f1"] < 1.0 and row["slot_f1_blind"] == 1.0
    assert out["sources"]["gallery"]["slot_f1_blind"] == 1.0
    assert out["selection_blind"] == pytest.approx((1.0 + 0.5) / 2)  # macro mean, not the row mean 2/3
    assert out["selection"] == pytest.approx((row["slot_f1"] + 0.5) / 2)
    assert out["selection"] < out["selection_blind"]
