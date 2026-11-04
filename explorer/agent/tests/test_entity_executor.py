"""Entity Boards over A2A (SPEC §18 slice 3): a row click opens a keyed Board with no model (B9),
its tab and log, prefetch, the Board pair at defaults (B8), `MAX_BOARD_INSTANCES` (B21). No
model, no database: the stub Explorer and its `buyer` Entity Board."""

import asyncio
import dataclasses
import json

import httpx

from explorer_agent.actions import TOO_OLD
from explorer_agent.boards import BoardRenderer, Control
from explorer_agent.drill import rewrite
from explorer_agent.executor import (
    ACTION_FAILED_KEY,
    BOARD_KEY,
    BUSY_ERROR,
    ENTITY_FAILED,
    KEYED_BOARD,
    LEVEL_KEY,
    LOG_KEY,
    MODEL_KEY,
    TAB_KEY,
    BoardViews,
    Conversations,
)
from explorer_agent.server import build_app
from explorer_agent.sql import SqlFail, SqlOk
from explorer_agent.testing.stub_server import (
    STUB_BOARD,
    STUB_ENTITY,
    StubBoardRunner,
    _pipeline,
    stub_drills,
)
from explorer_agent.ui import UiError
from tests.test_executor import (
    ORIGIN,
    URL,
    FirstResultAnswers,
    act,
    components,
    meta,
    open_board,
    send_action,
    settle,
)
from tests.test_controls_executor import controls, updates


class Runner(StubBoardRunner):
    """The stub rows, recording each bound query's parameters."""

    def __init__(self):
        self.bound: list[list] = []
        self.gates: dict = {}  # first parameter -> gate
        self.entered = asyncio.Event()
        self.fail_options = 0

    async def run(self, sql):
        if "bad_options" in sql and self.fail_options:
            self.fail_options -= 1
            return SqlFail(sqlstate="42P01", message="relation does not exist")
        if "bad_options" in sql:
            return SqlOk(["value", "label"], ["text", "text"], [{"value": "x", "label": "X"}], False, 1)
        return await super().run(sql)

    async def run_bound(self, sql, params, *, timeout_ms=None):
        self.bound.append(list(params))
        gate = self.gates.get(params[0]) if params else None
        if gate is not None:
            self.entered.set()
            await gate.wait()
        return await super().run_bound(sql, params, timeout_ms=timeout_ms)


def make(entity=STUB_ENTITY, board_prefetch_rows=1, explorer=STUB_BOARD, **limits):
    answers = FirstResultAnswers()
    runner = Runner()
    renderer = BoardRenderer({"explorer": explorer, "buyer": entity}, runner, concurrency=2, row_cap=500,
                             ttl_s=300, max_cache=20)
    conversations = Conversations(**limits)
    drills = stub_drills()
    app = build_app(answers, url=URL, client_origin=ORIGIN, conversations=conversations, drills=drills,
                    boards=BoardViews(renderer, _pipeline(), board_prefetch_rows), model="muse")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL)
    return client, answers, runner, conversations, drills.prefetcher, renderer


def visual(events, kind, root=None) -> tuple[str, dict]:
    comp = next(c for c in components(events) if c["component"] == kind and "action" in c
                and (root is None or c["id"].split("__")[0] == root))
    return meta(events)[LEVEL_KEY]["surfaceId"], comp["action"]


async def explorer(client):
    events = await send_action(client, open_board(), None)
    return events, events[-1]["contextId"]


async def click_buyer(client, events, ctx, row=0):
    surface, action = visual(events, "VegaChart", "tiles")
    return await send_action(client, act(surface, source=action["source"], row=row), ctx)


# --- B9: opening ---------------------------------------------------------------------------------


