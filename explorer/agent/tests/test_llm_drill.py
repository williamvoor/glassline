"""Drill queries and synthetic tool results in the tool loop (SPEC §15: A4, A5, A11)."""

import json
import logging

from explorer_agent.actions import drill_down_pair
from explorer_agent.executor import Conversation
from explorer_agent.llm import DRILLS_DROPPED_LOG, NO_VIEW_PROSE, LlmLoop, LoopConfig, compact_history
from explorer_agent.sql import SqlFail, SqlOk
from explorer_agent.testing.stub_model import ScriptedCompletion, text, tool_calls
from explorer_agent.ui import UiPipeline
from tests.test_llm import Statuses

ROWS_SQL = "SELECT name, value FROM s.t"
GOOD = "SELECT k, n FROM s.d WHERE name IS NOT DISTINCT FROM :name"
BAD_COLUMN = "SELECT nope FROM s.d WHERE name IS NOT DISTINCT FROM :name"


def reply(drill_sql: str, extra: str = "") -> str:
    return f"""Answer.
<a2ui>
surface("main")
root = Column([chart{extra and ', t'}])
chart = BarChart("r1", "name", "value", "T", drill_sql="{drill_sql}")
{extra}
</a2ui>"""


class Runner:
    """`run` returns two rows; `run_bound` (check step 2) fails for SQL containing `nope`."""

    def __init__(self):
        self.bound: list[str] = []
        self.params: list[list] = []

    async def run(self, sql):
        return SqlOk(columns=["name", "value"], column_types=["text", "numeric"],
                     rows=[{"name": "A", "value": 1.0}, {"name": "B", "value": 2.0}], truncated=False, elapsed_ms=1)

    async def run_bound(self, sql, params, *, timeout_ms=None):
        self.bound.append(sql)
        assert sql.endswith("LIMIT 0") and timeout_ms == 2000
        self.params.append(list(params))
        if "nope" in sql:
            return SqlFail(sqlstate="42703", message='column "nope" does not exist')
        return SqlOk(columns=["k", "n"], column_types=["text", "bigint"], rows=[], truncated=False, elapsed_ms=1)


def loop(model, runner, **config):
    pipeline = UiPipeline(allowed_schemas=("s",))
    return LlmLoop(LoopConfig(**config), "SYSTEM", pipeline, runner, None, completion=model)


def conv():
    c = Conversation("ctx")
    c.surface_id = "turn-1-1-abc"
    return c


QUERY = tool_calls(("run_sql", {"sql": ROWS_SQL, "purpose": "rows"}))


# --- A4 ------------------------------------------------------------------------------------------


async def test_step2_failure_is_a_repair_and_the_fixed_reply_is_shown():
    model = ScriptedCompletion([QUERY, text(reply(BAD_COLUMN)), text(reply(GOOD))])
    runner = Runner()
    c = conv()
    ui = await loop(model, runner)(c, "q", Statuses())
    assert len(runner.bound) == 2
    assert runner.params == [["B"], ["B"]]  # the first clickable row in displayed order (the larger bar, row 1)
    repair = model.calls[2]["messages"][-1]["content"]
    assert "42703" in repair and "drill_sql" in repair and "run_sql is still unavailable" in repair
    # The failed attempt reserved v1 and registered nothing; the shown reply uses v2.
    assert [s.id for s in ui.sources] == ["v2"] and [d.source for d in ui.drills] == ["v2"]
    assert c.actions.sources == {}  # registration is the executor's, after both checks


async def test_step1_failure_is_a_repair_with_the_drill_note():
    model = ScriptedCompletion([QUERY, text(reply("SELECT 1 FROM s.d")), text(reply(GOOD))])
    ui = await loop(model, Runner())(conv(), "q", Statuses())
    repair = model.calls[2]["messages"][-1]["content"]
    assert "drill: drill_sql needs at least one :column" in repair and "remove it" in repair
    assert len(ui.drills) == 1


# --- A5 ------------------------------------------------------------------------------------------


