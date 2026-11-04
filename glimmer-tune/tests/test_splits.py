from collections import Counter

import pytest

from a2ui_eval.io import SplitError, load_splits
from a2ui_eval.seeds import load_seeds

MULTI_TURN = {"00_incremental", "31_incremental-dashboard"}


def test_partition_complete():
    seeds = load_seeds()
    splits = load_splits(seeds)
    assert set(splits) == set(seeds)
    counts = Counter(splits.values())
    assert 25 <= counts["train"] <= 27
    assert 7 <= counts["dev"] <= 9
    assert 8 <= counts["test"] <= 10
    assert {f for f, s in splits.items() if s == "excluded"} == MULTI_TURN


def test_unknown_family_rejected(tmp_path):
    path = tmp_path / "splits.yaml"
    path.write_text("train: [a, nope]\ndev: [b]\n")
    with pytest.raises(SplitError, match="unknown families"):
        load_splits({"a", "b"}, path)


def test_family_in_two_splits_rejected(tmp_path):
    path = tmp_path / "splits.yaml"
    path.write_text("train: [a]\ntest: [a]\n")
    with pytest.raises(SplitError, match="both"):
        load_splits({"a"}, path)


def test_family_without_split_rejected(tmp_path):
    path = tmp_path / "splits.yaml"
    path.write_text("train: [a]\n")
    with pytest.raises(SplitError, match="without a split"):
        load_splits({"a", "b"}, path)


def test_frozen_eval_cases_are_unchanged():
    """Approach v2: the dev and test generator is archived, so these files are the source of truth.
    Any change to them must be a decided step (rescore, update these pins)."""
    import hashlib

    from a2ui_eval.io import read_jsonl
    from a2ui_eval.seeds import ROOT

    splits = load_splits(load_seeds())
    for split, n, digest in (("dev", 84, "d0f5a6f753def3be"), ("test", 136, "f181ebe01bb0f87b")):
        path = ROOT / "data" / split / "cases.jsonl"
        rows = read_jsonl(path)
        assert len(rows) == n
        assert all(r["split"] == split and splits[r["family"]] == split for r in rows)
        assert hashlib.sha256(path.read_bytes()).hexdigest()[:16] == digest