async def test_a_treemap_tile_opens_the_buyer_board_with_no_model_in_a_new_tab():
    client, answers, runner, conversations, *_ = make(board_prefetch_rows=0)
    async with client:
        board, ctx = await explorer(client)
        surface, action = visual(board, "VegaChart", "tiles")
        assert action["boards"] == action["rows"]  # every tile has a buyer
        out = await click_buyer(client, board, ctx)
    m = meta(out)
    assert not m.get(ACTION_FAILED_KEY) and answers.texts == [] and m[MODEL_KEY] is None
    assert (m[LEVEL_KEY]["turn"], m[LEVEL_KEY]["level"], m[LEVEL_KEY]["label"]) == (0, 2, "Buyer A")
    assert m[TAB_KEY] == {"id": "t2", "kind": "board", "title": "Buyer · Buyer A"}
    assert m[LOG_KEY] == {"kind": "board", "tag": "NEW TAB · NO MODEL", "text": "Opened Buyer · Buyer A"}
    assert m[BOARD_KEY] == {"id": "b2", "controls": []}
    comps = components(out)
    assert next(c for c in comps if c["id"] == "board_title")["text"] == "Buyer · Buyer A"
    assert any(c["component"] == "VegaChart" and "layer" in c["spec"] for c in comps)  # the Timeline
    assert ["Buyer A"] in runner.bound  # the key, bound
    inst = conversations.get_or_create(ctx).boards["b2"]
    assert (inst.key, inst.title) == ("Buyer A", "Buyer · Buyer A")


async def test_drill_down_rows_with_a_buyer_open_the_board():
    client, *_ = make(board_prefetch_rows=0)
    async with client:
        board, ctx = await explorer(client)
        surface, action = visual(board, "DataGrid")
        assert "boards" not in action  # the suppliers table drills, as before
        drill = await send_action(client, act(surface, source=action["source"], row=0), ctx)
        dsurface, daction = visual(drill, "DataGrid")
        assert daction["boards"] == daction["rows"]
        out = await send_action(client, act(dsurface, source=daction["source"], row=daction["rows"][0]), ctx)
    assert meta(out)[TAB_KEY]["kind"] == "board"
    assert meta(out)[LEVEL_KEY]["level"] == 3


async def test_open_board_rejects_a_keyed_board():
    client, *_ = make()
    async with client:
        out = await send_action(client, open_board("buyer"), None)
    assert meta(out)[ACTION_FAILED_KEY] is True
    assert KEYED_BOARD in json.dumps(out)


async def test_a_click_while_a_model_run_holds_the_conversation_is_busy():
    client, answers, runner, *_ = make(board_prefetch_rows=0)
    async with client:
        board, ctx = await explorer(client)
        answers.gate = asyncio.Event()
        from tests.test_server import stream

        question = asyncio.create_task(stream(client, context_id=ctx))
        await answers.entered.wait()
        out = await click_buyer(client, board, ctx)
        answers.gate.set()
        await question
    assert meta(out)[ACTION_FAILED_KEY] is True and BUSY_ERROR in json.dumps(out)


