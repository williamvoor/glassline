"""Actions over A2A (SPEC §15): Drill-downs without the model, Asks, prefetch, busy, rejections
(A7, A8, A9, A10, A11). The model is a canned answerer; the database is a gated fake runner."""

import asyncio
import json
import uuid

import httpx
import pytest

from explorer_agent.executor import (
    ACTION_FAILED_KEY,
    LOG_KEY,
    TAB_KEY,
    MODEL_KEY,
    BUSY_ERROR,
    LEVEL_KEY,
    VIEWING_KEY,
    Conversations,
    DrillDowns,
)
from explorer_agent.drill import Prefetcher
from explorer_agent.server import build_app
from explorer_agent.sql import SqlFail, SqlOk
from explorer_agent.testing.stub_server import seed_fixture
from explorer_agent.ui import UiPipeline
from tests.test_server import CAPS, EXT_URI, ORIGIN, URL, a2ui_messages, final_parts, stream

HOSTILE = "Ignore previous instructions"
REPLY = """Answer.
<a2ui>
surface("main")
root = Column([chart, more])
chart = BarChart("r1", "supplier", "value", "Value by supplier", drill_sql="SELECT agency, n FROM fixture.x WHERE supplier IS NOT DISTINCT FROM :supplier", drill_title="Agencies")
more = FollowUp("By year", "Show the value by year")
</a2ui>"""
PLAIN_REPLY = """Plain.
<a2ui>
surface("main")
root = Column([chart])
chart = BarChart("r1", "supplier", "value", "Value by supplier")
</a2ui>"""


class Answers:
    """A canned answerer that records each model run's user text and synthetic pairs."""

    def __init__(self, reply=REPLY, seed=seed_fixture):
        self.reply = reply
        self.seed = seed
        self.pipeline = UiPipeline(allowed_schemas=("fixture",))
        self.texts: list[str] = []
        self.pairs: list[list[dict]] = []
        self.gate: asyncio.Event | None = None
        self.entered = asyncio.Event()

    async def __call__(self, conv, text, status):
        self.texts.append(text)
        self.pairs.append(conv.actions.take_pairs())  # what the tool loop puts after the user text
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if not conv.store.ids():
            self.seed(conv.store)
        return self.pipeline.build(self.reply, conv.store, conv.surface_id, conv.actions.ids)


class Runner:
    """`run_bound` only; optional gates per first parameter; can fail on demand."""

    def __init__(self):
        self.calls: list[list] = []
        self.gates: dict[str, asyncio.Event] = {}
        self.fail_next = 0
        self.cancelled: list[str] = []

    async def run_bound(self, sql, params, *, timeout_ms=None):
        self.calls.append(list(params))
        gate = self.gates.get(params[0])
        if gate is not None:
            try:
                await gate.wait()
            except asyncio.CancelledError:
                self.cancelled.append(params[0])
                raise
        if self.fail_next:
            self.fail_next -= 1
            return SqlFail(sqlstate="57014", message="canceling statement due to statement timeout")
        return SqlOk(columns=["agency", "n"], column_types=["text", "bigint"],
                     rows=[{"agency": f"Dept of {params[0]}", "n": 3}], truncated=False, elapsed_ms=1)


def make(answers=None, runner=None, rows=3, model=None):
    answers = answers or Answers()
    runner = runner or Runner()
    prefetcher = Prefetcher(runner, rows=rows, timeout_ms=3000, max_held=30) if rows is not None else None
    drills = DrillDowns(runner, answers.pipeline, prefetcher, rows_to_model=20)
    app = build_app(answers, url=URL, client_origin=ORIGIN, conversations=Conversations(), drills=drills, model=model)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL)
    return client, answers, runner, prefetcher


async def send_action(client, action, context_id, *, viewing=None, extra: dict | None = None) -> list[dict]:
    metadata = {**CAPS, **({VIEWING_KEY: viewing} if viewing is not None else {}), **(extra or {})}
    message = {
        "kind": "message", "role": "user", "messageId": str(uuid.uuid4()), "contextId": context_id,
        "parts": [{"kind": "data", "data": {"version": "v0.9", "action": action},
                   "metadata": {"mimeType": "application/json+a2ui"}}],
        "metadata": metadata,
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "message/stream", "params": {"message": message}}
    events = []
    async with client.stream("POST", "/", json=body, headers={"X-A2A-Extensions": EXT_URI}) as r:
        async for line in r.aiter_lines():
            if line.startswith("data:"):
                payload = json.loads(line[5:])
                assert "error" not in payload, payload
                events.append(payload["result"])
    return events