async def test_repairs_exhausted_with_only_drill_errors_shows_the_answer_without_them(caplog):
    caplog.set_level(logging.WARNING)
    model = ScriptedCompletion([QUERY, text(reply(BAD_COLUMN)), text(reply(BAD_COLUMN))])
    ui = await loop(model, Runner(), max_ui_retries=1)(conv(), "q", Statuses())
    assert ui.prose == "Answer."
    assert ui.drills == []
    (src,) = ui.sources  # still clickable, its rows are Asks
    vega = next(c for m in ui.messages for c in (m.get("updateComponents") or {}).get("components", [])
                if c["component"] == "VegaChart")
    assert vega["action"] == {"source": src.id, "rows": [1, 0], "drillable": False}
    assert any(r.getMessage().startswith(DRILLS_DROPPED_LOG + "0") for r in caplog.records)


async def test_repairs_exhausted_with_another_error_gives_no_view():
    broken = reply(BAD_COLUMN, extra='t = Text("{r9[0].x}")')
    model = ScriptedCompletion([QUERY, text(broken), text(broken)])
    ui = await loop(model, Runner(), max_ui_retries=1)(conv(), "q", Statuses())
    assert NO_VIEW_PROSE in ui.prose and ui.messages == []


async def test_dropping_follows_visual_call_order():
    two = """A.
<a2ui>
surface("main")
root = Column([a, b])
a = BarChart("r1", "name", "value", "T", drill_sql="SELECT k, n FROM s.d WHERE name IS NOT DISTINCT FROM :name")
b = DataTable("r1", "U", drill_sql="SELECT nope FROM s.d WHERE name IS NOT DISTINCT FROM :name")
</a2ui>"""
    model = ScriptedCompletion([QUERY, text(two), text(two)])
    ui = await loop(model, Runner(), max_ui_retries=1)(conv(), "q", Statuses())
    assert [d.visual for d in ui.drills] == [0]  # the table (Visual 1) lost its Drill query
    assert len(ui.sources) == 2


# --- A11 ------------------------------------------------------------------------------------------


async def test_pending_pairs_follow_the_user_text_after_the_run_boundary():
    model = ScriptedCompletion([text("Fine."), text("Again.")])
    c = conv()
    result = c.store.add(sql="SELECT 1", purpose="p", columns=["a"], column_types=["text"],
                         rows=[{"a": "x" * 600}], truncated=False)
    level = c.actions.new_turn()
    c.actions.queue_pair(drill_down_pair(c.actions.new_level(1, "L"), result, "L", 20))
    c.actions.queue_pair(drill_down_pair(c.actions.new_level(1, "M"), result, "M", 20))
    await loop(model, Runner())(c, "why?", Statuses())
    sent = model.calls[0]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assert sent[1]["content"] == "why?"
    assert [m["tool_call_id"] for m in sent if m["role"] == "tool"] == ["synthetic-1-2", "synthetic-1-3"]
    assert len(json.loads(sent[3]["content"])["rows_preview"][0]["a"]) == 501  # cut at 500 + "…"
    assert c.run_starts == [0]
    # The next run summarises them like any tool result.
    await loop(model, Runner())(c, "and?", Statuses())
    later = model.calls[1]["messages"]
    tools = [m for m in later if m["role"] == "tool"]
    assert all(not t["content"].startswith("{") for t in tools)
    assert tools[0]["content"].startswith(f'{result.id} "Drill-down shown in Turn 1, Level 2"')
    assert level.level == 1


def test_compaction_boundary_is_the_model_run():
    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "run_sql", "arguments": '{"sql": "S", "purpose": "p"}'}}]},
        {"role": "tool", "tool_call_id": "a", "content": '{"result_id": "r1", "columns": ["x"], "row_count": 1}'},
    ]
    assert compact_history(history, current_run_start=0)[2]["content"].startswith("{")
    assert compact_history(history, current_run_start=3)[2]["content"].startswith('r1 "p"')
