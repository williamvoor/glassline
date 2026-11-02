import asyncio
import json
import logging

import litellm
import pytest

from explorer_agent.catalog import EXPLORER_CATALOG_ID
from explorer_agent.executor import Conversation
from explorer_agent.llm import (
    CONTENT_POLICY_PROSE,
    MODEL_FAILED_PROSE,
    LOGGED_ERRORS_MAX,
    NO_REPAIR_LOG,
    NO_VIEW_PROSE,
    REPAIR_LOG,
    REPAIR_STATUS,
    STEP_LIMIT_PROSE,
    TIMEOUT_PROSE,
    TOOLS_DURING_REPAIR,
    LlmLoop,
    UNKNOWN_CONTEXT_NOTE,
    LoopConfig,
)
from explorer_agent.schema import SchemaCatalog
from explorer_agent.sql import SqlRunner
from explorer_agent.testing.stub_model import ScriptedCompletion, text, tool_calls
from explorer_agent.tools import TOOL_SCHEMAS
from explorer_agent.ui import UiPipeline

SYSTEM = "SYSTEM PROMPT"
TOP_SQL = "SELECT supplier_name, total_value FROM mart.supplier_fy WHERE financial_year = '2024-25' ORDER BY 2 DESC LIMIT 3"
TOP_REPLY = """**{r1[0].supplier_name}** leads with {r1[0].total_value|currency_m}.
<a2ui>
surface("main")
root = Column([chart])
chart = BarChart("r1", "supplier_name", "total_value", "Top suppliers", _, false, "currency_m")
</a2ui>"""


class Statuses(list):
    async def __call__(self, text: str) -> None:
        self.append(text)


def make_loop(runner, catalog, stub, *, fmt="express", **config):
    return LlmLoop(LoopConfig(**config), SYSTEM, UiPipeline(fmt), runner, catalog, completion=stub)


def new_conv(n: int = 1) -> Conversation:
    conv = Conversation("ctx")
    conv.turns = n
    conv.surface_id = f"turn-{n}"
    return conv


@pytest.fixture
def runner(ro_pool):
    return SqlRunner(ro_pool, ("mart",), 5000, 100)


@pytest.fixture
def catalog(ro_pool):
    return SchemaCatalog(ro_pool, ["mart"])


class FakeRunner:
    """Records concurrency; returns a tiny result after a delay."""

    def __init__(self, delay=0.1, delays=None):
        self.delay, self.delays, self.active, self.max_active, self.sql = delay, delays or {}, 0, 0, []

    async def run(self, sql):
        from explorer_agent.sql import SqlOk

        self.sql.append(sql)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(self.delays.get(sql, self.delay))
        self.active -= 1
        return SqlOk(columns=["sql"], column_types=["text"], rows=[{"sql": sql}], truncated=False, elapsed_ms=1)


def surface_ids(messages):
    return {b["surfaceId"] for m in messages for b in m.values() if isinstance(b, dict) and "surfaceId" in b}


# L1
@pytest.mark.db
async def test_sql_error_is_corrected(runner, catalog):
    model = ScriptedCompletion([
        tool_calls(("run_sql", {"sql": TOP_SQL.replace("total_value", "total_valu", 1), "purpose": "top suppliers"})),
        tool_calls(("run_sql", {"sql": TOP_SQL, "purpose": "top suppliers"})),
        text(TOP_REPLY),
    ])
    status = Statuses()
    conv = new_conv()
    ui = await make_loop(runner, catalog, model)(conv, "who are the top suppliers?", status)
    assert status[0] == "Running SQL: top suppliers"
    assert status[1].startswith("SQL failed: ") and "total_valu" in status[1]
    assert status[2] == "Running SQL: top suppliers"
    assert surface_ids(ui.messages) == {"turn-1"}
    assert ui.prose.startswith("**") and "$" in ui.prose
    assert conv.store.ids() == ["r1"]  # the failed query was not stored
    assert_paired(conv.history)
    assert conv.history[-1] == {"role": "assistant", "content": TOP_REPLY}
    second_call = model.calls[1]["messages"]
    assert json.loads(second_call[-1]["content"])["error"]["sqlstate"] == "42703"


