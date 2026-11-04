"""Saved Conversations (specs/workspaces/SPEC.md phase 1, P1–P10). All model calls are stubbed."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from pathlib import Path

import httpx
import pytest

from explorer_agent.boards import BoardRenderer, entity_columns, load_boards
from explorer_agent.executor import (
    GAVE_UP,
    LEVEL_KEY,
    OPEN_BOARD,
    TAB_KEY,
    UNKNOWN_CONTEXT_PROSE,
    VIEW_KEY,
    BoardViews,
    Conversations,
    DrillDowns,
    SavedViews,
)
from explorer_agent.llm import LlmLoop, LoopConfig
from explorer_agent.macros import PANEL_UNDRAWABLE, RERUN_FAILED
from explorer_agent.saved import ROWS_OMITTED, SavedStore, Snapshot, omit_rows, result_ids_in, row_hash, title_of
from explorer_agent.schema import SchemaCatalog
from explorer_agent.server import build_app
from explorer_agent.sql import SqlRunner
from explorer_agent.testing.stub_model import ScriptedCompletion, text, tool_calls
from explorer_agent.ui import UiPipeline, UiResult
from tests.test_executor import act, meta, send_action
from tests.test_server import CAPS, a2ui_messages, final_parts, stream

URL = "http://agent.test"
ORIGIN = "http://127.0.0.1:5173"
TOP_SQL = "SELECT supplier_name, total_value FROM mart.supplier ORDER BY total_value DESC, supplier_name LIMIT 3"
DRILL = "SELECT agency_name, sum(total_value) AS total_value FROM mart.supplier_agency_fy WHERE supplier_name = :supplier_name GROUP BY 1"
REPLY = f"""**{{r1[0].supplier_name}}** leads with {{r1[0].total_value|currency_m}}.
<a2ui>
surface("main")
root = Column([chart])
chart = BarChart("r1", "supplier_name", "total_value", "Top suppliers", drill_sql="{DRILL}")
</a2ui>"""
AGENCY_SQL = "SELECT agency_name, total_value FROM mart.agency ORDER BY total_value DESC, agency_name LIMIT 3"
AGENCY_REPLY = """Agencies.
<a2ui>
surface("main")
root = Column([t])
t = DataTable("r2", "Agencies", "agency_name,total_value")
</a2ui>"""
EXPLORER_TOML = '''title = "Explorer"
layout = """root = Column([t])
t = DataTable("by_fy", "By year", "financial_year,total_value")"""

[[controls]]
name = "fy"
kind = "choice"
label = "Year"
type = "text"
options_sql = "SELECT DISTINCT financial_year AS value, financial_year AS label FROM mart.agency_fy ORDER BY 1"
all_label = "All years"

[[queries]]
name = "by_fy"
sql = "SELECT financial_year, sum(total_value) AS total_value FROM mart.agency_fy WHERE (:fy IS NULL OR financial_year = :fy) GROUP BY 1 ORDER BY 1"
'''
AGENCY_TOML = '''title = "Agency"
key = "agency_name"
key_type = "text"
key_columns = ["agency_name"]
layout = """root = Column([total])
total = Metric("totals", "total_value", "Total", 0, "currency_compact")"""

[[queries]]
name = "totals"
sql = "SELECT sum(total_value) AS total_value FROM mart.agency WHERE agency_name = :agency_name"
'''


class Agent:
    """One agent process over a Workspace's saved store: build it again for a "restart"."""

    def __init__(self, ro_pool, path: Path, steps=(), *, boards_dir: Path | None = None, max_conversations=100):
        self.runner = SqlRunner(ro_pool, ("mart",), 5000, 100)
        boards = load_boards(boards_dir, ("mart",)) if boards_dir else {}
        self.pipeline = UiPipeline(allowed_schemas=("mart",), entity=entity_columns(boards),
                                   key_types={b.name: b.key_type for b in boards.values() if b.key})
        self.model = ScriptedCompletion(list(steps))
        self.loop = LlmLoop(LoopConfig(), "SYSTEM", self.pipeline, self.runner, SchemaCatalog(ro_pool, ["mart"]),
                            completion=self.model)
        self.store = SavedStore(path)
        self.conversations = Conversations(max_conversations, saved=self.store)
        views = None
        if boards:
            renderer = BoardRenderer(boards, self.runner, concurrency=2, row_cap=500, ttl_s=300, max_cache=20)
            views = BoardViews(renderer, self.pipeline)
        self.app = build_app(self.loop, url=URL, client_origin=ORIGIN, conversations=self.conversations,
                             drills=DrillDowns(self.runner, self.pipeline), boards=views,
                             saved=SavedViews(self.store, self.runner, self.pipeline))
        self.executor = self.app.state.executor
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=URL)

    async def ask(self, question="top suppliers?", context_id=None, metadata=CAPS):
        events = await stream(self.client, question, context_id=context_id, metadata=metadata)
        await self.executor.flush()
        return events

    async def act(self, action, context_id, viewing=None):
        events = await send_action(self.client, action, context_id, viewing=viewing)
        await self.executor.flush()
        return events

    async def level(self, cid, lid, *, defaults=False):
        if defaults:
            r = await self.client.post(f"/conversations/{cid}/levels/{lid}/defaults", json={})
        else:
            r = await self.client.get(f"/conversations/{cid}/levels/{lid}")
        assert r.status_code == 200, r.text
        return r.json()

    async def close(self):
        await self.client.aclose()
        await self.store.close()


