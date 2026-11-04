"""Controls over A2A (SPEC §18 slice 2): re-query in place, superseded replies, busy, the Board
pair, released sources and the Ask copy (B7, B8, B10, B21). No model, no database: the stub
Explorer Board, whose `buyer` Control filters the trend."""

import asyncio
import dataclasses
import json
import logging

import httpx
import pytest

from explorer_agent.actions import TOO_OLD
from explorer_agent.boards import BoardRenderer
from explorer_agent.executor import (
    ACTION_FAILED_KEY,
    BOARD_KEY,
    BUSY_ERROR,
    IN_PLACE_KEY,
    LEVEL_KEY,
    LOG_KEY,
    SUPERSEDED_KEY,
    TAB_KEY,
    BoardViews,
    Conversations,
)
from explorer_agent.server import build_app
from explorer_agent.testing.stub_server import STUB_BOARD, STUB_BOARD_ROWS, StubBoardRunner, stub_drills
from tests.test_executor import (
    FirstResultAnswers,
    act,
    components,
    meta,
    open_board,
    prose,
    send_action,
    settle,
)
from tests.test_server import CAPS, a2ui_messages, final_parts, stream
from explorer_agent.executor import VIEWING_KEY


def ask(client, ctx, viewing=None):
    metadata = {**CAPS, **({VIEWING_KEY: viewing} if viewing else {})}
    return stream(client, context_id=ctx, metadata=metadata)


class Runner(StubBoardRunner):
    """The stub Board's rows, recording each query and gated per bound buyer."""

    def __init__(self):
        self.sql: list[str] = []
        self.gates: dict[str, asyncio.Event] = {}
        self.entered = asyncio.Event()

    async def run(self, sql):
        self.sql.append(sql)
        return await super().run(sql)

    async def run_bound(self, sql, params, *, timeout_ms=None):
        gate = self.gates.get(params[0]) if params else None
        if gate is not None:
            self.entered.set()
            await gate.wait()
        return await super().run_bound(sql, params, timeout_ms=timeout_ms)


def make(board=STUB_BOARD, answers=None, **limits):
    answers = answers or FirstResultAnswers()
    runner = Runner()
    renderer = BoardRenderer({"explorer": board}, runner, concurrency=2, row_cap=500, ttl_s=300, max_cache=20)
    conversations = Conversations(**limits)
    drills = stub_drills()
    app = build_app(answers, url="http://agent.test", client_origin="http://127.0.0.1:5173",
                    conversations=conversations, drills=drills, boards=BoardViews(renderer, answers.pipeline),
                    model="muse")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test")
    return client, answers, runner, conversations, drills.prefetcher


def controls(surface, seq, buyer, board="b1", **extra):
    return {"name": "controls", "surfaceId": surface, "sourceComponentId": "board_controls",
            "timestamp": "2026-11-03T00:00:00Z", "context": {"board": board, "seq": seq, "values": {"buyer": buyer}, **extra}}


async def opened(client):
    events = await send_action(client, open_board(), None)
    return events, events[-1]["contextId"], meta(events)[LEVEL_KEY]["surfaceId"]


def updates(events) -> list[dict]:
    return a2ui_messages(final_parts(events))


def chart_values(comps, root="years") -> list[float]:
    chart = next(c for c in comps if c["component"] == "VegaChart" and c["id"].split("__")[0] == root)
    return [v["value"] for v in chart["spec"]["data"]["values"]]


# --- opening ------------------------------------------------------------------------------------------


async def test_the_board_reply_names_its_instance_and_controls_and_sends_their_data_model():
    client, *_ = make()
    async with client:
        events, _, surface = await opened(client)
    assert meta(events)[BOARD_KEY] == {"id": "b1", "controls": [{"name": "buyer", "kind": "choice"}]}
    model = [m["updateDataModel"] for m in updates(events) if "updateDataModel" in m]
    assert model == [{"surfaceId": surface, "path": "/controls", "value": {"buyer": ["all"]}}]
    picker = next(c for c in components(events) if c["id"] == "board_control_buyer")
    assert picker["options"][0] == {"label": "All buyers", "value": "all"} and len(picker["options"]) == 6


# --- B7: re-query in place --------------------------------------------------------------------------------