# L2
async def test_parallel_tool_calls():
    # the first call is slower, so completion order differs from the model's order
    fake = FakeRunner(delays={"SELECT 1 AS one": 0.3, "SELECT 2 AS one": 0.05})
    model = ScriptedCompletion([
        tool_calls(("run_sql", {"sql": "SELECT 1 AS one", "purpose": "a"}),
                   ("run_sql", {"sql": "SELECT 2 AS one", "purpose": "b"})),
        text("Done: {r1[0].sql} and {r2[0].sql}."),
    ])
    conv = new_conv()
    await make_loop(fake, None, model)(conv, "q", Statuses())
    assert fake.max_active == 2
    sent = model.calls[1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "tool", "tool"]
    assert [tc["id"] for tc in sent[2]["tool_calls"]] == ["call_1", "call_2"]
    assert [m["tool_call_id"] for m in sent[3:]] == ["call_1", "call_2"]
    assert [json.loads(m["content"])["rows_preview"][0]["sql"] for m in sent[3:]] == ["SELECT 1 AS one", "SELECT 2 AS one"]
    assert_paired(sent[1:])
    assert conv.history[-1] == {"role": "assistant", "content": "Done: {r1[0].sql} and {r2[0].sql}."}


# L3
async def test_iteration_cap():
    model = ScriptedCompletion([tool_calls(("list_tables", {}))], repeat_last=True)

    class Cat:
        async def objects(self):
            return []

    status = Statuses()
    ui = await make_loop(FakeRunner(0), Cat(), model, max_tool_iterations=3)(new_conv(), "q", status)
    assert len(model.calls) == 3
    assert status == ["Looking at the schema"] * 3
    assert ui.prose == STEP_LIMIT_PROSE and ui.messages == []


# L5
@pytest.mark.db
async def test_write_attempt_is_reported(runner, catalog):
    model = ScriptedCompletion([
        tool_calls(("run_sql", {"sql": "DELETE FROM mart.contract", "purpose": "oops"})),
        tool_calls(("run_sql", {"sql": TOP_SQL, "purpose": "top suppliers"})),
        text(TOP_REPLY),
    ])
    ui = await make_loop(runner, catalog, model)(new_conv(), "q", Statuses())
    err = json.loads(model.calls[1]["messages"][-1]["content"])["error"]
    assert "only SELECT" in err["message"]
    assert ui.messages


# L6
async def test_model_error():
    model = ScriptedCompletion([
        litellm.exceptions.AuthenticationError(message="bad key", llm_provider="meta", model="m"),
    ])
    conv = new_conv()
    ui = await make_loop(FakeRunner(), None, model)(conv, "hello", Statuses())
    assert ui.prose == MODEL_FAILED_PROSE.format(name="AuthenticationError")
    assert conv.history == [{"role": "user", "content": "hello"}]


# L7
async def test_model_switch_kwargs():
    model = ScriptedCompletion([text("Hi.")])
    loop = make_loop(FakeRunner(), None, model, model="openrouter/some-model", reasoning_effort=None,
                     prompt_cache_key="explorer-demo")
    await loop(new_conv(), "q", Statuses())
    kw = model.calls[0]
    assert kw["model"] == "openrouter/some-model"
    assert kw["prompt_cache_key"] == "explorer-demo"
    assert kw["num_retries"] == 0
    assert "reasoning_effort" not in kw
    assert kw["tools"] == TOOL_SCHEMAS
    assert "tool_choice" not in kw
    assert kw["messages"][0] == {"role": "system", "content": SYSTEM}

    model2 = ScriptedCompletion([text("Hi.")])
    await make_loop(FakeRunner(), None, model2, reasoning_effort="low")(new_conv(), "q", Statuses())
    assert model2.calls[0]["reasoning_effort"] == "low"


# L8
@pytest.mark.db
async def test_direct_json_format(runner, catalog):
    reply = (
        "The top supplier received {r1[0].total_value|currency_m}.\n\n"
        '<a2ui-json>{"version": "v0.9", "createSurface": {"surfaceId": "main", "catalogId": "'
        + EXPLORER_CATALOG_ID + '"}}</a2ui-json>\n\n'
        '<a2ui-json>{"version": "v0.9", "updateComponents": {"surfaceId": "main", "components": ['
        '{"id": "root", "component": "Metric", "result_id": "r1", "column": "total_value", '
        '"label": "Top supplier value", "format": "currency_m"}]}}</a2ui-json>'
    )
    model = ScriptedCompletion([tool_calls(("run_sql", {"sql": TOP_SQL, "purpose": "top"})), text(reply)])
    ui = await make_loop(runner, catalog, model, fmt="direct_json")(new_conv(), "q", Statuses())
    assert surface_ids(ui.messages) == {"turn-1"}
    assert "$" in ui.prose


def test_direct_json_prompt_differs():
    assert UiPipeline("express").format.prompt_generator.generate() != UiPipeline(
        "direct_json").format.prompt_generator.generate()


