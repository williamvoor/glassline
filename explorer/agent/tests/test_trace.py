"""Traces (specs/trace/SPEC.md T1–T9)."""

import asyncio
import json

import pytest

from explorer_agent import trace
from explorer_agent.executor import LEVEL_KEY, TRACE_EVENT_KEY, TRACE_KEY
from explorer_agent.saved import omit_rows
from explorer_agent.sql import SqlRunner
from tests.test_saved import TOP_SQL, Agent, all_bytes, answer_steps, boards_dir, db_path  # noqa: F401 (fixtures)
from tests.test_server import CAPS

TRACED = {**CAPS, TRACE_KEY: True}


def _tool(content: dict) -> dict:
    return {"role": "tool", "tool_call_id": "c1", "content": json.dumps(content)}


async def test_untraced_records_nothing():
    await trace.emit({"kind": "x"})
    await trace.sql_event("SELECT 1", None, ok=True)
    await trace.model_event("m", [{"role": "system", "content": "S"}], reply=None, tokens=None, latency_ms=1)
    assert trace.active() is None


async def test_prompt_once_and_deltas():
    r = trace.Recorder()
    msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "q"}]
    with trace.recording(r):
        await trace.model_event("m", msgs, reply={"content": "a"}, tokens={"prompt": 3}, latency_ms=5)
        await trace.model_event("m", [*msgs, {"role": "assistant", "content": "a"}, {"role": "user", "content": "fix"}],
                                reply=None, tokens=None, latency_ms=5)
    kinds = [e["kind"] for e in r.events]
    assert kinds == ["prompt", "model", "model"]
    assert r.events[0]["text"] == "SYS" and r.events[1]["prompt_hash"] == r.events[0]["hash"]
    assert r.events[1]["messages"] == [{"role": "user", "content": "q"}]
    assert [m["content"] for m in r.events[2]["messages"]] == ["a", "fix"] and r.events[2]["first_message"] == 1
    assert [e["seq"] for e in r.events] == [0, 1, 2]
    # A prompt this Conversation has already saved is not sent again.
    again = trace.Recorder(known_prompts={r.events[0]["hash"]})
    with trace.recording(again):
        await trace.model_event("m", msgs, reply=None, tokens=None, latency_ms=1)
    assert [e["kind"] for e in again.events] == ["model"]


async def test_closed_recorder_drops():
    r = trace.Recorder()
    with trace.recording(r):
        task = asyncio.get_running_loop().create_task(asyncio.sleep(0))  # copies the context
        r.close()

        async def later():
            await trace.sql_event("SELECT 1", None, ok=True)

        await asyncio.get_running_loop().create_task(later())
        await task
    assert r.events == []


async def test_live_failure_is_swallowed():
    async def boom(_event):
        raise RuntimeError("client gone")

    r = trace.Recorder(boom)
    with trace.recording(r):
        await trace.sql_event("SELECT 1", [1], ok=True, row_count=1)
    assert len(r.events) == 1 and r.events[0]["params"] == [1]


def test_saved_form_omits_rows():
    events = [
        {"kind": "prompt", "hash": "h", "text": "SYS"},
        {"kind": "model", "messages": [_tool({"rows_preview": [{"name": "SECRET ROW"}], "row_count": 1})]},
        {"kind": "sql", "sql": "SELECT 1", "row_count": 1},
    ]
    saved = trace.saved_form(events, omit_rows)
    assert [e["kind"] for e in saved] == ["model", "sql"]
    assert "SECRET ROW" not in json.dumps(saved) and "rows_omitted" in json.dumps(saved)
    assert "SECRET ROW" in json.dumps(events)  # the live form is untouched


@pytest.mark.db
async def test_sql_events_from_runner(ro_pool):
    runner = SqlRunner(ro_pool, ("mart",), 5000, 2)
    r = trace.Recorder()
    with trace.recording(r), trace.source("run_sql"):
        await runner.run("SELECT supplier_name FROM mart.supplier ORDER BY 1")  # truncated at 2
        await runner.run("SELECT nope FROM mart.supplier")
        await runner.run("DELETE FROM mart.supplier")
    ok, failed, guarded = r.events
    assert ok["kind"] == "sql" and ok["source"] == "run_sql" and ok["ok"] and ok["row_count"] == 2 and ok["truncated"]
    assert "rows" not in ok and isinstance(ok["elapsed_ms"], int)
    assert not failed["ok"] and failed["sqlstate"] == "42703" and "nope" in failed["error"]
    assert not guarded["ok"] and guarded["sql"] == "DELETE FROM mart.supplier"