async def test_a_change_replaces_only_its_panels_in_place_with_no_controls_card_or_data_model(caplog):
    caplog.set_level(logging.INFO)
    client, _, runner, conversations, _ = make()
    async with client:
        board, ctx, surface = await opened(client)
        before = chart_values(components(board))
        runner.sql.clear()
        out = await send_action(client, controls(surface, 1, 1), ctx)
    m = meta(out)
    assert m[IN_PLACE_KEY] == {"board": "b1", "seq": 1}
    assert m[LOG_KEY] == {"kind": "controls", "tag": "IN PLACE · NO MODEL", "text": "Buyer set to Buyer B"}
    assert LEVEL_KEY not in m and TAB_KEY not in m and not m.get(ACTION_FAILED_KEY)
    (message,) = updates(out)
    assert list(message) == ["version", "updateComponents"] and message["updateComponents"]["surfaceId"] == surface
    ids = [c["id"] for c in message["updateComponents"]["components"]]
    assert ids and all(i == "years" or i.startswith("years__") for i in ids)
    after = chart_values(message["updateComponents"]["components"])
    assert after != before  # Buyer B's share (the chart picks its own compact unit)
    assert [a / after[0] for a in after] == pytest.approx([b / before[0] for b in before])
    assert len(runner.sql) == 1 and "_explorer_board" in runner.sql[0]  # only by_year re-ran
    inst = conversations.get_or_create(ctx).boards["b1"]
    assert inst.state == {"buyer": 1} and inst.seq_applied == 1
    assert "Buyer B" not in caplog.text  # logs carry no Control values or labels


async def test_back_to_the_defaults_is_served_from_the_cache_and_says_controls_reset():
    client, _, runner, _, _ = make()
    async with client:
        _, ctx, surface = await opened(client)
        await send_action(client, controls(surface, 1, 1), ctx)
        runner.sql.clear()
        out = await send_action(client, controls(surface, 2, None), ctx)
    assert meta(out)[LOG_KEY]["text"] == "Controls reset"
    assert runner.sql == []  # the pinned Explorer entry at its defaults: no query


async def test_newest_wins_a_superseded_change_is_skipped_and_its_panels_re_run_against_the_last_render():
    client, _, runner, conversations, _ = make()
    async with client:
        _, ctx, surface = await opened(client)
        runner.gates["Buyer A"] = asyncio.Event()
        first = asyncio.create_task(send_action(client, controls(surface, 1, 0), ctx))
        await runner.entered.wait()  # seq 1 holds the instance lock
        second = asyncio.create_task(send_action(client, controls(surface, 2, 1), ctx))
        third = asyncio.create_task(send_action(client, controls(surface, 3, 2), ctx))
        await settle()
        runner.gates["Buyer A"].set()
        one, two, three = await asyncio.gather(first, second, third)
        stale = await send_action(client, controls(surface, 2, 4), ctx)
    assert meta(one)[IN_PLACE_KEY]["seq"] == 1
    assert meta(two) == {SUPERSEDED_KEY: True} and updates(two) == []
    assert meta(three)[IN_PLACE_KEY]["seq"] == 3 and meta(three)[LOG_KEY]["text"] == "Buyer set to Buyer C"
    assert meta(stale) == {SUPERSEDED_KEY: True}
    assert conversations.get_or_create(ctx).boards["b1"].state == {"buyer": 2}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda s: controls(s, 1, 9), "isn't allowed for Buyer"),
        (lambda s: controls(s, 1, "1"), "isn't allowed for Buyer"),
        (lambda s: {**controls(s, 1, 1), "context": {"board": "b1", "seq": 1, "values": {}}}, "missing"),
        (lambda s: {**controls(s, 1, 1), "context": {"board": "b1", "seq": 1, "values": {"buyer": 1, "x": 1}}}, "Unknown control"),
        (lambda s: controls(s, 1, 1, extra=True), "malformed"),
        (lambda s: controls(s, True, 1), "malformed"),
        (lambda s: controls(s, 0, 1), "malformed"),
        (lambda s: controls(s, 1, 1, board="b9"), "Unknown board"),
        (lambda s: controls("turn-9-1-x", 1, 1), "doesn't match its view"),
    ],
)
async def test_rejected_changes_run_nothing(change, message):
    client, _, runner, conversations, _ = make()
    async with client:
        _, ctx, surface = await opened(client)
        runner.sql.clear()
        out = await send_action(client, change(surface), ctx)
    assert meta(out)[ACTION_FAILED_KEY] and message in prose(out)
    assert runner.sql == []
    inst = conversations.get_or_create(ctx).boards["b1"]
    assert inst.state == {"buyer": None} and inst.seq_seen == 0