# L9
async def test_logging(caplog):
    secret_row = "SECRET-ROW-VALUE"
    fake = FakeRunner(0)

    async def run(sql):
        from explorer_agent.sql import SqlOk

        return SqlOk(columns=["v"], column_types=["text"], rows=[{"v": secret_row}], truncated=False, elapsed_ms=1)

    fake.run = run
    model = ScriptedCompletion([
        tool_calls(("run_sql", {"sql": "SELECT 'x' AS v", "purpose": "p"}), cached=None, reasoning=None),
        text("Value: {r1[0].v}.", prompt=2000, cached=1500),
    ])
    caplog.set_level(logging.DEBUG)
    await make_loop(fake, None, model)(new_conv(), "user question", Statuses())
    calls = [r.getMessage() for r in caplog.records if r.getMessage().startswith("model call:")]
    assert len(calls) == 2
    assert "cached=- " in calls[0] and "reasoning=-" in calls[0]
    assert "cached=1500" in calls[1] and "cache_rate=50%" in calls[1]  # 1500 of 3000 prompt tokens
    everything = "\n".join(r.getMessage() for r in caplog.records)
    assert SYSTEM not in everything and secret_row not in everything and "user question" not in everything


# L10
async def test_bad_tool_arguments():
    model = ScriptedCompletion([tool_calls(("run_sql", "{not json")), text("Sorry.")])
    await make_loop(FakeRunner(), None, model)(new_conv(), "q", Statuses())
    result = json.loads(model.calls[1]["messages"][-1]["content"])
    assert "not valid JSON" in result["error"]["message"]


# Regression (refuter M2): importing the loop must not touch the network (litellm fetches a
# model price map from GitHub on import unless told to use its local copy).
def test_import_makes_no_network_call():
    import subprocess
    import sys

    script = (
        "import socket\n"
        "def deny(*a, **k): raise SystemExit('network call attempted')\n"
        "socket.socket.connect = deny\n"
        "socket.create_connection = deny\n"
        "import explorer_agent.llm, explorer_agent.testing.stub_model\n"
        "print('ok')\n"
    )
    env = {k: v for k, v in __import__("os").environ.items() if k != "LITELLM_LOCAL_MODEL_COST_MAP"}
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60, env=env)
    assert done.stdout.strip() == "ok", done.stderr[-2000:]


def assert_paired(messages):
    """Every assistant tool call is answered by a tool message, and no tool message is orphaned."""
    pending: list[str] = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            assert not pending, f"unanswered tool calls {pending}"
            pending = [tc["id"] for tc in m["tool_calls"]]
        elif m["role"] == "tool":
            assert m["tool_call_id"] in pending, f"orphan tool result {m['tool_call_id']}"
            pending.remove(m["tool_call_id"])
        else:
            assert not pending, f"unanswered tool calls {pending}"
    assert not pending, f"unanswered tool calls {pending}"


# Regression (refuter M2): a failure while tools run must not leave unanswered tool calls behind.
async def test_status_failure_leaves_history_consistent():
    async def boom(text):
        raise RuntimeError("client went away")

    model = ScriptedCompletion([tool_calls(("run_sql", {"sql": "SELECT 1 AS one", "purpose": "p"}))])
    conv = new_conv()
    with pytest.raises(RuntimeError):
        await make_loop(FakeRunner(0), None, model)(conv, "q", boom)
    assert_paired(conv.history)


async def test_cancelled_turn_leaves_history_consistent():
    model = ScriptedCompletion([tool_calls(("run_sql", {"sql": "SELECT 1 AS one", "purpose": "p"}))])
    conv = new_conv()
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await make_loop(FakeRunner(delay=5), None, model)(conv, "q", Statuses())
    assert_paired(conv.history)


async def test_malformed_model_response_is_a_model_failure():
    from litellm import ModelResponse

    empty = ModelResponse(choices=[])
    empty.choices = []
    model = ScriptedCompletion([empty])
    ui = await make_loop(FakeRunner(), None, model)(new_conv(), "q", Statuses())
    assert ui.prose.startswith("The model service failed")


# M2's UiError path (L4 extends it with Repairs): the kept prose, the explanation and the errors.
@pytest.mark.db
async def test_ui_error_ends_turn_with_kept_prose(runner, catalog):
    bad = TOP_REPLY.replace('BarChart("r1"', 'BarChart("r9"')
    model = ScriptedCompletion([tool_calls(("run_sql", {"sql": TOP_SQL, "purpose": "top"})), text(bad)])
    ui = await make_loop(runner, catalog, model, max_ui_retries=0)(new_conv(), "q", Statuses())
    assert ui.messages == []
    first, explanation, errors = ui.prose.split("\n\n")
    assert first.startswith("**") and "$" in first  # the prose was fine, so it is kept
    assert explanation == NO_VIEW_PROSE
    assert "r9" in errors