def meta(events) -> dict:
    return events[-1]["status"]["message"].get("metadata") or {}


def prose(events) -> str:
    return final_parts(events)[0]["text"]


def components(events) -> list[dict]:
    return [c for m in a2ui_messages(final_parts(events)) for c in (m.get("updateComponents") or {}).get("components", [])]


def chart_action(events) -> tuple[str, dict]:
    vega = next(c for c in components(events) if c["component"] == "VegaChart")
    return meta(events)[LEVEL_KEY]["surfaceId"], vega["action"]


def act(surface, name="drill", **context):
    return {"name": name, "surfaceId": surface, "sourceComponentId": "chart", "timestamp": "2026-11-03T00:00:00Z",
            "context": context}


async def first_answer(client):
    events = await stream(client)
    return events[-1]["contextId"], events


async def settle():
    for _ in range(10):
        await asyncio.sleep(0)


# --- A7 -----------------------------------------------------------------------------------------


async def test_drill_down_runs_the_bound_query_without_the_model():
    client, answers, runner, _ = make(rows=0)
    async with client:
        ctx, events = await first_answer(client)
        surface, action = chart_action(events)
        assert meta(events)[LEVEL_KEY] == {"turn": 1, "level": 1, "label": "Overview", "surfaceId": surface}
        assert action == {"source": "v1", "rows": [0, 1, 2], "drillable": True}
        out = await send_action(client, act(surface, source="v1", row=0), ctx)
    assert len(answers.texts) == 1  # the stub model was not called for the Drill-down
    assert runner.calls == [["Supplier A"]]
    level = meta(out)[LEVEL_KEY]
    assert (level["turn"], level["level"], level["label"]) == (1, 2, "Supplier A")
    assert level["surfaceId"].startswith("turn-1-2-")
    assert prose(out) == ""
    comps = components(out)
    title = next(c for c in comps if c["component"] == "Text" and c.get("variant") == "h4")
    assert title["text"] == "Agencies · Supplier A"
    grid = next(c for c in comps if c["component"] == "DataGrid")
    assert grid["rows"] == [["Dept of Supplier A", "3"]]
    assert grid["action"]["drillable"] is False  # one level without the model
    sql = next(c for c in comps if c["component"] == "SqlDetails")
    assert "IS NOT DISTINCT FROM 'Supplier A'" in sql["sql"]
    button = next(c for c in comps if c["component"] == "Button")
    assert button["action"]["event"] == {"name": "ask", "context": {"source": grid["action"]["source"]}}


async def test_drill_down_is_reported_to_the_next_model_run_as_a_synthetic_pair():
    client, answers, runner, _ = make(rows=0)
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        await send_action(client, act(surface, source="v1", row=1), ctx)
        await stream(client, "and then?", context_id=ctx)
    call, tool = answers.pairs[1]
    assert call["tool_calls"][0]["id"] == "synthetic-1-2"
    args = json.loads(call["tool_calls"][0]["function"]["arguments"])
    assert args["purpose"] == "Drill-down shown in Turn 1, Level 2"
    assert "'Supplier B'" in args["sql"]
    assert json.loads(tool["content"])["clicked"] == "Supplier B"
    assert "Supplier B" not in answers.texts[1]


async def test_failed_drill_down_shows_the_error_and_adds_no_level():
    runner = Runner()
    runner.fail_next = 1
    client, answers, runner, _ = make(runner=runner, rows=0)
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        out = await send_action(client, act(surface, source="v1", row=0), ctx)
        assert meta(out) == {ACTION_FAILED_KEY: True}
        assert "57014" in prose(out)
        retry = await send_action(client, act(surface, source="v1", row=0), ctx)
    assert meta(retry)[LEVEL_KEY]["level"] == 2  # the failed attempt used no Level number


# --- A10 ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "drill", "surfaceId": "SURFACE", "context": {"source": "v1", "row": 0}},  # missing fields
        {**act("SURFACE", "explode", source="v1", row=0)},
        {**act("turn-9-9-zzz", source="v1", row=0)},
        {**act("SURFACE", source="v42", row=0)},
        {**act("SURFACE", source="v1", row=99)},
        {**act("SURFACE", "ask", follow_up="f9")},
    ],
)
async def test_invalid_actions_call_neither_model_nor_database(bad):
    client, answers, runner, _ = make(rows=0)
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        bad = json.loads(json.dumps(bad).replace("SURFACE", surface))
        out = await send_action(client, bad, ctx)
    assert meta(out) == {ACTION_FAILED_KEY: True}
    assert len(answers.texts) == 1 and runner.calls == []


