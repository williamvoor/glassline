from collections import Counter

import pytest

from a2ui_eval.generate import GenerationError, load_splits
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
    with pytest.raises(GenerationError, match="unknown families"):
        load_splits({"a", "b"}, path)


def test_family_in_two_splits_rejected(tmp_path):
    path = tmp_path / "splits.yaml"
    path.write_text("train: [a]\ntest: [a]\n")
    with pytest.raises(GenerationError, match="both"):
        load_splits({"a"}, path)


def test_family_without_split_rejected(tmp_path):
    path = tmp_path / "splits.yaml"
    path.write_text("train: [a]\n")
    with pytest.raises(GenerationError, match="without a split"):
        load_splits({"a", "b"}, path)