def test_loop_config_from_settings(tmp_path):
    from pathlib import Path

    from explorer_agent.config import load_settings

    root = Path(__file__).resolve().parents[2]
    s = load_settings({"DATABASE_URL": "postgresql://u:p@h/db", "MODEL": "m/x", "REASONING_EFFORT": "",
                       "MAX_TOOL_ITERATIONS": "5", "MAX_UI_RETRIES": "1", "ROWS_TO_MODEL": "7"}, root)
    c = LoopConfig.from_settings(s)
    assert (c.model, c.reasoning_effort, c.prompt_cache_key) == ("m/x", None, "explorer-austender")
    assert (c.max_tool_iterations, c.max_ui_retries, c.rows_to_model) == (5, 1, 7)


# L4
@pytest.mark.db
async def test_ui_retry_succeeds(runner, catalog):
    bad = TOP_REPLY.replace('BarChart("r1"', 'BarChart("r9"')
    model = ScriptedCompletion([
        tool_calls(("run_sql", {"sql": TOP_SQL, "purpose": "top"})), text(bad), text(TOP_REPLY),
    ])
    status = Statuses()
    conv = new_conv()
    ui = await make_loop(runner, catalog, model)(conv, "q", status)
    assert ui.messages and surface_ids(ui.messages) == {"turn-1"}
    assert status[-1] == REPAIR_STATUS
    repair_msg = model.calls[2]["messages"][-1]
    assert repair_msg["role"] == "user" and "could not be shown" in repair_msg["content"]
    assert "r9" in repair_msg["content"]
    assert_paired(conv.history)


@pytest.mark.db
async def test_ui_retry_gives_up_after_max(runner, catalog):
    bad = TOP_REPLY.replace('BarChart("r1"', 'BarChart("r9"')
    model = ScriptedCompletion([tool_calls(("run_sql", {"sql": TOP_SQL, "purpose": "top"})), text(bad), text(bad)])
    ui = await make_loop(runner, catalog, model, max_ui_retries=1)(new_conv(), "q", Statuses())
    assert len(model.calls) == 3
    assert ui.messages == []
    assert NO_VIEW_PROSE in ui.prose and "r9" in ui.prose
    assert ui.prose.startswith("**")  # the last parsed prose is kept


async def test_repairs_count_toward_the_iteration_cap():
    bad = '<a2ui>\nroot = Column([c])\nc = BarChart("r9", "x", "y", "T")\n</a2ui>'
    model = ScriptedCompletion([text(bad)], repeat_last=True)
    status = Statuses()
    conv = new_conv()
    ui = await make_loop(FakeRunner(), None, model, max_tool_iterations=3, max_ui_retries=5)(conv, "q", status)
    assert len(model.calls) == 3
    # the last call's failure isn't followed by a Repair nobody would answer: give up with the errors
    assert NO_VIEW_PROSE in ui.prose and "r9" in ui.prose
    assert status == [REPAIR_STATUS, REPAIR_STATUS]
    assert conv.history[-1]["role"] == "assistant"


# L13
@pytest.mark.db
async def test_repair_runs_no_sql(runner, catalog):
    sql_seen = []
    real_run = runner.run

    async def spy(sql):
        sql_seen.append(sql)
        return await real_run(sql)

    runner.run = spy
    bad = TOP_REPLY.replace('BarChart("r1"', 'BarChart("r9"')
    model = ScriptedCompletion([
        tool_calls(("run_sql", {"sql": TOP_SQL, "purpose": "top"})),
        text(bad),
        tool_calls(("run_sql", {"sql": "SELECT 1 AS one", "purpose": "sneaky"})),  # ignored during a Repair
        text(TOP_REPLY),
    ])
    conv = new_conv()
    ui = await make_loop(runner, catalog, model, max_ui_retries=2)(conv, "q", Statuses())
    assert ui.messages
    assert sql_seen == [TOP_SQL]  # the Repair's tool call never ran
    assert "tool_choice" not in model.calls[0] and "tool_choice" not in model.calls[1]
    assert "tool_choice" not in model.calls[2] and "tool_choice" not in model.calls[3]  # Meta: "auto" only
    assert model.calls[2]["tools"] == model.calls[0]["tools"] == TOOL_SCHEMAS
    assert TOOLS_DURING_REPAIR in model.calls[3]["messages"][-1]["content"]  # counted as a failed Repair
    assert_paired(conv.history)


def _turn_messages(sent):
    return [m for m in sent if m["role"] == "tool"]