async def test_drill_down_on_a_plain_visual_is_rejected():
    client, answers, runner, _ = make(Answers(PLAIN_REPLY), rows=0)
    async with client:
        ctx, events = await first_answer(client)
        surface, action = chart_action(events)
        assert action["drillable"] is False
        out = await send_action(client, act(surface, source="v1", row=0), ctx)
    assert "no drill-down" in prose(out) and runner.calls == []


async def test_drill_down_while_a_model_run_holds_the_lock_is_busy_at_once():
    answers = Answers()
    client, answers, runner, _ = make(answers, rows=0)
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        answers.gate, answers.entered = asyncio.Event(), asyncio.Event()
        typed = asyncio.create_task(stream(client, "next", context_id=ctx))
        await answers.entered.wait()
        out = await asyncio.wait_for(send_action(client, act(surface, source="v1", row=0), ctx), 2)
        answers.gate.set()
        await typed
    assert prose(out) == BUSY_ERROR and runner.calls == []


async def test_drill_down_while_a_model_run_awaits_the_lock_is_busy():
    """A model run waiting behind a slow Drill-down makes the next Drill-down busy."""
    runner = Runner()
    runner.gates["Supplier A"] = asyncio.Event()
    client, answers, runner, _ = make(runner=runner, rows=0)
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        slow = asyncio.create_task(send_action(client, act(surface, source="v1", row=0), ctx))
        await settle()
        typed = asyncio.create_task(stream(client, "next", context_id=ctx))
        await settle()
        out = await asyncio.wait_for(send_action(client, act(surface, source="v1", row=1), ctx), 2)
        runner.gates["Supplier A"].set()
        await slow
        await typed
    assert prose(out) == BUSY_ERROR
    assert runner.calls == [["Supplier A"]]


# --- A11: Asks --------------------------------------------------------------------------------------


async def test_ask_on_a_row_sends_ids_only_and_the_row_in_a_synthetic_result():
    client, answers, runner, _ = make(Answers(PLAIN_REPLY), rows=0)
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        out = await send_action(client, act(surface, "ask", source="v1", row=2), ctx)
    assert answers.texts[1] == (
        "Ask: the user clicked a row of a Visual in Turn 1 (Result r1, row 2). Show the next level of detail "
        "for that row, keeping the same filters and period."
    )
    call, tool = answers.pairs[1]
    assert call["tool_calls"][0]["id"] == "synthetic-1-2"
    assert json.loads(call["tool_calls"][0]["function"]["arguments"])["purpose"] == "Row clicked in Turn 1"
    body = json.loads(tool["content"])
    assert (body["result_id"], body["row_index"], body["rows_preview"][0]["supplier"]) == ("r1", 2, "Supplier C")
    level = meta(out)[LEVEL_KEY]
    assert (level["turn"], level["level"], level["label"]) == (1, 2, "Supplier C")
    assert runner.calls == []


async def test_follow_up_ask_sends_its_request():
    client, answers, runner, _ = make(rows=0)
    async with client:
        ctx, events = await first_answer(client)
        surface = meta(events)[LEVEL_KEY]["surfaceId"]
        button = next(c for c in components(events) if c["component"] == "Button")
        fid = button["action"]["event"]["context"]["follow_up"]
        out = await send_action(client, act(surface, "ask", follow_up=fid), ctx)
    assert answers.texts[1] == "Show the value by year"
    assert meta(out)[LEVEL_KEY]["label"] == "By year"


async def test_ask_on_an_older_turn_adds_a_level_there():
    client, answers, runner, _ = make(Answers(PLAIN_REPLY), rows=0)
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        second = await stream(client, "again", context_id=ctx)
        assert meta(second)[LEVEL_KEY]["turn"] == 2
        out = await send_action(client, act(surface, "ask", source="v1", row=0), ctx)
    assert (meta(out)[LEVEL_KEY]["turn"], meta(out)[LEVEL_KEY]["level"]) == (1, 2)