def answer_steps(sql=TOP_SQL, reply=REPLY):
    return [tool_calls(("run_sql", {"sql": sql, "purpose": "top suppliers"})), text(reply)]


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "data" / "workspaces" / "demo" / "conversations.sqlite"


@pytest.fixture
def boards_dir(tmp_path):
    d = tmp_path / "ds"
    (d / "boards").mkdir(parents=True)
    (d / "boards" / "explorer.toml").write_text(EXPLORER_TOML, encoding="utf-8")
    (d / "boards" / "agency.toml").write_text(AGENCY_TOML, encoding="utf-8")
    return d


def all_bytes(path: Path) -> bytes:
    return b"".join(p.read_bytes() for p in path.parent.iterdir() if p.is_file())


def components(a2ui: list[dict]) -> list[dict]:
    return [c for m in a2ui for c in (m.get("updateComponents") or {}).get("components", [])]


def texts(a2ui: list[dict]) -> list[str]:
    return [c.get("text") for c in components(a2ui) if c.get("component") == "Text"]


# --- pure parts ----------------------------------------------------------------------------------


def test_omit_rows_keeps_columns_counts_and_sql():
    run = {"result_id": "r1", "columns": ["a"], "row_count": 2, "rows_preview": [{"a": "SECRET"}], "sql": "SELECT 1"}
    pair = {"board": "B", "controls": {}, "results": [{"id": "r2", "columns": ["b"], "rows_preview": [{"b": "SECRET"}]}]}
    history = [{"role": "user", "content": "q"}, {"role": "tool", "tool_call_id": "c1", "content": json.dumps(run)},
               {"role": "tool", "tool_call_id": "c2", "content": json.dumps(pair)}]
    out = omit_rows(history)
    assert "SECRET" not in json.dumps(out)
    first, second = json.loads(out[1]["content"]), json.loads(out[2]["content"])
    assert first == {**run, "rows_preview": ROWS_OMITTED}
    assert second["results"][0] == {"id": "r2", "columns": ["b"], "rows_preview": ROWS_OMITTED}
    assert "SECRET" in json.dumps(history)  # the in-memory history is untouched


def test_row_hash_title_and_result_ids():
    assert row_hash([{"a": 1, "b": 2}]) == row_hash([{"b": 2, "a": 1}]) != row_hash([{"a": 1, "b": 3}])
    assert title_of("  a\nb  ") == "a b" and len(title_of("x" * 200)) == 80
    assert result_ids_in("{r1[0].a} r12 r1 rx br3") == ["r1", "r12"]