# L11
@pytest.mark.parametrize("mode", ["compact", "full"])
async def test_history_modes(mode):
    fake = FakeRunner(0)
    model = ScriptedCompletion([
        tool_calls(("run_sql", {"sql": "SELECT 1 AS one", "purpose": "first q"})), text("One: {r1[0].sql}."),
        tool_calls(("run_sql", {"sql": "SELECT 2 AS one", "purpose": "second q"})), text("Two: {r2[0].sql}."),
    ])
    conv = new_conv()
    loop = make_loop(fake, None, model, history_mode=mode)
    await loop(conv, "q1", Statuses())
    conv.turns, conv.surface_id = 2, "turn-2"
    await loop(conv, "q2", Statuses())
    final = model.calls[3]["messages"]
    turn1, turn2 = _turn_messages(final)
    if mode == "compact":
        assert turn1["content"] == 'r1 "first q": columns sql; 1 rows (truncated: no). SQL: SELECT 1 AS one'
    else:
        assert json.loads(turn1["content"])["rows_preview"] == [{"sql": "SELECT 1 AS one"}]
    assert json.loads(turn2["content"])["result_id"] == "r2"  # the current Turn is always in full
    assert_paired(final[1:])
    assert conv.history[conv.turn_starts[0] + 2]["content"].startswith("{")  # stored in full either way


def test_compact_summary_of_errors_and_schema_lookups():
    from explorer_agent.llm import compact_history

    call = lambda i, name, args: {"id": i, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": None, "tool_calls": [call("a", "run_sql", {"sql": "x", "purpose": "p"}),
                                                               call("b", "list_tables", {})]},
        {"role": "tool", "tool_call_id": "a", "content": json.dumps({"error": {"message": "boom"}})},
        {"role": "tool", "tool_call_id": "b", "content": json.dumps({"tables": []})},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "next"},
    ]
    out = compact_history(history, current_turn_start=5)
    assert [m["content"] for m in out[2:4]] == ["failed: boom", "(schema lookup; omitted)"]
    assert compact_history(history, 5) == out  # stable


def test_unknown_context_note_reaches_the_model():
    conv = new_conv()
    conv.unknown_context = True
    model = ScriptedCompletion([text("Hi.")])
    asyncio.run(make_loop(FakeRunner(), None, model)(conv, "and by agency?", Statuses()))
    assert model.calls[0]["messages"][1]["content"] == f"{UNKNOWN_CONTEXT_NOTE}\n\nand by agency?"


# L12
@pytest.mark.db
async def test_follow_up_reuses_earlier_result(runner, catalog):
    model = ScriptedCompletion([
        tool_calls(("run_sql", {"sql": TOP_SQL, "purpose": "top"})), text(TOP_REPLY),
        text('As a table.\n<a2ui>\nsurface("main")\nroot = Column([t])\nt = DataTable("r1", "Top suppliers")\n</a2ui>'),
    ])
    conv = new_conv()
    loop = make_loop(runner, catalog, model)
    await loop(conv, "top suppliers?", Statuses())
    conv.turns, conv.surface_id = 2, "turn-2"
    ui = await loop(conv, "show that as a table", Statuses())
    assert surface_ids(ui.messages) == {"turn-2"}
    assert conv.store.ids() == ["r1"]  # no new SQL


# ---- L14: one model retry ---------------------------------------------------------------------

def rate_limited():
    return litellm.exceptions.RateLimitError(message="slow down", llm_provider="meta", model="m")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


async def test_model_retry_once_then_success():
    sleeps = []

    async def sleep(s):
        sleeps.append(s)

    model = ScriptedCompletion([rate_limited(), text("Hi.")])
    loop = LlmLoop(LoopConfig(max_tool_iterations=1), SYSTEM, UiPipeline(), FakeRunner(), None,
                   completion=model, sleep=sleep)
    ui = await loop(new_conv(), "q", Statuses())
    assert ui.prose == "Hi."  # succeeded even though max_tool_iterations=1: the retry didn't count
    assert len(model.calls) == 2 and sleeps == [1.0]


# A blocked question is never transient: its own prose, and no retry.
async def test_content_policy_error_is_not_retried():
    async def sleep(s):
        pass

    blocked = litellm.ContentPolicyViolationError(message="rejected", model="m", llm_provider="meta")
    model = ScriptedCompletion([blocked, text("Hi.")])
    loop = LlmLoop(LoopConfig(), SYSTEM, UiPipeline(), FakeRunner(), None, completion=model, sleep=sleep)
    ui = await loop(new_conv(), "q", Statuses())
    assert ui.prose == CONTENT_POLICY_PROSE
    assert len(model.calls) == 1