@pytest.mark.db
async def test_executor_streams_trace_and_saves_it_without_rows(ro_pool, db_path):  # noqa: F811
    agent = Agent(ro_pool, db_path, answer_steps())
    try:
        events = await agent.ask(metadata=TRACED)
        live = [e["status"]["message"]["metadata"][TRACE_EVENT_KEY] for e in events
                if e.get("kind") == "status-update" and not e.get("final")
                and TRACE_EVENT_KEY in ((e["status"].get("message") or {}).get("metadata") or {})]
        kinds = [e["kind"] for e in live]
        assert kinds == ["prompt", "model", "sql", "model", "sql"], kinds
        assert live[4]["source"] == "drill_check" and live[4]["row_count"] == 0
        assert live[0]["text"] == "SYSTEM"
        assert live[1]["reply"]["tool_calls"][0]["function"]["name"] == "run_sql"
        assert live[2]["sql"] == TOP_SQL and live[2]["source"] == "run_sql" and live[2]["row_count"] == 3
        # The second model call's new messages hold the run_sql preview the model saw.
        cid = events[-1]["contextId"]
        sentinel = agent.conversations.held(cid).store.get("r1").rows[1]["supplier_name"]
        assert sentinel in json.dumps(live[3]["messages"])
        final = events[-1]["status"]["message"]["metadata"]
        lid = f'{final[LEVEL_KEY]["turn"]}-{final[LEVEL_KEY]["level"]}'
        assert final[TRACE_KEY] == {"level": lid}

        saved = await agent.level(cid, lid)
        assert [e["kind"] for e in saved["trace"]["events"]] == ["model", "sql", "model", "sql"]
        assert saved["trace"]["prompts"] == {live[0]["hash"]: "SYSTEM"}
        assert sentinel not in json.dumps(saved["trace"])
        await agent.store.close()
        assert sentinel.encode() not in all_bytes(db_path)
    finally:
        await agent.client.aclose()


@pytest.mark.db
async def test_executor_untraced(ro_pool, db_path):  # noqa: F811
    agent = Agent(ro_pool, db_path, answer_steps())
    try:
        events = await agent.ask()
        metas = [((e.get("status") or {}).get("message") or {}).get("metadata") or {} for e in events]
        assert not any(TRACE_EVENT_KEY in m or TRACE_KEY in m for m in metas)
        cid = events[-1]["contextId"]
        assert "trace" not in await agent.level(cid, "1-1")
    finally:
        await agent.close()


async def test_llm_loop_model_events_with_tokens_and_a_retry():
    """T7: the LlmLoop's model events: a failed attempt (error, attempt 1), then its retry with the
    same messages, the reply's tool calls and the call's tokens."""
    from explorer_agent.llm import LlmLoop, LoopConfig
    from explorer_agent.testing.stub_model import ScriptedCompletion, text
    from explorer_agent.ui import UiPipeline
    from tests.test_llm import SYSTEM, FakeRunner, Statuses, new_conv, rate_limited

    async def sleep(_s):
        return None

    loop = LlmLoop(LoopConfig(max_tool_iterations=1), SYSTEM, UiPipeline(), FakeRunner(), None,
                   completion=ScriptedCompletion([rate_limited(), text("Hi.")]), sleep=sleep)
    r = trace.Recorder()
    with trace.recording(r):
        await loop(new_conv(), "q", Statuses())
    prompt, failed, ok = r.events
    assert prompt["kind"] == "prompt" and prompt["text"] == SYSTEM
    assert failed["error"].startswith("RateLimitError") and failed["attempt"] == 1 and failed["reply"] is None
    assert ok["attempt"] == 2 and ok["error"] is None and ok["reply"]["content"] == "Hi."
    assert ok["messages"] == failed["messages"] and ok["messages"][-1]["content"].endswith("q")
    assert ok["tokens"]["prompt"] is not None and set(ok["tokens"]) == {"prompt", "completion", "cached", "reasoning"}


@pytest.mark.db
async def test_a_control_change_appends_to_its_board_levels_trace(ro_pool, db_path, boards_dir):  # noqa: F811
    from tests.test_executor import act, meta, send_action

    agent = Agent(ro_pool, db_path, boards_dir=boards_dir)
    try:
        opened = await send_action(agent.client, {"name": "open_board", "context": {"board": "explorer"}}, None,
                                   extra={TRACE_KEY: True})
        await agent.executor.flush()
        cid, m = opened[-1]["contextId"], meta(opened)
        assert m[TRACE_KEY] == {"level": "0-1"}
        changed = await send_action(agent.client, act(m[LEVEL_KEY]["surfaceId"], "controls", board=m["explorerBoard"]["id"],
                                                      seq=1, values={"fy": 1}), cid, extra={TRACE_KEY: True})
        await agent.executor.flush()
        live = [e["status"]["message"]["metadata"][TRACE_EVENT_KEY] for e in changed
                if TRACE_EVENT_KEY in (((e.get("status") or {}).get("message") or {}).get("metadata") or {})]
        assert live and all(e["kind"] == "sql" and e["source"] == "board" for e in live)
        saved = (await agent.level(cid, "0-1"))["trace"]["events"]
        assert len(saved) > len(live) and saved[-len(live):] == live  # the open's events, then the change's
    finally:
        await agent.close()
