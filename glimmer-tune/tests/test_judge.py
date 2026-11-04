"""Phase 3 judge calibration: pool, page, labels and agreement. No model is called."""

import json
from collections import Counter

import pytest

from a2ui_eval import judge as J


def _cands(per_intent: dict[str, list[bool]]):
    return {cid: [{"sample": k, "text": f"<a2ui>{cid}{k}</a2ui>", "pass": p} for k, p in enumerate(ps)]
            for cid, ps in per_intent.items()}


def test_pool_takes_only_passing_candidates_and_spreads_over_intents():
    cands = _cands({"a": [True] * 4, "b": [True, False, True, False], "c": [False] * 4, "d": [True]})
    pool = J.label_pool(cands, n=5, seed=1)
    by = {p["key"]: p for p in pool}
    assert all(cands[p["id"]][p["sample"]]["pass"] for p in pool)
    assert len(by) == 5
    counts = Counter(p["id"] for p in pool)
    assert counts["d"] == 1 and counts["b"] == 2 and counts["a"] == 2 and "c" not in counts


def test_pool_is_seeded():
    cands = _cands({c: [True] * 4 for c in "abcdef"})
    assert J.label_pool(cands, 10, 7) == J.label_pool(cands, 10, 7)


def test_pool_refuses_when_too_few_pass():
    with pytest.raises(J.JudgeError, match="only 1"):
        J.label_pool(_cands({"a": [True, False]}), n=2, seed=0)


def test_page_lists_every_candidate_with_its_rubric_and_escapes_text():
    pool = [{"key": "intent/x/1#0", "id": "intent/x/1", "sample": 0, "text": "<a2ui>root = Text(\"<b>\")</a2ui>"}]
    out = J.page(pool, {"intent/x/1": "Show <b>sales</b>"}, lambda cid, k: ["shots/x_0.375.png"])
    assert 'data-key="intent/x/1#0"' in out
    assert all(f'data-k="{k}"' in out for k in J.RUBRIC_KEYS)
    assert "Show &lt;b&gt;sales&lt;/b&gt;" in out and "<b>sales</b>" not in out
    assert 'class="s375"' in out


def _write_labels(tmp_path, labels):
    p = tmp_path / "labels.json"
    p.write_text(json.dumps({"labels": labels}))
    return p


POOL = [{"key": "a#0"}, {"key": "b#1"}]


def test_labels_pass_when_nothing_fails(tmp_path):
    p = _write_labels(tmp_path, {"a#0": {"fails": [], "note": ""}, "b#1": {"fails": ["action"], "note": "no submit"}})
    assert J.load_labels(p, POOL) == {"a#0": True, "b#1": False}


def test_labels_refuse_a_missing_label(tmp_path):
    with pytest.raises(J.JudgeError, match="no label"):
        J.load_labels(_write_labels(tmp_path, {"a#0": {"fails": []}}), POOL)


def test_labels_refuse_an_unknown_rubric_item_or_key(tmp_path):
    with pytest.raises(J.JudgeError, match="unknown rubric"):
        J.load_labels(_write_labels(tmp_path, {"a#0": {"fails": ["vibes"]}, "b#1": {"fails": []}}), POOL)
    with pytest.raises(J.JudgeError, match="outside the pool"):
        J.load_labels(_write_labels(tmp_path, {"a#0": {"fails": []}, "b#1": {"fails": []}, "z#9": {"fails": []}}), POOL)


def test_always_pass_judge_fails_the_bar_on_kappa_despite_high_raw_agreement():
    """The judge-disagreement case: at a 90% person pass rate a judge that always says pass gets
    0.9 raw agreement, above 0.85, but kappa 0 shows it has learnt nothing, so it misses the bar."""
    person = {str(i): i >= 5 for i in range(50)}
    a = J.agreement(person, {k: True for k in person})
    assert a["agreement"] == 0.9 and a["kappa"] == 0.0 and not a["meets_bar"]
    assert a["judge_passes_person_fail"] == 5


def test_agreement_counts_disagreements_both_ways():
    a = J.agreement({"a": True, "b": False, "c": True, "d": False}, {"a": True, "b": True, "c": False, "d": False})
    assert (a["both_pass"], a["both_fail"], a["judge_passes_person_fail"], a["judge_fails_person_pass"]) == (1, 1, 1, 1)
    assert a["agreement"] == 0.5 and not a["meets_bar"]


# ---------------------------------------------------------------- the judge call (stubbed model)

def _reply(fails=(), **extra):
    return json.dumps({k: {"why": "x", "ok": k not in fails} for k in J.RUBRIC_KEYS} | extra)


def test_parse_reply_returns_the_failed_items():
    assert J.parse_reply(_reply()) == []
    assert J.parse_reply("```json\n" + _reply(["action", "layout"]) + "\n```") == ["action", "layout"]