def test_the_store_file_is_owner_only(db_path):
    store = SavedStore(db_path)
    store.save_sync(Snapshot("c1", "t", "2026", "2026", 1, {"x": 1}, []))
    assert db_path.stat().st_mode & 0o777 == 0o600
    assert [c["id"] for c in store.list_sync()] == ["c1"]
    store.close_sync()


# --- P1, P2 --------------------------------------------------------------------------------------


@pytest.mark.db
async def test_p1_the_saved_file_holds_no_row_values(ro_pool, db_path):
    agent = Agent(ro_pool, db_path, answer_steps())
    try:
        events = await agent.ask()
        sentinel = agent.conversations.held(events[-1]["contextId"]).store.get("r1").rows[1]["supplier_name"]
        shown = final_parts(events)[0]["text"]
        assert agent.conversations.held(events[-1]["contextId"]).store.get("r1").rows[0]["supplier_name"] in shown
        await agent.store.close()
        data = all_bytes(db_path)
        assert sentinel.encode() not in data
        assert b"rows_omitted" in data and TOP_SQL.encode() in data
    finally:
        await agent.client.aclose()


@pytest.mark.db
async def test_p2_opening_the_explorer_saves_nothing_and_a_question_saves_with_its_title(ro_pool, db_path, boards_dir):
    agent = Agent(ro_pool, db_path, answer_steps(), boards_dir=boards_dir)
    try:
        opened = await agent.act({"name": OPEN_BOARD, "context": {"board": "explorer"}}, None)
        cid = opened[-1]["contextId"]
        assert meta(opened)[LEVEL_KEY]["turn"] == 0
        assert (await agent.client.get("/conversations")).json() == []
        await agent.ask("Who are the top suppliers this year?", cid)
        listed = (await agent.client.get("/conversations")).json()
        assert [(c["id"], c["title"], c["turns"]) for c in listed] == [(cid, "Who are the top suppliers this year?", 1)]
    finally:
        await agent.close()


# --- P3, P6, P8 ----------------------------------------------------------------------------------


@pytest.mark.db
async def test_p3_after_a_restart_a_saved_answer_rebuilds_to_the_same_a2ui(ro_pool, db_path):
    first = Agent(ro_pool, db_path, answer_steps())
    events = await first.ask()
    cid = events[-1]["contextId"]
    before = a2ui_messages(final_parts(events))
    prose = final_parts(events)[0]["text"]
    await first.close()

    second = Agent(ro_pool, db_path)
    try:
        view = (await second.client.get(f"/conversations/{cid}")).json()
        assert [t["level"] for t in view["tabs"]] == ["1-1"] and view["selected"] == view["tabs"][0]["id"]
        assert view["tabs"][0]["log"]["kind"] == "question"
        level = await second.level(cid, "1-1")
        assert level["changed"] is False and level["failed_results"] == [] and level["prose"] == prose
        # Action source IDs are issued again (v2 for v1); everything else is identical.
        assert json.dumps(level["a2ui"]).replace('"v2"', '"v1"') == json.dumps(before)
        assert second.model.calls == []  # no model work to reopen
    finally:
        await second.close()


@pytest.mark.db
async def test_p6_asking_on_a_restored_conversation_loads_it_with_rows_omitted(ro_pool, db_path):
    first = Agent(ro_pool, db_path, answer_steps())
    cid = (await first.ask())[-1]["contextId"]
    sentinel = first.conversations.held(cid).store.get("r1").rows[2]["supplier_name"]
    await first.close()

    # The follow-up reply names r1 without running SQL: it is re-run before the reply is built (B9).
    second = Agent(ro_pool, db_path, [text(REPLY)])
    try:
        events = await second.ask("and again?", cid)
        prose = final_parts(events)[0]["text"]
        assert UNKNOWN_CONTEXT_PROSE not in prose and prose.startswith("**")
        loaded = second.conversations.held(cid).history
        tool = next(m for m in loaded if m["role"] == "tool")
        assert json.loads(tool["content"])["rows_preview"] == ROWS_OMITTED
        sent = second.model.calls[0]["messages"]
        assert sentinel not in json.dumps(sent)  # G19: SQL, columns and row count, never rows
        assert "3 rows" in json.dumps(sent) and TOP_SQL in json.dumps(sent)
        assert sent[-1] == {"role": "user", "content": "and again?"}
        assert meta(events)[LEVEL_KEY]["turn"] == 2
    finally:
        await second.close()