async def test_viewing_line_names_the_selected_tab_when_it_is_not_the_newest():
    """SPEC §18 slice 1b: explorerViewing is a tab ID; the line keeps §15's form."""
    client, answers, runner, _ = make(rows=0)
    async with client:
        ctx, events = await first_answer(client)  # t1
        surface, _ = chart_action(events)
        await send_action(client, act(surface, source="v1", row=0), ctx)  # t2, the newest
        for viewing in ("t1", "t2", "t99", surface, ["t1"]):
            await stream(client, "why?", context_id=ctx, metadata={**CAPS, VIEWING_KEY: viewing})
    assert answers.texts[1] == "Viewing in Turn 1: Level 1\n\nwhy?"
    # t2 was the newest when sent; then each question's tab is the newest, so t1 still counts
    # only while it isn't; unknown IDs, surface IDs and lists send nothing.
    assert answers.texts[2:] == ["Viewing in Turn 1: Level 2\n\nwhy?", "why?", "why?", "why?"]


async def test_each_level_opens_a_tab_with_its_log_entry_and_failures_open_none():
    client, answers, runner, _ = make(rows=0, model="muse")
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        runner.fail_next = 1
        failed = await send_action(client, act(surface, source="v1", row=0), ctx)
        drill = await send_action(client, act(surface, source="v1", row=0), ctx)
        ask = await send_action(client, act(surface, "ask", source="v1", row=1), ctx)
    assert meta(events)[TAB_KEY] == {"id": "t1", "kind": "question", "title": "top suppliers?"}
    assert meta(events)[LOG_KEY] == {"kind": "question", "tag": "NEW TAB · muse", "text": "top suppliers?"}
    assert TAB_KEY not in meta(failed) and LOG_KEY not in meta(failed)
    assert meta(drill)[TAB_KEY] == {"id": "t2", "kind": "drill", "title": "Agencies · Supplier A"}
    assert meta(drill)[LOG_KEY] == {"kind": "drill", "tag": "NEW TAB · NO MODEL", "text": "Drill-down: Agencies · Supplier A"}
    assert meta(ask)[TAB_KEY] == {"id": "t3", "kind": "ask", "title": "Supplier B"}
    assert meta(ask)[LOG_KEY] == {"kind": "ask", "tag": "NEW TAB · muse", "text": "Ask: Supplier B"}


async def test_a_question_whose_view_fails_opens_no_tab():
    client, answers, _, _ = make(Answers('<a2ui>\nsurface("main")\nroot = Column([t])\nt = Text("{r9[0].x}")\n</a2ui>'), rows=0)
    async with client:
        _, events = await first_answer(client)
    assert "couldn't build the view" in prose(events)
    assert TAB_KEY not in meta(events) and LOG_KEY not in meta(events)


async def test_an_unnamed_model_tags_its_tab_new_tab_only():
    client, _, _, _ = make(rows=0)
    async with client:
        _, events = await first_answer(client)
    assert meta(events)[LOG_KEY]["tag"] == "NEW TAB"


async def test_tab_titles_are_plain_text():
    """The client renders titles as text, so a row label isn't markdown-escaped there."""
    runner = Runner()
    answers = Answers(seed=lambda store: store.add(
        sql="SELECT 1", purpose="p", columns=["supplier", "value"], column_types=["text", "numeric"],
        rows=[{"supplier": "*Acme*", "value": 1.0}], truncated=False))
    client, _, _, _ = make(answers, runner, rows=0)
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        out = await send_action(client, act(surface, source="v1", row=0), ctx)
    assert meta(out)[TAB_KEY]["title"] == "Agencies · *Acme*"


async def test_a_drill_title_filled_from_rows_is_plain_text_in_the_tab_and_log():
    """Regression: `_fill` markdown-escapes database values, which a plain-text tab shows as backslashes."""
    reply = REPLY.replace('drill_title="Agencies"', 'drill_title="Top: {r1[0].supplier} {r1[0].value|currency_m}"')
    answers = Answers(reply, seed=lambda store: store.add(
        sql="SELECT 1", purpose="p", columns=["supplier", "value"], column_types=["text", "numeric"],
        rows=[{"supplier": "Smith (Pty) Ltd.", "value": 1_500_000.0}], truncated=False))
    client, _, _, _ = make(answers, rows=0)
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        out = await send_action(client, act(surface, source="v1", row=0), ctx)
    assert meta(out)[TAB_KEY]["title"] == "Top: Smith (Pty) Ltd. $1.5M · Smith (Pty) Ltd."
    assert meta(out)[LOG_KEY]["text"] == "Drill-down: Top: Smith (Pty) Ltd. $1.5M · Smith (Pty) Ltd."


# --- A8: prefetch in the executor ----------------------------------------------------------------------