@pytest.mark.parametrize("text, match", [
    ("looks fine to me", "no JSON"),
    ("{not json}", "bad JSON"),
    (json.dumps({"elements": {"ok": True}}), "rubric keys"),
    (_reply(extra_item={"ok": True}), "rubric keys"),
    (json.dumps({k: {"why": "x", "ok": "yes"} for k in J.RUBRIC_KEYS}), "boolean ok"),
])
def test_parse_reply_never_turns_a_malformed_reply_into_a_pass(text, match):
    with pytest.raises(J.JudgeError, match=match):
        J.parse_reply(text)


def test_system_prompt_is_the_same_for_every_candidate(tmp_path):
    """The stable prefix for prompt caching: no per-candidate text in the system message."""
    shot = tmp_path / "s.png"
    shot.write_bytes(b"\x89PNG")
    a = J.judge_messages("Show sales", "<a2ui>a</a2ui>", [shot])
    b = J.judge_messages("Log a loan", "<a2ui>b</a2ui>", [shot, shot])
    assert a[0] == b[0] and a[0]["content"] == J.SYSTEM
    assert all(k in J.SYSTEM for k in J.RUBRIC_KEYS)
    assert [c["type"] for c in b[1]["content"]] == ["text", "image_url", "image_url"]
    assert b[1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def _judge_setup(tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir()
    pool = [{"key": f"intent/x/{i}#0", "id": f"intent/x/{i}", "sample": 0, "text": "<a2ui>x</a2ui>"} for i in range(2)]
    for p in pool:
        for w in J.WIDTHS:
            (shots / f"{p['id'].replace('/', '_')}_0.{w}.png").write_bytes(b"\x89PNG")
    return pool, {p["id"]: "a request" for p in pool}, shots


def test_run_judge_repeats_each_candidate_at_low_reasoning_and_resumes(tmp_path):
    import asyncio

    pool, intents, shots = _judge_setup(tmp_path)
    out = tmp_path / "run.jsonl"
    calls = []

    async def stub(req):
        calls.append(req)
        if len(calls) == 1:
            raise RuntimeError("429")
        if len(calls) == 2:
            return {"content": "not json", "finish_reason": "stop"}
        return {"content": _reply(), "finish_reason": "stop", "prompt_tokens": 100, "completion_tokens": 10}

    s = asyncio.run(J.run_judge(pool, intents, shots, out, stub, repeats=3, workers=1))
    assert s["calls"] == 6 and s["ok"] == 4 and s["not_ok"] == {"error": 1, "length": 0, "bad_output": 1}
    assert all(r["reasoning_effort"] == "low" and r["cache_key"] == J.CACHE_KEY for r in calls)
    s = asyncio.run(J.run_judge(pool, intents, shots, out, stub, repeats=3, workers=1))
    assert s["calls"] == 2 and s["ok"] == 6  # only the error and the bad output are retried
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [(r["key"], r["repeat"]) for r in rows] == [(p["key"], i) for p in pool for i in range(3)]


def test_run_judge_refuses_a_candidate_without_screenshots(tmp_path):
    import asyncio

    pool, intents, shots = _judge_setup(tmp_path)
    next(shots.iterdir()).unlink()

    async def stub(req):
        return {"content": _reply(), "finish_reason": "stop"}

    asyncio.run(J.run_judge(pool, intents, shots, tmp_path / "run.jsonl", stub, repeats=1))
    rows = [json.loads(line) for line in (tmp_path / "run.jsonl").read_text().splitlines()]
    assert sorted(r["finish"] for r in rows) == ["error", "ok"]
    assert "missing screenshots" in next(r["error"] for r in rows if r["finish"] == "error")


def test_verdicts_take_the_majority_count_flips_and_score_the_rubric():
    ok = lambda k, i, fails: {"key": k, "repeat": i, "finish": "ok", "fails": fails}  # noqa: E731
    rows = [ok("a", 0, []), ok("a", 1, []), ok("a", 2, ["layout"]),   # flips, passes 2 to 1
            ok("b", 0, ["action"]), ok("b", 1, []),                     # a tie fails
            ok("c", 0, []), {"key": "c", "repeat": 1, "finish": "error"},
            {"key": "d", "repeat": 0, "finish": "bad_output"}]
    v = J.verdicts(rows)
    assert v["passed"] == {"a": True, "b": False, "c": True}
    assert v["flipped"] == ["a", "b"] and v["unjudged"] == ["d"]
    assert v["score"]["a"] == round(1 - 1 / 18, 3) and v["score"]["c"] == 1.0


def test_a_judge_that_agrees_and_discriminates_meets_the_bar():
    person = {str(i): i >= 10 for i in range(50)}
    judge = dict(person) | {"0": True, "20": False}  # 2 disagreements in 50
    a = J.agreement(person, judge)
    assert a["agreement"] == 0.96 and a["kappa"] >= J.KAPPA_BAR and a["meets_bar"]