@pytest.mark.db
async def test_p6_an_unknown_context_still_starts_a_new_conversation(ro_pool, db_path):
    agent = Agent(ro_pool, db_path, answer_steps())
    try:
        events = await agent.ask(context_id="never-seen")
        assert final_parts(events)[0]["text"].startswith(UNKNOWN_CONTEXT_PROSE)
    finally:
        await agent.close()


@pytest.mark.db
async def test_p8_an_evicted_conversation_reloads_from_the_store(ro_pool, db_path):
    agent = Agent(ro_pool, db_path, answer_steps() + answer_steps() + [text(REPLY)], max_conversations=1)
    try:
        a = (await agent.ask())[-1]["contextId"]
        b = (await agent.ask())[-1]["contextId"]
        assert agent.conversations.held(a) is None and agent.conversations.held(b) is not None
        events = await agent.ask("more?", a)
        assert UNKNOWN_CONTEXT_PROSE not in final_parts(events)[0]["text"]
        assert meta(events)[LEVEL_KEY]["turn"] == 2
    finally:
        await agent.close()


# --- P4 ------------------------------------------------------------------------------------------


def _tamper(db_path: Path, sql: str, *args) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(sql, args)
    conn.commit()
    conn.close()


@pytest.mark.db
async def test_p4_changed_rows_set_changed(ro_pool, db_path):
    first = Agent(ro_pool, db_path, answer_steps())
    cid = (await first.ask())[-1]["contextId"]
    await first.close()
    _tamper(db_path, "UPDATE results SET row_hash = 'other' WHERE result_id = 'r1'")
    second = Agent(ro_pool, db_path)
    try:
        level = await second.level(cid, "1-1")
        assert level["changed"] is True and level["failed_results"] == []
    finally:
        await second.close()


@pytest.mark.db
async def test_p4_a_query_that_no_longer_runs_shows_its_error_and_the_rest_renders(ro_pool, db_path):
    two = f"""Top is **{{r1[0].supplier_name}}**.
<a2ui>
surface("main")
root = Column([a, b])
a = BarChart("r1", "supplier_name", "total_value", "Top suppliers")
b = DataTable("r2", "Agencies", "agency_name,total_value")
</a2ui>"""
    steps = [tool_calls(("run_sql", {"sql": TOP_SQL, "purpose": "top"}), ("run_sql", {"sql": AGENCY_SQL, "purpose": "agencies"})),
             text(two)]
    first = Agent(ro_pool, db_path, steps)
    cid = (await first.ask())[-1]["contextId"]
    await first.close()
    _tamper(db_path, "UPDATE results SET sql = 'SELECT no_such_column FROM mart.supplier' WHERE result_id = 'r1'")
    second = Agent(ro_pool, db_path)
    try:
        level = await second.level(cid, "1-1")
        assert level["failed_results"] == ["r1"]
        assert level["prose"] == "Top is **—**."
        shown = texts(level["a2ui"])
        assert any(t and t.startswith(RERUN_FAILED.split("{")[0]) for t in shown)
        sql = [c for c in components(level["a2ui"]) if c.get("component") == "SqlDetails"]
        assert any("no_such_column" in c["sql"] for c in sql)
        assert any(c.get("component") == "DataGrid" for c in components(level["a2ui"]))  # r2 still renders
    finally:
        await second.close()