async def test_prefetch_runs_the_first_rows_and_a_click_on_one_runs_no_query():
    client, answers, runner, prefetcher = make(rows=2)
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        await settle()
        assert runner.calls == [["Supplier A"], ["Supplier B"]]  # displayed order, first 2
        out = await send_action(client, act(surface, source="v1", row=0), ctx)
        assert runner.calls == [["Supplier A"], ["Supplier B"]]
        assert meta(out)[LEVEL_KEY]["level"] == 2
        await send_action(client, act(surface, source="v1", row=2), ctx)  # not prefetched
    assert runner.calls[-1] == ["Supplier C"]


async def test_typed_question_cancels_prefetch_and_an_invalid_ask_does_not():
    runner = Runner()
    runner.gates["Supplier A"] = asyncio.Event()
    client, answers, runner, prefetcher = make(runner=runner, rows=1)
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        await settle()
        assert runner.calls == [["Supplier A"]]
        await send_action(client, act(surface, "ask", source="v1", row=99), ctx)  # invalid
        await settle()
        assert runner.cancelled == []
        await stream(client, "next", context_id=ctx)
        await settle()
    assert runner.cancelled[0] == "Supplier A"


async def test_a_drill_down_level_does_not_replace_the_prefetch():
    runner = Runner()
    runner.gates["Supplier B"] = asyncio.Event()
    client, answers, runner, prefetcher = make(runner=runner, rows=2)
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        await settle()
        await send_action(client, act(surface, source="v1", row=0), ctx)  # held: no query
        assert runner.cancelled == []
        runner.gates["Supplier B"].set()
        await settle()
        before = len(runner.calls)
        await send_action(client, act(surface, source="v1", row=1), ctx)
    assert len(runner.calls) == before  # still prefetched after the Drill-down Level


# Old issue 9: the Drill-down title is an h4 Text, which the client renders as plain text (Lit binds it as
# text, so a label can't inject markup). Escaping it showed backslashes. It was escaped since the
# refuter's 2026-11-03 finding, which assumed every Text renders markdown.
async def test_drill_down_title_shows_the_row_label_as_written_in_a_plain_text_heading():
    label = "[Click](https://evil.example) **bold**"
    reply = REPLY.replace('drill_title="Agencies"', 'drill_title="Top: {r1[0].supplier}"')

    def seed(store):
        store.add(sql="SELECT 1", purpose="p", columns=["supplier", "value"], column_types=["text", "numeric"],
                  rows=[{"supplier": label, "value": 1.0}], truncated=False)

    client, answers, runner, _ = make(Answers(reply, seed=seed), rows=0)
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        out = await send_action(client, act(surface, source="v1", row=0), ctx)
    title = next(c for c in components(out) if c["component"] == "Text" and c.get("variant") == "h4")
    assert title["text"] == f"Top: {label} · {label}"
    assert meta(out)[LEVEL_KEY]["label"] == label


# --- refuter round 1: cases that had no proving test ---------------------------------------------


async def test_busy_covers_a_woken_waiter():
    """The lock is free but a model run was woken and hasn't resumed: a Drill-down is still busy."""
    from explorer_agent.executor import Conversation, ExplorerExecutor

    conv = Conversation("c")
    await conv.lock.acquire()

    async def model_run():  # what ExplorerExecutor._model_run does around the lock
        conv.model_runs_waiting += 1
        try:
            async with conv.lock:
                await asyncio.sleep(0)
        finally:
            conv.model_runs_waiting -= 1

    waiter = asyncio.create_task(model_run())
    await asyncio.sleep(0)
    conv.lock.release()  # wakes the waiter; it hasn't run yet
    assert not conv.lock.locked()
    failed = []

    async def fail(text):
        failed.append(text)

    executor = ExplorerExecutor(None, Conversations(), None, DrillDowns(Runner(), None))
    await executor._drill(conv, None, None, fail)
    assert failed == [BUSY_ERROR]
    await waiter


async def test_a_valid_ask_cancels_the_running_prefetch():
    runner = Runner()
    runner.gates["Supplier A"] = asyncio.Event()
    client, answers, runner, _ = make(runner=runner, rows=1)
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        await settle()
        assert runner.calls == [["Supplier A"]] and runner.cancelled == []
        answers.gate, answers.entered = asyncio.Event(), asyncio.Event()
        ask = asyncio.create_task(send_action(client, act(surface, "ask", source="v1", row=1), ctx))
        await answers.entered.wait()
        await settle()
        assert runner.cancelled == ["Supplier A"]  # while the Ask's model run is still running
        answers.gate.set()
        await ask