async def test_a_change_during_a_model_run_is_busy():
    client, answers, runner, _, _ = make()
    async with client:
        _, ctx, surface = await opened(client)
        answers.gate = asyncio.Event()
        question = asyncio.create_task(stream(client, context_id=ctx))
        await answers.entered.wait()
        out = await send_action(client, controls(surface, 1, 1), ctx)
        answers.gate.set()
        await question
    assert meta(out)[ACTION_FAILED_KEY] and prose(out) == BUSY_ERROR


async def test_a_model_run_arriving_mid_re_query_waits_and_its_pair_stays_resolvable_until_it_ends():
    seen = {}

    class Pinning(FirstResultAnswers):
        async def __call__(self, conv, text, status):
            pair = [m for m in conv.actions.board_pair or []]
            seen["pair"] = pair
            body = json.loads(pair[1]["content"])
            ids = [r["id"] for r in body["results"]]
            inst = conv.boards["b1"]
            seen["completed"] = inst.results["by_year"].id in ids
            inst.results = {}  # as if a later render released them: the pinned copies still resolve
            seen["resolves"] = all(conv.results.get(i) is not None for i in ids)
            return await super().__call__(conv, text, status)

    client, answers, runner, conversations, _ = make(answers=Pinning())
    async with client:
        _, ctx, surface = await opened(client)
        runner.gates["Buyer A"] = asyncio.Event()
        change = asyncio.create_task(send_action(client, controls(surface, 1, 0), ctx))
        await runner.entered.wait()
        question = asyncio.create_task(ask(client, ctx, "t1"))
        await settle()
        assert not answers.entered.is_set()  # it waits for the re-query
        runner.gates["Buyer A"].set()
        await asyncio.gather(change, question)
    assert seen["completed"] and seen["resolves"]
    assert conversations.get_or_create(ctx).pinned == {}  # released once the run ended


async def test_a_released_source_is_too_old_and_an_untouched_panel_keeps_its_source():
    client, _, _, _, _ = make()
    async with client:
        board, ctx, surface = await opened(client)
        sources = {c["id"].split("__")[0]: c["action"]["source"] for c in components(board) if c.get("action")}
        await send_action(client, controls(surface, 1, 1), ctx)
        old = await send_action(client, act(surface, "ask", source=sources["years"], row=0), ctx)
        kept = await send_action(client, act(surface, source=sources["suppliers"], row=0), ctx)
    assert meta(old)[ACTION_FAILED_KEY] and prose(old) == TOO_OLD
    assert not meta(kept).get(ACTION_FAILED_KEY)


async def test_a_failed_rebuild_changes_nothing(monkeypatch):
    import explorer_agent.executor as executor_module
    from explorer_agent.ui import UiError

    client, _, _, conversations, _ = make()
    async with client:
        _, ctx, surface = await opened(client)

        def boom(*args, **kwargs):
            raise UiError(["invalid UI: boom"])

        monkeypatch.setattr(executor_module, "build_board", boom)
        out = await send_action(client, controls(surface, 1, 1), ctx)
    assert meta(out)[ACTION_FAILED_KEY] and "couldn't be updated" in prose(out)
    inst = conversations.get_or_create(ctx).boards["b1"]
    assert inst.state == {"buyer": None} and inst.seq_applied == 0


# --- B8: the Board pair -------------------------------------------------------------------------------


def board_pairs(pairs: list[dict]) -> list[dict]:
    return [m for m in pairs if m["role"] == "assistant"
            and json.loads(m["tool_calls"][0]["function"]["arguments"])["purpose"].startswith("Board shown")]