async def test_model_retry_twice_fails():
    async def sleep(s):
        pass

    model = ScriptedCompletion([rate_limited(), rate_limited()])
    loop = LlmLoop(LoopConfig(), SYSTEM, UiPipeline(), FakeRunner(), None, completion=model, sleep=sleep)
    ui = await loop(new_conv(), "q", Statuses())
    assert ui.prose == MODEL_FAILED_PROSE.format(name="RateLimitError")
    assert len(model.calls) == 2


@pytest.mark.parametrize(
    ("error", "retried"),
    [
        (litellm.exceptions.InternalServerError(message="x", llm_provider="meta", model="m"), True),
        (litellm.exceptions.ServiceUnavailableError(message="x", llm_provider="meta", model="m"), True),
        (litellm.exceptions.Timeout(message="x", llm_provider="meta", model="m"), True),
        (litellm.exceptions.BadRequestError(message="x", llm_provider="meta", model="m"), False),
        (litellm.exceptions.AuthenticationError(message="x", llm_provider="meta", model="m"), False),
    ],
)
async def test_model_retry_only_for_transient_errors(error, retried):
    async def sleep(s):
        pass

    model = ScriptedCompletion([error, text("Hi.")])
    loop = LlmLoop(LoopConfig(), SYSTEM, UiPipeline(), FakeRunner(), None, completion=model, sleep=sleep)
    ui = await loop(new_conv(), "q", Statuses())
    assert (len(model.calls) == 2) is retried
    assert (ui.prose == "Hi.") is retried


async def test_model_retry_skipped_when_it_would_cross_the_budget(caplog):
    clock = Clock()

    def fail_late(kwargs):
        clock.now = 58.5  # 1 s wait + 2.9 s default median would end past the 60 s budget
        return rate_limited()

    model = ScriptedCompletion([fail_late, text("Hi.")])
    loop = LlmLoop(LoopConfig(turn_timeout_s=60), SYSTEM, UiPipeline(), FakeRunner(), None,
                   completion=model, clock=clock)
    ui = await loop(new_conv(), "q", Statuses())
    assert len(model.calls) == 1
    assert ui.prose == MODEL_FAILED_PROSE.format(name="RateLimitError")


async def test_model_retry_is_logged(caplog):
    async def sleep(s):
        pass

    caplog.set_level(logging.INFO)
    model = ScriptedCompletion([rate_limited(), text("Hi.")])
    await LlmLoop(LoopConfig(), SYSTEM, UiPipeline(), FakeRunner(), None, completion=model, sleep=sleep)(
        new_conv(), "q", Statuses())
    assert any(r.getMessage() == "model call retry: RateLimitError status=429" for r in caplog.records)


# ---- L15: the Turn time budget ----------------------------------------------------------------

async def test_turn_timeout_cancels_a_hanging_model_call():
    cancelled = []

    async def hang(kwargs):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    model = ScriptedCompletion([hang])
    loop = LlmLoop(LoopConfig(turn_timeout_s=1), SYSTEM, UiPipeline(), FakeRunner(), None, completion=model)
    start = asyncio.get_running_loop().time()
    conv = new_conv()
    ui = await loop(conv, "q", Statuses())
    assert asyncio.get_running_loop().time() - start < 2
    assert ui.prose == TIMEOUT_PROSE and cancelled == [True]
    assert_paired(conv.history)


@pytest.mark.db
async def test_turn_timeout_cancels_a_running_query(ro_pool, catalog):
    slow = SqlRunner(ro_pool, ("mart",), 30_000, 100, guard_enabled=False)
    model = ScriptedCompletion([tool_calls(("run_sql", {"sql": "SELECT pg_sleep(5) AS s", "purpose": "slow"}))])
    loop = LlmLoop(LoopConfig(turn_timeout_s=1), SYSTEM, UiPipeline(), slow, catalog, completion=model)
    conv = new_conv()
    ui = await loop(conv, "q", Statuses())
    assert ui.prose == TIMEOUT_PROSE
    assert_paired(conv.history)
    await asyncio.sleep(0.5)
    check = SqlRunner(ro_pool, ("mart",), 5000, 10, guard_enabled=False)
    out = await check.run("SELECT count(*) AS n FROM pg_stat_activity WHERE query LIKE 'SELECT pg_sleep(5)%'")
    assert out.rows == [{"n": 0}]  # the query was cancelled server-side
    assert (await check.run("SELECT 1 AS one")).rows == [{"one": 1}]  # the pool still works