async def test_a_typed_question_cancels_prefetch_before_waiting_for_the_lock():
    runner = Runner()
    runner.gates["Supplier A"] = asyncio.Event()  # the prefetch
    runner.gates["Supplier C"] = asyncio.Event()  # a slow Drill-down holding the lock
    client, answers, runner, _ = make(runner=runner, rows=1)
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        await settle()
        slow = asyncio.create_task(send_action(client, act(surface, source="v1", row=2), ctx))
        await settle()
        typed = asyncio.create_task(stream(client, "next", context_id=ctx))
        await settle()
        assert not slow.done()  # the lock is still held
        assert runner.cancelled == ["Supplier A"]
        runner.gates["Supplier C"].set()
        await slow
        await typed


async def test_prefetch_does_not_start_while_another_model_run_waits():
    client, answers, runner, _ = make(rows=1)
    async with client:
        ctx, _events = await first_answer(client)
        await settle()
        gate2, gate3 = asyncio.Event(), asyncio.Event()
        answers.gate, answers.entered = gate2, asyncio.Event()
        second = asyncio.create_task(stream(client, "again", context_id=ctx))
        await answers.entered.wait()
        answers.gate, answers.entered = gate3, asyncio.Event()  # the third run will wait on gate3
        third = asyncio.create_task(stream(client, "and again", context_id=ctx))
        await settle()
        before = len(runner.calls)
        gate2.set()  # the second run ends while the third waits for the lock
        await answers.entered.wait()  # the third run is now answering
        await settle()
        assert len(runner.calls) == before  # the second run started no prefetch
        gate3.set()
        await second
        await third


async def test_a_failed_ask_says_the_action_failed():
    answers = Answers(PLAIN_REPLY)
    client, answers, runner, _ = make(answers, rows=0)
    async with client:
        ctx, events = await first_answer(client)
        surface, _ = chart_action(events)
        answers.reply = '<a2ui>\nsurface("main")\nroot = Column([t])\nt = Text("{r9[0].x}")\n</a2ui>'
        out = await send_action(client, act(surface, "ask", source="v1", row=0), ctx)
    assert meta(out) == {ACTION_FAILED_KEY: True}


# --- SPEC §16 L5 ---------------------------------------------------------------------------------


async def test_each_level_names_its_serving_model_or_none_for_a_drill_down():
    client, _, _, _ = make(rows=0, model="meta/test-model")
    async with client:
        ctx, events = await first_answer(client)
        assert meta(events)[MODEL_KEY] == "meta/test-model"  # a typed question is a model run
        surface, _ = chart_action(events)
        drill = await send_action(client, act(surface, source="v1", row=0), ctx)
        assert MODEL_KEY in meta(drill) and meta(drill)[MODEL_KEY] is None  # no model, database only
        ask = await send_action(client, act(surface, name="ask", source="v1", row=1), ctx)
        assert meta(ask)[MODEL_KEY] == "meta/test-model"
        failed = await send_action(client, act(surface, source="v1", row=99), ctx)
    assert meta(failed) == {ACTION_FAILED_KEY: True}  # no Level, so no model either



async def test_a_model_run_without_a_model_name_sends_no_model_key():
    client, _, _, _ = make(rows=0)  # no model name given
    async with client:
        _, events = await first_answer(client)
    assert MODEL_KEY not in meta(events)  # absent, not null: null means "no model"


# --- open_board (SPEC §18, B5) ---------------------------------------------------------------------

from explorer_agent.executor import NO_BOARDS_KEY, BoardViews  # noqa: E402
from explorer_agent.testing.stub_server import STUB_BOARD, StubBoardRunner  # noqa: E402
from explorer_agent.boards import BoardRenderer  # noqa: E402


class NoModel(Answers):
    async def __call__(self, conv, text, status):
        raise AssertionError("the model must not be called for a Board")


def make_boards(boards=True):
    answers = NoModel()
    views = None
    if boards:
        renderer = BoardRenderer({"explorer": STUB_BOARD}, StubBoardRunner(), concurrency=2, row_cap=500, ttl_s=300, max_cache=20)
        views = BoardViews(renderer, answers.pipeline)
    app = build_app(answers, url=URL, client_origin=ORIGIN, conversations=Conversations(), boards=views)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL)


