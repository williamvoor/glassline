"""Teacher baseline (ADR 0025): Muse Spark Open answers the dev prompts, the existing scorer scores them.

The model is always stubbed. No test calls Baseten or any network."""

import asyncio
import importlib.util
import json
import subprocess
import sys

import pytest

from a2ui_eval import teacher
from a2ui_eval.teacher import TeacherError


def _row(i: str) -> dict:
    return {"id": i, "source": "gallery", "subset": False,
            "messages": [{"role": "system", "content": "S" * 400}, {"role": "user", "content": f"{i:<40}"}]}


ROWS = [_row("dev/a/0000"), _row("dev/a/0001"), _row("synth/dev/k/00000")]
ALLOWED = {r["id"] for r in ROWS}


def _reply(content="<a2ui>\nroot = Text(\"hi\")\n</a2ui>", finish="stop"):
    return {"content": content, "finish_reason": finish, "prompt_tokens": 110, "cached_tokens": 100, "completion_tokens": 20}


def _run(rows, out, complete, **kw):
    return asyncio.run(teacher.run_baseline(rows, out, complete, allowed_ids=kw.pop("allowed", ALLOWED), **kw))


def _lines(path):
    return [json.loads(l) for l in path.read_text().splitlines()]


def test_writes_one_scorer_prediction_per_row(tmp_path):
    out = tmp_path / "p.jsonl"
    seen = []

    async def complete(req):
        seen.append(req)
        return _reply()

    _run(ROWS, out, complete)
    preds = _lines(out)
    assert sorted(p["id"] for p in preds) == sorted(ALLOWED)
    for p in preds:
        assert p["checkpoint"] == teacher.CHECKPOINT
        assert p["finish"] == "stop"
        assert p["text"].startswith("<a2ui>")
        assert p["prompt_tokens"] == 110 and p["completion_tokens"] == 20
    # same sampling as the trainer's eval: greedy, 3072 tokens, the row's messages unchanged
    assert all(r["temperature"] == 0.0 and r["max_tokens"] == 3072 for r in seen)
    assert {json.dumps(r["messages"]) for r in seen} == {json.dumps(r["messages"]) for r in ROWS}


def test_fixed_cache_key_and_model(tmp_path):
    seen = []

    async def complete(req):
        seen.append(req)
        return _reply()

    _run(ROWS, tmp_path / "p.jsonl", complete, model="baseten/x")
    assert {r["cache_key"] for r in seen} == {teacher.CACHE_KEY}
    assert {r["model"] for r in seen} == {"baseten/x"}
    # LiteLLM's baseten provider rejects these unless allowed (smoke test, 2026-11-03)
    assert all(set(r["allowed_openai_params"]) == {"reasoning_effort", "prompt_cache_key"} for r in seen)


def test_length_finish_is_kept(tmp_path):
    async def complete(req):
        return _reply(finish="length")

    out = tmp_path / "p.jsonl"
    _run(ROWS[:1], out, complete)
    assert _lines(out)[0]["finish"] == "length"


def test_failed_call_is_an_error_row_and_the_run_goes_on(tmp_path):
    async def complete(req):
        if req["messages"] is ROWS[0]["messages"]:
            raise RuntimeError("boom")
        return _reply()

    out = tmp_path / "p.jsonl"
    summary = _run(ROWS, out, complete)
    preds = {p["id"]: p for p in _lines(out)}
    assert preds["dev/a/0000"]["finish"] == "error" and preds["dev/a/0000"]["text"] is None
    assert preds["dev/a/0001"]["finish"] == "stop"
    assert summary["errors"] == 1


def test_resume_skips_answered_rows_and_retries_errors(tmp_path):
    out = tmp_path / "p.jsonl"
    out.write_text(
        json.dumps({"id": "dev/a/0000", "checkpoint": teacher.CHECKPOINT, "text": "x", "finish": "stop"}) + "\n"
        + json.dumps({"id": "dev/a/0001", "checkpoint": teacher.CHECKPOINT, "text": None, "finish": "error"}) + "\n"
    )
    asked = []

    async def complete(req):
        asked.append(req["messages"])
        return _reply()

    _run(ROWS, out, complete)
    assert ROWS[0]["messages"] not in asked
    assert len(asked) == 2
    preds = _lines(out)
    assert sorted(p["id"] for p in preds) == sorted(ALLOWED)  # one line per id, the error replaced
    assert {p["id"]: p["finish"] for p in preds}["dev/a/0001"] == "stop"


def test_refuses_rows_outside_dev(tmp_path):
    async def complete(req):
        raise AssertionError("must not be called")

    with pytest.raises(TeacherError, match="test/x/0000"):
        _run([*ROWS, _row("test/x/0000")], tmp_path / "p.jsonl", complete)


def test_limit_takes_the_first_rows(tmp_path):
    async def complete(req):
        return _reply()

    out = tmp_path / "p.jsonl"
    _run(ROWS, out, complete, limit=2)
    assert sorted(p["id"] for p in _lines(out)) == ["dev/a/0000", "dev/a/0001"]


def test_estimate_uses_chars_and_list_prices():
    est = teacher.estimate(ROWS, out_tokens=400)
    in_tokens = 3 * 440 / teacher.CHARS_PER_TOKEN
    assert est["input_tokens"] == pytest.approx(in_tokens)
    assert est["usd"] == pytest.approx(in_tokens * teacher.PRICE_IN / 1e6 + 3 * 400 * teacher.PRICE_OUT / 1e6)


def test_actual_cost_from_usage():
    preds = [{"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000, "cached_tokens": 0}, {"finish": "error"}]
    assert teacher.actual_usd(preds) == pytest.approx(teacher.PRICE_IN + teacher.PRICE_OUT)


def test_no_llm_import_at_module_load():
    code = "import sys, a2ui_eval.teacher; assert 'litellm' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)
    assert importlib.util.find_spec("litellm") is None