async def test_the_board_pair_is_sent_only_for_the_viewed_board_off_its_defaults():
    client, answers, _, _, _ = make()
    async with client:
        _, ctx, surface = await opened(client)
        await ask(client, ctx, "t1")  # at its defaults: nothing to say
        await send_action(client, controls(surface, 1, 1), ctx)
        await ask(client, ctx, "t2")  # a question's tab: not a Board
        await ask(client, ctx, "t1")
    assert [len(board_pairs(p)) for p in answers.pairs] == [0, 0, 1]
    call, result = answers.pairs[2][-2:]
    args = json.loads(call["tool_calls"][0]["function"]["arguments"])
    assert args["purpose"] == "Board shown in Tab t1"
    assert "'Buyer B'::text" in args["sql"] and "Explorer" not in args["sql"]  # display copies, no titles
    body = json.loads(result["content"])
    assert body["board"] == "Explorer" and body["controls"] == {"Buyer": "Buyer B"}
    assert [r["purpose"] for r in body["results"]] == [f"Explorer · {q.name}" for q in STUB_BOARD.queries]
    assert all(len(r["rows_preview"]) <= 5 for r in body["results"])
    assert all("Buyer B" not in t for t in answers.texts)  # user text carries no labels
    assert all("Viewing" not in t for t in answers.texts)  # never a Viewing line for a Board tab


async def test_the_board_pair_is_outside_max_pending_pairs_and_cut_to_its_size():
    client, answers, _, _, _ = make(max_pending_pairs=1, board_pair_max_chars=900)
    async with client:
        board, ctx, surface = await opened(client)
        await send_action(client, controls(surface, 1, 1), ctx)
        grid = next(c for c in components(board) if c["component"] == "DataGrid")
        await send_action(client, act(surface, "ask", source=grid["action"]["source"], row=0), ctx, viewing="t1")
    pairs = answers.pairs[0]
    assert len(pairs) == 4  # the row pair, then the Board pair
    assert "Row clicked" in pairs[0]["tool_calls"][0]["function"]["arguments"]
    assert len(pairs[3]["content"]) <= 900


# --- the Ask copy, prefetch keyed by Control values ----------------------------------------------------


async def test_asks_on_a_board_row_reuse_its_store_copy():
    client, answers, _, conversations, _ = make()
    async with client:
        board, ctx, surface = await opened(client)
        grid = next(c for c in components(board) if c["component"] == "DataGrid")
        for _ in range(2):
            await send_action(client, act(surface, "ask", source=grid["action"]["source"], row=0), ctx)
    ids = [json.loads(p[1]["content"])["result_id"] for p in answers.pairs]
    assert ids[0] == ids[1]
    assert sum(1 for i in conversations.get_or_create(ctx).store.ids() if i == ids[0]) == 1


async def test_a_change_used_by_a_drill_query_rebuilds_its_panel_so_prefetched_rows_are_not_reused():
    layout = STUB_BOARD.layout.replace(
        "WHERE supplier IS NOT DISTINCT FROM :supplier",
        "WHERE supplier IS NOT DISTINCT FROM :supplier AND (:buyer IS NULL OR buyer = :buyer)",
    )
    client, _, _, _, prefetcher = make(board=dataclasses.replace(STUB_BOARD, layout=layout))
    async with client:
        board, ctx, surface = await opened(client)
        grid = next(c for c in components(board) if c["component"] == "DataGrid")
        await settle()
        assert (grid["action"]["source"], 0) in prefetcher.held(ctx)
        out = await send_action(client, controls(surface, 1, 1), ctx)
        new = next(c for c in updates(out)[0]["updateComponents"]["components"] if c["component"] == "DataGrid")
        await settle()
        assert new["action"]["source"] != grid["action"]["source"]
        assert (new["action"]["source"], 0) in prefetcher.held(ctx)
        drill = await send_action(client, act(surface, source=grid["action"]["source"], row=0), ctx)
    assert meta(drill)[ACTION_FAILED_KEY] and prose(drill) == TOO_OLD


async def test_unchanged_queries_keep_their_result_ids():
    client, _, _, conversations, _ = make()
    async with client:
        _, ctx, surface = await opened(client)
        inst = conversations.get_or_create(ctx).boards["b1"]
        before = {n: r.id for n, r in inst.results.items()}
        await send_action(client, controls(surface, 1, 1), ctx)
        after = {n: r.id for n, r in inst.results.items()}
    assert {n for n in before if before[n] != after[n]} == {"by_year"}
    assert set(STUB_BOARD_ROWS) == set(after)