def open_board(board="explorer", **extra):
    return {"name": "open_board", "surfaceId": "", "sourceComponentId": "", "timestamp": "2026-11-03T00:00:00Z",
            "context": {"board": board, **extra}}


async def test_open_board_renders_the_explorer_as_turn_0_without_the_model():
    async with make_boards() as client:
        events = await send_action(client, open_board(), None)
    m = meta(events)
    assert not m.get(ACTION_FAILED_KEY)
    assert m[LEVEL_KEY]["turn"] == 0 and m[LEVEL_KEY]["level"] == 1
    assert m[LEVEL_KEY]["surfaceId"].startswith("turn-0-1-")
    assert m[MODEL_KEY] is None  # no model, database only
    kinds = {c["component"] for c in components(events)}
    assert {"VegaChart", "DataGrid", "SqlDetails"} <= kinds


async def test_a_second_open_board_unknown_and_malformed_are_rejected():
    async with make_boards() as client:
        events = await send_action(client, open_board(), None)
        context_id = events[-1]["contextId"]
        again = await send_action(client, open_board(), context_id)
        unknown = await send_action(client, open_board("nope"), None)
        malformed = await send_action(client, open_board(extra=1), None)
    assert meta(again)[ACTION_FAILED_KEY] and "already open" in prose(again)
    assert meta(unknown)[ACTION_FAILED_KEY] and "Unknown board" in prose(unknown)
    assert meta(malformed)[ACTION_FAILED_KEY] and "malformed" in prose(malformed)


async def test_open_board_without_boards_says_so():
    async with make_boards(boards=False) as client:
        events = await send_action(client, open_board(), None)
    m = meta(events)
    assert m[ACTION_FAILED_KEY] and m[NO_BOARDS_KEY]


async def test_a_question_after_the_board_uses_the_next_turn_and_unique_result_ids():
    answers = Answers()
    renderer = BoardRenderer({"explorer": STUB_BOARD}, StubBoardRunner(), concurrency=2, row_cap=500, ttl_s=300, max_cache=20)
    conversations = Conversations()
    app = build_app(answers, url=URL, client_origin=ORIGIN, conversations=conversations,
                    boards=BoardViews(renderer, answers.pipeline))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL) as client:
        events = await send_action(client, open_board(), None)
        context_id = events[-1]["contextId"]
        conv = conversations.get_or_create(context_id)
        board_ids = {r.id for inst in conv.boards.values() for r in inst.results.values()}
        conv.store.add(sql="SELECT 1", purpose="p", columns=["x"], column_types=["int"], rows=[], truncated=False)
        assert not board_ids & set(conv.store.ids())
        assert conv.actions.new_turn().turn == 1


async def test_the_stub_app_answers_a_question_after_its_board():
    """Regression: the Board takes r1–r4, so the stub's canned reply must name the fixture's own ID."""
    from explorer_agent.testing.stub_server import stub_answerer, stub_boards, stub_drills

    app = build_app(stub_answerer, url=URL, client_origin=ORIGIN, conversations=Conversations(),
                    drills=stub_drills(), boards=stub_boards())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL) as client:
        board = await send_action(client, open_board(), None)
        events = await stream(client, context_id=board[-1]["contextId"])
    assert not meta(events).get(ACTION_FAILED_KEY)
    assert "couldn't build the view" not in prose(events)
    assert any(c["component"] == "VegaChart" for c in components(events))


async def test_the_stub_app_answers_a_question_after_a_drill_down_on_its_board():
    """Regression: the stub named the store's first Result, which an Explorer Drill-down had taken."""
    from explorer_agent.testing.stub_server import stub_answerer, stub_boards, stub_drills

    app = build_app(stub_answerer, url=URL, client_origin=ORIGIN, conversations=Conversations(),
                    drills=stub_drills(), boards=stub_boards())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL) as client:
        board = await send_action(client, open_board(), None)
        ctx = board[-1]["contextId"]
        grid = next(c for c in components(board) if c["component"] == "DataGrid")
        await send_action(client, act(meta(board)[LEVEL_KEY]["surfaceId"], source=grid["action"]["source"], row=0), ctx)
        events = await stream(client, context_id=ctx)
    assert "couldn't build the view" not in prose(events)