async def test_a_board_that_fails_to_build_takes_back_its_level(monkeypatch):
    import explorer_agent.executor as executor

    real = executor.build_board
    calls = 0

    def flaky(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise UiError(["boom"])
        return real(*args, **kwargs)

    client, *_ = make(board_prefetch_rows=0)
    async with client:
        board, ctx = await explorer(client)
        monkeypatch.setattr(executor, "build_board", flaky)
        failed = await click_buyer(client, board, ctx)
        ok = await click_buyer(client, board, ctx)
    assert meta(failed)[ACTION_FAILED_KEY] is True and ENTITY_FAILED in json.dumps(failed)
    assert TAB_KEY not in meta(failed)
    assert meta(ok)[LEVEL_KEY]["level"] == 2 and meta(ok)[TAB_KEY]["id"] == "t2"


# --- prefetch -------------------------------------------------------------------------------------------


async def test_the_first_tiles_board_is_prefetched_and_its_click_runs_no_query():
    client, answers, runner, conversations, prefetcher, renderer = make()
    async with client:
        board, ctx = await explorer(client)
        _, action = visual(board, "VegaChart", "tiles")
        await settle()
        for _ in range(50):
            if (action["source"], action["rows"][0]) in prefetcher.held(ctx):
                break
            await asyncio.sleep(0.01)
        assert (action["source"], action["rows"][0]) in prefetcher.held(ctx)
        assert (action["source"], action["rows"][1]) not in prefetcher.held(ctx)  # BOARD_PREFETCH_ROWS = 1
        runner.bound.clear()
        out = await click_buyer(client, board, ctx, row=action["rows"][0])
    assert not meta(out).get(ACTION_FAILED_KEY)
    assert runner.bound == []  # every query came from the prefetched render


async def test_board_prefetch_zero_turns_it_off():
    client, _, runner, _, prefetcher, _ = make(board_prefetch_rows=0)
    async with client:
        board, ctx = await explorer(client)
        _, action = visual(board, "VegaChart", "tiles")
        await settle()
    assert (action["source"], action["rows"][0]) not in prefetcher.held(ctx)


# --- B8: the Board pair ---------------------------------------------------------------------------------


async def test_a_viewed_entity_board_sends_its_pair_at_its_defaults():
    from tests.test_controls_executor import ask

    client, answers, *_ = make(board_prefetch_rows=0)
    async with client:
        board, ctx = await explorer(client)
        out = await click_buyer(client, board, ctx)
        await ask(client, ctx, viewing=meta(out)[TAB_KEY]["id"])
    pair = answers.pairs[0]
    call = json.loads(pair[-2]["tool_calls"][0]["function"]["arguments"])
    body = json.loads(pair[-1]["content"])
    assert call["purpose"] == "Board shown in Tab t2"
    assert body["board"] == "Buyer · Buyer A" and body["controls"] == {}


# --- Controls on an Entity Board keep the key bound -----------------------------------------------------


BIG = Control(name="big", kind="toggle", label="Large only", type="bool", default=False)
_SQL = "SELECT count(*) AS contracts, sum(value) AS value FROM fixture.contracts WHERE buyer = :buyer AND (NOT :big OR value > 1e8)"


async def test_a_control_change_on_an_entity_board_rebinds_its_key():
    from explorer_agent.boards import BoardQuery

    summary = BoardQuery("summary", _SQL, rewrite(_SQL, {"buyer": "text", "big": "bool"}).sql, ("buyer", "big"))
    entity = dataclasses.replace(STUB_ENTITY, queries=(summary, *STUB_ENTITY.queries[1:]), controls=(BIG,))

    class KeyedRunner(Runner):
        async def run_bound(self, sql, params, *, timeout_ms=None):
            if "NOT $2" in sql or "NOT $2::boolean" in sql:
                self.bound.append(list(params))
                return SqlOk(["contracts", "value"], ["bigint", "numeric"], [{"contracts": 1, "value": 1.0}], False, 1)
            return await super().run_bound(sql, params, timeout_ms=timeout_ms)

    client, _, _, conversations, _, renderer = make(entity=entity, board_prefetch_rows=0)
    renderer._runner = runner = KeyedRunner()
    async with client:
        board, ctx = await explorer(client)
        out = await click_buyer(client, board, ctx)
        surface = meta(out)[LEVEL_KEY]["surfaceId"]
        runner.bound.clear()
        change = await send_action(client, {**controls(surface, 1, True, board="b2"),
                                            "context": {"board": "b2", "seq": 1, "values": {"big": True}}}, ctx)
    assert not meta(change).get(ACTION_FAILED_KEY), json.dumps(change)
    assert runner.bound == [["Buyer A", True]]
    assert next(c for c in updates(change)[0]["updateComponents"]["components"] if c["id"] == "total")


# --- B21: MAX_BOARD_INSTANCES -----------------------------------------------------------------------------


async def test_the_oldest_entity_board_is_dropped_never_the_explorer_and_then_answers_too_old():
    client, _, _, conversations, *_ = make(board_prefetch_rows=0, max_board_instances=2)
    async with client:
        board, ctx = await explorer(client)
        first = await click_buyer(client, board, ctx, row=0)
        second = await click_buyer(client, board, ctx, row=1)
        conv = conversations.get_or_create(ctx)
        assert list(conv.boards) == ["b1", "b3"] and conv.dropped_boards == {"b2"}
        assert meta(second)[BOARD_KEY]["id"] == "b3"
        surface, action = visual(first, "DataGrid")
        click = await send_action(client, act(surface, "ask", source=action["source"], row=0), ctx)
        change = await send_action(client, {**controls(surface, 1, None, board="b2"),
                                            "context": {"board": "b2", "seq": 1, "values": {}}}, ctx)
    assert TOO_OLD in json.dumps(click) and meta(click)[ACTION_FAILED_KEY] is True
    assert TOO_OLD in json.dumps(change) and meta(change)[ACTION_FAILED_KEY] is True



# --- refuter round 1: prefetch `take`, no Drill prefetch beside Board rows, option failures -------------


async def test_a_click_during_a_running_board_prefetch_waits_for_it_and_runs_nothing_twice():
    client, answers, runner, conversations, prefetcher, renderer = make()
    runner.gates["Buyer A"] = gate = asyncio.Event()
    async with client:
        board, ctx = await explorer(client)
        _, action = visual(board, "VegaChart", "tiles")
        await asyncio.wait_for(runner.entered.wait(), 1)  # the prefetch's first query is running
        click = asyncio.create_task(click_buyer(client, board, ctx, row=action["rows"][0]))
        await settle()
        gate.set()
        out = await click
    assert not meta(out).get(ACTION_FAILED_KEY)
    assert runner.bound.count(["Buyer A"]) == len(STUB_ENTITY.queries)  # each query once: the click joined it
    assert (action["source"], action["rows"][0]) not in prefetcher.held(ctx)  # consumed by the click


async def test_a_board_visual_with_board_rows_never_prefetches_its_drill_query():
    from explorer_agent.testing.stub_server import STUB_BOARD_DRILL

    # `:buyer` is the Explorer's Control, so the row placeholder is `:value`.
    drill = STUB_BOARD_DRILL.replace(":supplier", ":value").replace("supplier IS", "value IS")
    layout = STUB_BOARD.layout.replace(
        'tiles = Treemap("by_buyer", "Value by buyer", "buyer", "value", "currency_compact")',
        f'tiles = DataTable("by_buyer", "Value by buyer", drill_sql="{drill}")')
    assert layout != STUB_BOARD.layout
    client, _, _, _, prefetcher, _ = make(board_prefetch_rows=0, explorer=dataclasses.replace(STUB_BOARD, layout=layout))
    async with client:
        board, ctx = await explorer(client)
        _, action = next((meta(board)[LEVEL_KEY]["surfaceId"], c["action"]) for c in components(board)
                         if c["component"] == "DataGrid" and c["id"].split("__")[0] == "tiles")
        assert action["drillable"] is True and action["boards"] == action["rows"]
        await settle()
        for _ in range(20):
            await asyncio.sleep(0.01)
    assert [k for k in prefetcher.held(ctx) if k[0] == action["source"]] == []


async def test_a_failed_option_query_fails_the_click_and_takes_no_level():
    pick = Control(name="pick", kind="choice", label="Pick", type="text", default="all",
                   options_sql="SELECT 'x' AS value, 'X' AS label FROM bad_options", all_label="All")
    client, _, runner, *_ = make(entity=dataclasses.replace(STUB_ENTITY, controls=(pick,)), board_prefetch_rows=0)
    runner.fail_options = 1
    async with client:
        board, ctx = await explorer(client)
        failed = await click_buyer(client, board, ctx)
        ok = await click_buyer(client, board, ctx)
    assert meta(failed)[ACTION_FAILED_KEY] is True and ENTITY_FAILED in json.dumps(failed)
    assert TAB_KEY not in meta(failed)
    assert meta(ok)[LEVEL_KEY]["level"] == 2 and meta(ok)[TAB_KEY]["id"] == "t2"