async def test_timeout_keeps_last_parsed_prose():
    bad = 'Something useful.\n<a2ui>\nroot = Column([c])\nc = BarChart("r9", "x", "y", "T")\n</a2ui>'

    async def hang(kwargs):
        await asyncio.sleep(30)

    model = ScriptedCompletion([text(bad), hang])
    loop = LlmLoop(LoopConfig(turn_timeout_s=1), SYSTEM, UiPipeline(), FakeRunner(), None, completion=model)
    ui = await loop(new_conv(), "q", Statuses())
    assert ui.prose == f"Something useful.\n\n{TIMEOUT_PROSE}"


async def test_slow_turn_logged(caplog):
    clock = Clock()

    def slow_answer(kwargs):
        clock.now += 31
        return text("Hi.")

    model = ScriptedCompletion([slow_answer])
    caplog.set_level(logging.WARNING)
    await LlmLoop(LoopConfig(slow_turn_s=30), SYSTEM, UiPipeline(), FakeRunner(), None, completion=model,
                  clock=clock)(new_conv(), "q", Statuses())
    slow = [r.getMessage() for r in caplog.records if r.getMessage().startswith("slow turn:")]
    assert slow == ["slow turn: 31.0 s (target 30 s), model_calls=1 repairs=0 queries=0"]


# Regression: LiteLLM loads the nearest .env on import (find_dotenv from its own folder), which
# put the repo root's real API key and DATABASE_URL into every process, tests included. Only
# __main__ loads configuration, from the file it is told to use.
def test_import_does_not_load_dotenv():
    import os
    import subprocess
    import sys

    script = (
        "import os\n"
        "import explorer_agent.llm, explorer_agent.testing.stub_model\n"
        "print(sorted(k for k in ('DATABASE_URL', 'META_API_KEY', 'FIREWORKS_API_KEY') if k in os.environ))\n"
    )
    env = {k: v for k, v in os.environ.items()
           if k not in ("DATABASE_URL", "META_API_KEY", "FIREWORKS_API_KEY", "LITELLM_MODE")}
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60, env=env)
    assert done.stdout.strip() == "[]", done.stderr[-2000:]


def test_litellm_dotenv_autoload_is_switched_off():
    import os

    import explorer_agent  # noqa: F401

    assert os.environ["LITELLM_MODE"] != "DEV"



def test_step_limit_keeps_last_parsed_prose():
    import asyncio as _a

    bad = 'Useful prose.\n<a2ui>\nroot = Column([c])\nc = BarChart("r9", "x", "y", "T")\n</a2ui>'
    model = ScriptedCompletion([text(bad), tool_calls(("list_tables", {}))], repeat_last=True)

    class Cat:
        async def objects(self):
            return []

    # the Repair is answered with a tool call (ignored, a failed Repair) until the cap: the prose stays
    ui = _a.run(make_loop(FakeRunner(), Cat(), model, max_tool_iterations=2, max_ui_retries=5)(new_conv(), "q", Statuses()))
    assert ui.prose.startswith("Useful prose.\n\n")


def test_compact_summary_reports_truncation():
    from explorer_agent.llm import compact_history

    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "run_sql",
                                                          "arguments": json.dumps({"sql": "S", "purpose": "p"})}}]},
        {"role": "tool", "tool_call_id": "a", "content": json.dumps(
            {"result_id": "r1", "columns": ["x"], "row_count": 5000, "truncated": True})},
        {"role": "assistant", "content": "reply"},
    ]
    assert compact_history(history, 4)[2]["content"] == 'r1 "p": columns x; 5000 rows (truncated: yes). SQL: S'


async def test_slow_turn_counts_queries_and_repairs(caplog):
    clock = Clock()
    bad = '<a2ui>\nroot = Column([c])\nc = BarChart("r9", "x", "y", "T")\n</a2ui>'

    def later(response):
        def step(kwargs):
            clock.now += 11
            return response
        return step

    model = ScriptedCompletion([
        later(tool_calls(("run_sql", {"sql": "SELECT 1 AS one", "purpose": "a"}),
                         ("run_sql", {"sql": "SELECT 2 AS one", "purpose": "b"}))),
        later(text(bad)),
        later(text("Fine.")),
    ])
    caplog.set_level(logging.WARNING)
    await LlmLoop(LoopConfig(slow_turn_s=30), SYSTEM, UiPipeline(), FakeRunner(0), None, completion=model,
                  clock=clock)(new_conv(), "q", Statuses())
    slow = [r.getMessage() for r in caplog.records if r.getMessage().startswith("slow turn:")]
    assert slow == ["slow turn: 33.0 s (target 30 s), model_calls=3 repairs=1 queries=2"]