async def test_a_board_that_fails_to_build_can_be_opened_again(monkeypatch):
    import explorer_agent.executor as executor_module
    from explorer_agent.ui import UiError

    real = executor_module.build_board
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise UiError(["invalid UI: boom"])
        return real(*args, **kwargs)

    monkeypatch.setattr(executor_module, "build_board", flaky)
    async with make_boards() as client:
        first = await send_action(client, open_board(), None)
        context_id = first[-1]["contextId"]
        second = await send_action(client, open_board(), context_id)
    assert meta(first)[ACTION_FAILED_KEY] and "couldn't be shown" in prose(first)
    assert not meta(second).get(ACTION_FAILED_KEY)
    assert meta(second)[LEVEL_KEY]["level"] == 1  # the failed attempt's Level was rolled back


# --- SPEC §18 slice 1b: clicks on the Explorer, tabs -------------------------------------------------


class FirstResultAnswers(Answers):
    """PLAIN_REPLY naming the store's first Result (a Board takes the first IDs, SPEC §18)."""

    async def __call__(self, conv, text, status):
        if not conv.store.ids():
            self.seed(conv.store)
        self.reply = PLAIN_REPLY.replace('"r1"', f'"{conv.store.ids()[0]}"')
        return await super().__call__(conv, text, status)


def make_board_app(answers=None):
    from explorer_agent.testing.stub_server import stub_boards, stub_drills

    answers = answers or FirstResultAnswers()
    conversations = Conversations()
    drills = stub_drills()
    app = build_app(answers, url=URL, client_origin=ORIGIN, conversations=conversations, drills=drills,
                    boards=stub_boards(), model="muse")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL)
    return client, answers, conversations, drills.prefetcher


def grid_action(events) -> tuple[str, dict]:
    grid = next(c for c in components(events) if c["component"] == "DataGrid")
    return meta(events)[LEVEL_KEY]["surfaceId"], grid["action"]


async def test_the_explorer_opens_tab_t1_and_its_table_drills_into_turn_0_with_prefetch():
    client, answers, conversations, prefetcher = make_board_app()
    async with client:
        board = await send_action(client, open_board(), None)
        ctx = board[-1]["contextId"]
        conv = conversations.get_or_create(ctx)
        surface, action = grid_action(board)
        assert action["drillable"] is True
        await settle()
        assert (action["source"], 0) in prefetcher.held(ctx)  # the Explorer's first rows, as for an answer
        assert conv.actions.sources == {}  # Board sources live in the instance, outside MAX_ACTION_SOURCES
        drill = await send_action(client, act(surface, source=action["source"], row=0), ctx)
    assert meta(board)[TAB_KEY] == {"id": "t1", "kind": "board", "title": "Explorer"}
    assert meta(board)[LOG_KEY] == {"kind": "board", "tag": "NEW TAB · NO MODEL", "text": "Opened Explorer"}
    assert not meta(drill).get(ACTION_FAILED_KEY)
    level = meta(drill)[LEVEL_KEY]
    assert (level["turn"], level["level"], level["label"]) == (0, 2, "Supplier A")
    assert meta(drill)[TAB_KEY] == {"id": "t2", "kind": "drill", "title": "Contracts by buyer · Supplier A"}


async def test_an_ask_on_an_explorer_row_copies_its_result_into_the_store():
    client, answers, conversations, _ = make_board_app()
    async with client:
        board = await send_action(client, open_board(), None)
        ctx = board[-1]["contextId"]
        conv = conversations.get_or_create(ctx)
        surface, action = grid_action(board)
        board_ids = {r.id for inst in conv.boards.values() for r in inst.results.values()}
        out = await send_action(client, act(surface, "ask", source=action["source"], row=1), ctx)
    assert not meta(out).get(ACTION_FAILED_KEY)
    body = json.loads(answers.pairs[0][1]["content"])
    assert body["result_id"] not in board_ids  # the model sees a store ID only
    assert conv.store.get(body["result_id"]).rows[1]["supplier"] == "Supplier B"
    assert "Visual in Turn 0" in answers.texts[0]
    assert meta(out)[TAB_KEY] == {"id": "t2", "kind": "ask", "title": "Supplier B"}


async def test_the_explorer_tab_never_gets_a_viewing_line():
    client, answers, _, _ = make_board_app()
    async with client:
        board = await send_action(client, open_board(), None)
        ctx = board[-1]["contextId"]
        await stream(client, "first", context_id=ctx, metadata={**CAPS, VIEWING_KEY: "t1"})
        await stream(client, "second", context_id=ctx, metadata={**CAPS, VIEWING_KEY: "t1"})
    assert answers.texts == ["first", "second"]  # the model hasn't seen the Board (slice 2's Board pair)