@pytest.mark.db
async def test_p4_data_that_no_longer_fits_a_macro_draws_a_panel_note(ro_pool, db_path):
    reply = """<a2ui>
surface("main")
root = Column([m])
m = Metric("r1", "total_value", "Third", 2)
</a2ui>"""
    first = Agent(ro_pool, db_path, answer_steps(reply=reply))
    cid = (await first.ask())[-1]["contextId"]
    await first.close()
    _tamper(db_path, "UPDATE results SET sql = ? WHERE result_id = 'r1'", TOP_SQL.replace("LIMIT 3", "LIMIT 1"))
    second = Agent(ro_pool, db_path)
    try:
        level = await second.level(cid, "1-1")
        assert PANEL_UNDRAWABLE in texts(level["a2ui"]) and level["changed"] is True
    finally:
        await second.close()


@pytest.mark.db
async def test_a_give_up_reopens_as_a_note(ro_pool, db_path):
    first = Agent(ro_pool, db_path, [text("<a2ui>broken"), text("<a2ui>broken"), text("<a2ui>broken")])
    cid = (await first.ask())[-1]["contextId"]
    await first.close()
    second = Agent(ro_pool, db_path)
    try:
        assert (await second.level(cid, "1-1"))["prose"] == GAVE_UP
    finally:
        await second.close()


# --- P5, P7 --------------------------------------------------------------------------------------


async def _open_and_ask(agent: Agent) -> tuple[str, dict]:
    opened = await agent.act({"name": OPEN_BOARD, "context": {"board": "explorer"}}, None)
    cid = opened[-1]["contextId"]
    return cid, opened


@pytest.mark.db
async def test_p5_a_board_level_rerenders_with_its_saved_control_values(ro_pool, db_path, boards_dir):
    first = Agent(ro_pool, db_path, boards_dir=boards_dir)
    cid, opened = await _open_and_ask(first)
    m = meta(opened)
    surface = m[LEVEL_KEY]["surfaceId"]
    board_id = m["explorerBoard"]["id"]
    changed = await first.act(act(surface, "controls", board=board_id, seq=1, values={"fy": 1}), cid)
    assert "explorerInPlace" in meta(changed)
    await first.close()

    second = Agent(ro_pool, db_path, boards_dir=boards_dir)
    try:
        view = (await second.client.get(f"/conversations/{cid}")).json()
        assert view["controls"] == {"0-1": {"fy": 1}}
        assert [e.get("kind") for e in view["log"]] == ["board", "controls"]
        assert view["log"][1]["on_tab"] == view["tabs"][0]["id"] and "tab" not in view["log"][1]
        level = await second.level(cid, "0-1")
        assert level["board_changed"] is False and level["board"]["controls"] == [{"name": "fy", "kind": "choice"}]
        rows = [c for c in components(level["a2ui"]) if c.get("component") == "DataGrid"]
        assert len(rows) == 1
        data = [m for m in level["a2ui"] if "updateDataModel" in m]
        assert data[0]["updateDataModel"]["value"] == {"fy": ["1"]}  # the Control shows option 1, not All
    finally:
        await second.close()


@pytest.mark.db
async def test_p5_a_changed_or_removed_board_file_gives_board_changed(ro_pool, db_path, boards_dir):
    first = Agent(ro_pool, db_path, boards_dir=boards_dir)
    cid, opened = await _open_and_ask(first)
    m = meta(opened)
    await first.act(act(m[LEVEL_KEY]["surfaceId"], "controls", board=m["explorerBoard"]["id"], seq=1,
                        values={"fy": 1}), cid)
    await first.close()
    path = boards_dir / "boards" / "explorer.toml"
    path.write_text(EXPLORER_TOML.replace('title = "Explorer"', 'title = "Explorer again"'), encoding="utf-8")
    second = Agent(ro_pool, db_path, boards_dir=boards_dir)
    try:
        level = await second.level(cid, "0-1")
        assert level["board_changed"] is True and level["a2ui"] == []
        fresh = await second.level(cid, "0-1", defaults=True)  # the notice's button (B11)
        assert fresh["board_changed"] is False and fresh["a2ui"]
        await second.executor.flush()
        assert (await second.client.get(f"/conversations/{cid}")).json()["controls"] == {"0-1": {"fy": None}}
    finally:
        await second.close()
    third = Agent(ro_pool, db_path, boards_dir=boards_dir)
    try:
        assert (await third.level(cid, "0-1"))["board_changed"] is False  # the new hash was saved
    finally:
        await third.close()