async def test_texts_and_cites_reading_a_changed_query_are_re_sent_with_the_new_values():
    # The changed set's second rule (SPEC §18 slice 2): a `{query[i].col}` placeholder in a
    # passthrough Text, a Cite, and an inline Text nested in the layout.
    layout = STUB_BOARD.layout.replace(
        "root = Column([metrics, Row([tiles, years]), suppliers])",
        'root = Column([metrics, Row([tiles, years]), suppliers, note, lead, Card(Text("First {by_year[0].value|currency_compact}"))])\n'
        'note = Text("Latest {by_year[2].value|currency_compact}")\n'
        'lead = Cite("{by_year[1].value|currency_compact} in the middle year.")',
    )
    client, *_ = make(board=dataclasses.replace(STUB_BOARD, layout=layout))
    async with client:
        board, ctx, surface = await opened(client)
        out = await send_action(client, controls(surface, 1, 1), ctx)
    before = {c["id"]: c for c in components(board)}
    sent = {c["id"]: c for c in updates(out)[0]["updateComponents"]["components"]}
    roots = {i.split("__")[0] for i in sent}
    assert {"years", "note", "lead"} <= roots
    inline = roots - {"years", "note", "lead"}
    assert len(inline) == 1 and not inline & {"root", "board_layout", "tiles", "suppliers", "metrics"}
    texts = [c for c in sent.values() if c["component"] == "Text" and c["id"].split("__")[0] != "years"]
    assert any("Latest" in c["text"] for c in texts) and any("First" in c["text"] for c in texts)
    assert any("middle year" in c["text"] for c in texts)
    for c in texts:
        assert c["text"] != before[c["id"]]["text"], c["id"]  # new values, same IDs


async def test_a_change_keeps_prefetch_going_for_the_panels_it_did_not_touch():
    # Regression: the in-place reply restarted prefetch with the re-sent panels' sources only,
    # which cancelled the untouched table's queued prefetch for good.
    # The trend gets a Drill query, so the re-sent panel has prefetch jobs of its own.
    layout = STUB_BOARD.layout.replace(
        'format="currency_compact")',
        'format="currency_compact", drill_sql="SELECT buyer, count(*) AS contracts FROM fixture.contracts '
        'WHERE year = :year GROUP BY buyer", drill_as="bar", drill_x="buyer", drill_y="contracts", drill_title="By buyer")',
    )
    assert layout != STUB_BOARD.layout
    client, _, _, _, prefetcher = make(board=dataclasses.replace(STUB_BOARD, layout=layout))
    gate = asyncio.Event()
    run_bound = prefetcher.runner.run_bound

    async def gated(sql, params, **kw):
        await gate.wait()
        return await run_bound(sql, params, **kw)

    prefetcher.runner.run_bound = gated
    async with client:
        board, ctx, surface = await opened(client)
        grid = next(c for c in components(board) if c["component"] == "DataGrid")
        await send_action(client, controls(surface, 1, 1), ctx)
        assert (grid["action"]["source"], 0) not in prefetcher.held(ctx)  # still waiting
        gate.set()
        await settle()
        assert (grid["action"]["source"], 0) in prefetcher.held(ctx)


async def test_a_re_query_forgets_the_store_copies_of_the_results_it_released():
    client, _, _, conversations, _ = make()
    async with client:
        board, ctx, surface = await opened(client)
        chart = next(c for c in components(board) if c["component"] == "VegaChart" and c["id"].startswith("years"))
        grid = next(c for c in components(board) if c["component"] == "DataGrid")
        for source in (chart["action"]["source"], grid["action"]["source"]):
            await send_action(client, act(surface, "ask", source=source, row=0), ctx)
        inst = conversations.get_or_create(ctx).boards["b1"]
        old = inst.results["by_year"].id
        assert old in inst.copies
        await send_action(client, controls(surface, 1, 1), ctx)
    assert old not in inst.copies
    assert inst.results["top_suppliers"].id in inst.copies  # untouched: kept