@pytest.mark.parametrize(("fail_at", "retried"), [(55.1, True), (55.2, False)])
async def test_model_retry_budget_boundary(fail_at, retried):
    # 60 s budget, 1 s wait, 3.9 s measured median → a retry must start by 55.1 s
    clock = Clock()

    async def sleep(s):
        clock.now += s

    def warm(kwargs):
        clock.now += 3.9  # one measured call: the median is now 3.9 s, not the 2.9 s default
        return text("Warm.")

    def fail(kwargs):
        clock.now = fail_at
        return rate_limited()

    model = ScriptedCompletion([warm, fail, text("Hi.")])
    loop = LlmLoop(LoopConfig(turn_timeout_s=60), SYSTEM, UiPipeline(), FakeRunner(), None,
                   completion=model, clock=clock, sleep=sleep)
    await loop(new_conv(), "first", Statuses())
    clock.now = 0.0
    ui = await loop(new_conv(), "q", Statuses())
    assert (ui.prose == "Hi.") is retried


# Regression (live check 2026-11-02): Meta's API accepts only tool_choice="auto". Repairs sent
# tool_choice="none", so every Repair failed with HTTP 400 and the Turn ended "model service failed".
def meta_tool_choice(step):
    def respond(kwargs):
        if kwargs.get("tool_choice", "auto") != "auto":
            return litellm.exceptions.BadRequestError(
                message='only "auto" is supported for `tool_choice`', llm_provider="meta", model="m"
            )
        return step
    return respond


BROKEN_VIEW = '<a2ui>\nroot = Column([c])\nc = Metric("r9", "value", "Label")\n</a2ui>'


async def test_repair_request_is_one_meta_accepts():
    model = ScriptedCompletion([meta_tool_choice(text(BROKEN_VIEW)), meta_tool_choice(text("Fixed."))])
    loop = LlmLoop(LoopConfig(), SYSTEM, UiPipeline(), FakeRunner(), None, completion=model)
    ui = await loop(new_conv(), "q", Statuses())
    assert ui.prose == "Fixed."
    assert len(model.calls) == 2
    assert model.calls[1]["tools"] == model.calls[0]["tools"]  # the cached prefix is unchanged


async def test_ui_errors_that_start_a_repair_are_logged(caplog):
    model = ScriptedCompletion([text(BROKEN_VIEW), text("Fixed.")])
    loop = LlmLoop(LoopConfig(), SYSTEM, UiPipeline(), FakeRunner(), None, completion=model)
    with caplog.at_level(logging.WARNING, logger="explorer_agent.llm"):
        await loop(new_conv(), "q", Statuses())
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith(REPAIR_LOG)]
    assert len(lines) == 1 and "r9" in lines[0]


async def test_each_repair_and_the_final_failure_are_logged(caplog):
    model = ScriptedCompletion([text(BROKEN_VIEW), text(BROKEN_VIEW)])
    loop = LlmLoop(LoopConfig(max_ui_retries=1), SYSTEM, UiPipeline(), FakeRunner(), None, completion=model)
    with caplog.at_level(logging.WARNING, logger="explorer_agent.llm"):
        ui = await loop(new_conv(), "q", Statuses())
    assert NO_VIEW_PROSE in ui.prose
    messages = [r.getMessage() for r in caplog.records]
    assert len([m for m in messages if m.startswith(REPAIR_LOG)]) == 1
    assert len([m for m in messages if m.startswith(NO_REPAIR_LOG)]) == 1


async def test_tools_during_a_repair_are_logged(caplog):
    model = ScriptedCompletion([
        text(BROKEN_VIEW),
        tool_calls(("run_sql", {"sql": "SELECT 1 AS one", "purpose": "sneaky"})),
        text("Fixed."),
    ])
    with caplog.at_level(logging.WARNING, logger="explorer_agent.llm"):
        await LlmLoop(LoopConfig(), SYSTEM, UiPipeline(), FakeRunner(), None, completion=model)(new_conv(), "q", Statuses())
    repairs = [r.getMessage() for r in caplog.records if r.getMessage().startswith(REPAIR_LOG)]
    assert len(repairs) == 2 and TOOLS_DURING_REPAIR in repairs[1]


async def test_logged_repair_errors_are_capped(caplog):
    long_name = "x" * 2000
    reply = f'<a2ui>\nroot = Column([c])\nc = Metric("{long_name}", "value", "Label")\n</a2ui>'
    model = ScriptedCompletion([text(reply), text("Fixed.")])
    with caplog.at_level(logging.WARNING, logger="explorer_agent.llm"):
        await LlmLoop(LoopConfig(), SYSTEM, UiPipeline(), FakeRunner(), None, completion=model)(new_conv(), "q", Statuses())
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith(REPAIR_LOG))
    assert len(line) <= len(REPAIR_LOG) + LOGGED_ERRORS_MAX + 1 and line.endswith("…")