@pytest.mark.db
async def test_p7_clicks_on_a_rebuilt_level_work(ro_pool, db_path, boards_dir):
    steps = answer_steps() + [tool_calls(("run_sql", {"sql": AGENCY_SQL, "purpose": "agencies"})), text(AGENCY_REPLY)]
    first = Agent(ro_pool, db_path, steps, boards_dir=boards_dir)
    cid = (await first.ask())[-1]["contextId"]
    await first.ask("agencies?", cid)
    await first.close()

    second = Agent(ro_pool, db_path, boards_dir=boards_dir)
    try:
        bars = await second.level(cid, "1-1")
        source = _source(bars, "VegaChart")
        drilled = await second.act(act(_surface(bars), "drill", source=source, row=0), cid)
        assert meta(drilled)[TAB_KEY]["kind"] == "drill", meta(drilled)

        table = await second.level(cid, "2-1")
        source = _source(table, "DataGrid")
        entity = await second.act(act(_surface(table), "drill", source=source, row=0), cid)
        assert meta(entity)[TAB_KEY]["kind"] == "board" and meta(entity)["explorerBoard"]["id"]
        await second.executor.flush()
    finally:
        await second.close()
    third = Agent(ro_pool, db_path, boards_dir=boards_dir)
    try:
        view = (await third.client.get(f"/conversations/{cid}")).json()
        kinds = [t["kind"] for t in view["tabs"]]
        assert kinds == ["question", "question", "drill", "board"]
        drill = await third.level(cid, view["tabs"][2]["level"])
        assert drill["a2ui"] and drill["failed_results"] == []
        board = await third.level(cid, view["tabs"][3]["level"])
        assert board["a2ui"] and board["board_changed"] is False
    finally:
        await third.close()


def _source(level: dict, kind: str) -> str:
    comp = next(c for c in components(level["a2ui"]) if c.get("component") == kind)
    return re.search(r'"source": "(v\d+)"', json.dumps(comp)).group(1)


def _surface(level: dict) -> str:
    return next(m["createSurface"]["surfaceId"] for m in level["a2ui"] if "createSurface" in m)


# --- P9, P10 -------------------------------------------------------------------------------------


@pytest.mark.db
async def test_p9_delete_one_delete_all_and_409_while_running(ro_pool, db_path):
    gate = asyncio.Event()

    async def slow(kwargs):
        await gate.wait()
        return text("Done.")

    agent = Agent(ro_pool, db_path, answer_steps() + answer_steps() + [slow])
    try:
        a = (await agent.ask())[-1]["contextId"]
        b = (await agent.ask())[-1]["contextId"]
        assert len((await agent.client.get("/conversations")).json()) == 2
        assert (await agent.client.delete(f"/conversations/{a}")).status_code == 204
        assert [c["id"] for c in (await agent.client.get("/conversations")).json()] == [b]
        assert (await agent.client.delete(f"/conversations/{a}")).status_code == 404
        running = asyncio.create_task(agent.ask("slow?", b))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if agent.conversations.held(b).busy:
                break
        assert (await agent.client.delete(f"/conversations/{b}")).status_code == 409
        assert (await agent.client.delete("/conversations")).status_code == 409
        gate.set()
        await running
        assert (await agent.client.delete("/conversations")).status_code == 204
        assert (await agent.client.get("/conversations")).json() == []
    finally:
        await agent.close()


