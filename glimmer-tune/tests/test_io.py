"""Shared helpers in io.py (moved from the archived generate.py)."""

import pytest

from a2ui_eval.io import read_jsonl, write_jsonl


def test_write_jsonl_is_atomic(tmp_path):
    # The draft and the review verdicts hold hand edits: a failed write must leave the old file as it was.
    path = tmp_path / "intents_draft.jsonl"
    write_jsonl(path, [{"id": "a", "intent": "my edit"}])
    with pytest.raises(TypeError):
        write_jsonl(path, [{"id": "a"}, {"id": "b", "bad": object()}])
    assert read_jsonl(path) == [{"id": "a", "intent": "my edit"}]
    assert [p.name for p in tmp_path.iterdir()] == ["intents_draft.jsonl"]  # no temp file left