async def test_p10_a_slow_store_does_not_delay_the_final_event(tmp_path):
    """The write is scheduled after `updater.complete`: the stream ends while it still waits."""
    release = asyncio.Event()

    class SlowStore(SavedStore):
        async def save(self, snap):
            await release.wait()
            await super().save(snap)

    async def answer(conv, text_, status):
        return UiResult(prose="ok", messages=[], bare_numbers=[])

    store = SlowStore(tmp_path / "w" / "conversations.sqlite")
    app = build_app(answer, url=URL, client_origin=ORIGIN, saved=SavedViews(store, None, UiPipeline()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL) as c:
        events = await asyncio.wait_for(stream(c, "q?"), 10)
        assert events[-1]["final"] is True
        assert store.list_sync() == []  # not written yet
        release.set()
        await app.state.executor.flush()
        assert [x["title"] for x in store.list_sync()] == ["q?"]
    await store.close()


async def test_the_view_metadata_saves_closed_and_selected_tabs(tmp_path):
    async def answer(conv, text_, status):
        return UiResult(prose="ok", messages=[], bare_numbers=[])

    store = SavedStore(tmp_path / "w" / "conversations.sqlite")
    app = build_app(answer, url=URL, client_origin=ORIGIN, saved=SavedViews(store, None, UiPipeline()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL) as c:
        cid = (await stream(c, "one?"))[-1]["contextId"]
        await stream(c, "two?", context_id=cid, metadata={**CAPS, VIEW_KEY: {"closed": ["t1", "t99", 3], "selected": "t1"}})
        await app.state.executor.flush()
        view = (await c.get(f"/conversations/{cid}")).json()
    assert [(t["id"], t["closed"]) for t in view["tabs"]] == [("t1", True), ("t2", False)]
    assert view["selected"] == "t1" and view["title"] == "one?"
    await store.close()


# --- refuter regressions -------------------------------------------------------------------------


@pytest.mark.db
async def test_p1_sql_error_messages_never_reach_the_file(ro_pool, db_path):
    """Refuter F1: Postgres quotes row values in errors ("invalid input syntax ... "<name>"")."""
    bad = "SELECT supplier_name::int AS n FROM mart.supplier ORDER BY total_value DESC LIMIT 1"
    agent = Agent(ro_pool, db_path, [tool_calls(("run_sql", {"sql": bad, "purpose": "cast"}))] + answer_steps())
    try:
        events = await agent.ask()
        conv = agent.conversations.held(events[-1]["contextId"])
        error = next(json.loads(m["content"])["error"] for m in conv.history
                     if m["role"] == "tool" and "error" in json.loads(m["content"]))
        sentinel = re.search(r'"(.+)"', error["message"]).group(1)  # the model saw it as Postgres wrote it
        assert sentinel
        await agent.store.close()
        assert sentinel.encode() not in all_bytes(db_path)
        assert b"22P02" in all_bytes(db_path)  # the sqlstate is kept
    finally:
        await agent.client.aclose()


@pytest.mark.db
async def test_p5_a_removed_board_file_gives_board_changed(ro_pool, db_path, boards_dir):
    """Refuter F4."""
    steps = [tool_calls(("run_sql", {"sql": AGENCY_SQL, "purpose": "agencies"})),
             text(AGENCY_REPLY.replace('"r2"', '"r1"'))]
    first = Agent(ro_pool, db_path, steps, boards_dir=boards_dir)
    cid = (await first.ask("agencies?"))[-1]["contextId"]
    table = await first.level(cid, "1-1")
    await first.act(act(_surface(table), "drill", source=_source(table, "DataGrid"), row=0), cid)
    await first.close()
    (boards_dir / "boards" / "agency.toml").unlink()
    second = Agent(ro_pool, db_path, boards_dir=boards_dir)
    try:
        view = (await second.client.get(f"/conversations/{cid}")).json()
        board = next(t for t in view["tabs"] if t["kind"] == "board")
        assert (await second.level(cid, board["level"]))["board_changed"] is True
    finally:
        await second.close()


@pytest.mark.db
async def test_p5_a_choice_whose_option_moved_gives_board_changed(ro_pool, db_path, boards_dir):
    """Refuter F5: a choice is saved as an option index; if the options move, that index names
    another value, so the saved Tab must not silently show it."""
    first = Agent(ro_pool, db_path, boards_dir=boards_dir)
    cid, opened = await _open_and_ask(first)
    m = meta(opened)
    await first.act(act(m[LEVEL_KEY]["surfaceId"], "controls", board=m["explorerBoard"]["id"], seq=1,
                        values={"fy": 0}), cid)
    await first.close()
    # The options are now newest first: index 0 names another year. The file hash is unchanged.
    conn = sqlite3.connect(db_path)
    state = json.loads(conn.execute("SELECT state FROM conversations").fetchone()[0])
    assert state["records"]["0-1"]["shown"] == {"fy": [state["records"]["0-1"]["shown"]["fy"][0]]}
    state["records"]["0-1"]["shown"]["fy"] = ["not-a-year"]
    conn.execute("UPDATE conversations SET state = ?", (json.dumps(state),))
    conn.commit()
    conn.close()
    second = Agent(ro_pool, db_path, boards_dir=boards_dir)
    try:
        assert (await second.level(cid, "0-1"))["board_changed"] is True
    finally:
        await second.close()


async def test_a_failed_write_is_retried_with_its_results(tmp_path):
    """Refuter F8: Results of a failed write go with the next one."""
    from explorer_agent.results import StoredResult

    fail = {"on": True}

    class Flaky(SavedStore):
        def save_sync(self, snap):
            if fail["on"]:
                raise OSError("disk full")
            super().save_sync(snap)

    async def answer(conv, text_, status):
        r = conv.store.add(sql="SELECT 1", purpose="p", columns=["a"], column_types=["int4"], rows=[{"a": 1}], truncated=False)
        assert isinstance(r, StoredResult)
        return UiResult(prose="ok", messages=[], bare_numbers=[])

    store = Flaky(tmp_path / "w" / "conversations.sqlite")
    app = build_app(answer, url=URL, client_origin=ORIGIN, saved=SavedViews(store, None, UiPipeline()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL) as c:
        cid = (await stream(c, "one?"))[-1]["contextId"]
        await app.state.executor.flush()
        fail["on"] = False
        await stream(c, "two?", context_id=cid)
        await app.state.executor.flush()
    loaded = store.load_sync(cid)
    assert sorted(loaded.results) == ["r1", "r2"]
    await store.close()


async def test_a_saved_state_that_cannot_be_read_starts_a_new_conversation(tmp_path):
    """Refuter suspicion: a corrupt or older saved state must not fail every request."""
    store = SavedStore(tmp_path / "w" / "conversations.sqlite")
    store.save_sync(Snapshot("c1", "t", "2026", "2026", 1, {"version": 99}, []))

    async def answer(conv, text_, status):
        return UiResult(prose="ok", messages=[], bare_numbers=[])

    app = build_app(answer, url=URL, client_origin=ORIGIN, saved=SavedViews(store, None, UiPipeline()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL) as c:
        events = await stream(c, "q?", context_id="c1")
        assert final_parts(events)[0]["text"].startswith(UNKNOWN_CONTEXT_PROSE)
        await app.state.executor.flush()
        assert (await c.get("/conversations/c1")).json()["title"] == "q?"  # the unreadable one was replaced
    await store.close()


async def test_single_mode_keeps_no_saving_state():
    """Refuter F7: without a store nothing is recorded (W1)."""
    async def answer(conv, text_, status):
        return UiResult(prose="ok", messages=[], bare_numbers=[], reply="<a2ui></a2ui> r1")

    app = build_app(answer, url=URL, client_origin=ORIGIN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL) as c:
        cid = (await stream(c, "q?"))[-1]["contextId"]
    conv = app.state.conversations.held(cid)
    assert conv.records == {} and conv.log == [] and conv.unsaved == []
