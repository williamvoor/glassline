"""The draft loop with a scripted model: never the network (SPEC.md phase 2b: D1-D5, L2)."""

import json

import explorer_agent  # noqa: F401  (first: keeps LiteLLM from fetching its price map)
import litellm
import pytest
from explorer_agent.sql import SqlRunner
from explorer_agent.testing.stub_model import ScriptedCompletion, text, tool_calls

from explorer_bootstrap.draft import NUDGE, DraftError, build_draft_prompt, cache_key, run_draft
from explorer_bootstrap.settings import ALLOWED_MODELS, PRICES_PATH, SettingsError, load_draft_settings, load_prices

from conftest import valid_args

pytestmark = pytest.mark.db


@pytest.fixture
def settings():
    return load_draft_settings({"BOOTSTRAP_MAX_TOOL_ITERATIONS": "4"})


@pytest.fixture
def runner(ro_pool):
    return SqlRunner(ro_pool, ["bs"], 2000, row_cap=50)


def _tool_messages(call):
    return [m for m in call["messages"] if m["role"] == "tool"]


# D1
async def test_loop_happy_path(bs_report, runner, settings):
    model = ScriptedCompletion([
        tool_calls(("run_sql", {"sql": "SELECT count(*) AS n FROM bs.mixed", "purpose": "row count"})),
        tool_calls(("submit_draft", valid_args())),
    ])
    events = []
    args, usage = await run_draft(bs_report, runner, settings, completion=model, on_event=events.append)
    assert args == valid_args()
    first = model.calls[0]
    assert first["model"] == "meta/muse-wonder-1.0"
    assert [t["function"]["name"] for t in first["tools"]] == ["run_sql", "submit_draft"]
    assert first["prompt_cache_key"] == "bootstrap-bs"
    assert first["reasoning_effort"] == "low" and first["max_tokens"] == 32000
    assert "response_format" not in first and "tool_choice" not in first
    tool = json.loads(_tool_messages(model.calls[1])[0]["content"])
    assert tool == {"columns": ["n"], "rows": [{"n": 100}], "row_count": 1, "truncated": False}
    assert [e["type"] for e in events] == ["call", "sql", "call"]
    assert events[1] == {"type": "sql", "purpose": "row count", "sql": "SELECT count(*) AS n FROM bs.mixed"}
    assert usage["totals"]["calls"] == 2


# D2
@pytest.mark.parametrize(
    "sql", ["DELETE FROM bs.mixed", "SELECT * FROM mart.contract", "CREATE TABLE bs.x (a int)", "SELECT 1; SELECT 2"]
)
async def test_guard_refuses_model_sql(bs_report, runner, settings, ro_pool, sql):
    model = ScriptedCompletion([
        tool_calls(("run_sql", {"sql": sql, "purpose": "try"})),
        tool_calls(("submit_draft", valid_args())),
    ])
    await run_draft(bs_report, runner, settings, completion=model)
    result = json.loads(_tool_messages(model.calls[1])[0]["content"])
    assert "error" in result
    async with ro_pool.connection() as conn:
        assert (await (await conn.execute("SELECT count(*) FROM bs.mixed")).fetchone())[0] == 100


# D3
async def test_invalid_then_valid(bs_report, runner, settings):
    bad = valid_args()
    bad["comments"].append({"object": "bs.nope", "column": "", "comment": "x"})
    model = ScriptedCompletion([
        tool_calls(("submit_draft", bad), ("run_sql", {"sql": "SELECT 1 AS one FROM bs.mixed LIMIT 1", "purpose": "p"})),
        tool_calls(("submit_draft", "{not json")),
        tool_calls(("submit_draft", valid_args())),
    ])
    events = []
    args, _ = await run_draft(bs_report, runner, settings, completion=model, on_event=events.append)
    assert args == valid_args()
    replies = [json.loads(m["content"]) for m in _tool_messages(model.calls[1])]
    assert "problems" in replies[0]["error"] and any("comments[6].object" in p for p in replies[0]["error"]["problems"])
    assert replies[1]["error"]["message"].startswith("ignored")  # run_sql next to a submit is not run
    second = json.loads(_tool_messages(model.calls[2])[-1]["content"])
    assert any("not valid JSON" in p for p in second["error"]["problems"])
    assert [e["type"] for e in events].count("invalid_draft") == 2


async def test_nudge(bs_report, runner, settings):
    model = ScriptedCompletion([text("Here is my draft..."), tool_calls(("submit_draft", valid_args()))])
    await run_draft(bs_report, runner, settings, completion=model)
    assert model.calls[1]["messages"][-1] == {"role": "user", "content": NUDGE}
    stubborn = ScriptedCompletion([text("a"), text("b")])
    with pytest.raises(DraftError, match="without calling submit_draft"):
        await run_draft(bs_report, runner, settings, completion=stubborn)


async def test_cap(bs_report, runner, settings):
    model = ScriptedCompletion(
        [tool_calls(("run_sql", {"sql": "SELECT 1 AS one FROM bs.mixed LIMIT 1", "purpose": "p"}))], repeat_last=True
    )
    with pytest.raises(DraftError, match="after 4 model calls"):
        await run_draft(bs_report, runner, settings, completion=model)
    assert len(model.calls) == 4


async def test_model_error(bs_report, runner, settings, monkeypatch):
    key = "sk-THISisAfakeKEYthatIsLong1234567890"
    monkeypatch.setenv("META_API_KEY", key)
    err = litellm.exceptions.AuthenticationError(message=f"bad key {key}", llm_provider="meta", model="m")
    with pytest.raises(DraftError) as e:
        await run_draft(bs_report, runner, settings, completion=ScriptedCompletion([err]))
    assert "AuthenticationError" in str(e.value) and key not in str(e.value)


class _BrokenRunner:
    async def run(self, sql):
        from psycopg_pool import PoolTimeout

        raise PoolTimeout("couldn't get a connection after 30.00 sec")


async def test_unexpected_tool_failure_becomes_a_tool_error(bs_report, settings):
    model = ScriptedCompletion([
        tool_calls(("run_sql", {"sql": "SELECT 1", "purpose": "a"}), ("run_sql", {"sql": "SELECT 2", "purpose": "b"}),
                   ("list_tables", {})),
        tool_calls(("submit_draft", valid_args())),
    ])
    args, _ = await run_draft(bs_report, _BrokenRunner(), settings, completion=model)
    assert args == valid_args()
    replies = [json.loads(m["content"]) for m in _tool_messages(model.calls[1])]
    assert [r["error"]["message"] for r in replies[:2]] == ["run_sql failed unexpectedly (PoolTimeout)"] * 2
    assert "unknown tool 'list_tables'" in replies[2]["error"]["message"]


async def test_parallel_run_sql(bs_report, runner, settings):
    model = ScriptedCompletion([
        tool_calls(("run_sql", {"sql": "SELECT count(*) AS n FROM bs.mixed", "purpose": "a"}),
                   ("run_sql", {"sql": "SELECT count(*) AS n FROM bs.fresh", "purpose": "b"})),
        tool_calls(("submit_draft", valid_args())),
    ])
    await run_draft(bs_report, runner, settings, completion=model)
    replies = [json.loads(m["content"]) for m in _tool_messages(model.calls[1])]
    assert [r["rows"] for r in replies] == [[{"n": 100}], [{"n": 3}]]


async def test_model_call_timeout(bs_report, runner):
    import asyncio

    async def slow(_kwargs):
        await asyncio.sleep(5)

    s = load_draft_settings({"BOOTSTRAP_CALL_TIMEOUT_S": "1"})
    with pytest.raises(DraftError, match="longer than 1s"):
        await run_draft(bs_report, runner, s, completion=ScriptedCompletion([slow]))


async def test_model_error_redacts_the_configured_key(bs_report, runner, settings, monkeypatch):
    key = "short.k+y"
    monkeypatch.setenv("META_API_KEY", key)
    with pytest.raises(DraftError) as e:
        await run_draft(bs_report, runner, settings, completion=ScriptedCompletion([RuntimeError(f"rejected {key}")]))
    assert key not in str(e.value)


# D4
async def test_prompt_is_stable(bs_report):
    p1, p2 = build_draft_prompt(bs_report), build_draft_prompt(json.loads(json.dumps(bs_report)))
    assert p1 == p2
    assert p1.index("## Report") > p1.index("submit_draft")
    assert cache_key(bs_report) == "bootstrap-bs"
    for word in ("austender", "AusTender", "mart."):
        assert word not in build_draft_prompt({"schemas": [], "objects": []})


async def test_prompt_and_key_use_the_dataset_name(bs_report):
    assert cache_key({**bs_report, "dataset": "vicprop"}) == "bootstrap-vicprop"


# D5
async def test_usage_and_cost(bs_report, runner, settings):
    model = ScriptedCompletion([
        tool_calls(("run_sql", {"sql": "SELECT 1 AS one FROM bs.mixed LIMIT 1", "purpose": "p"}),
                   prompt=10_000, completion=1_000, cached=0, reasoning=400),
        tool_calls(("submit_draft", valid_args()), prompt=12_000, completion=5_000, cached=10_000, reasoning=None),
    ])
    _, usage = await run_draft(bs_report, runner, settings, completion=model)
    t = usage["totals"]
    assert (t["prompt_tokens"], t["cached_tokens"], t["completion_tokens"], t["reasoning_tokens"]) == (
        22_000, 10_000, 6_000, 400)
    # Wonder: $4 input, $0.20 cached, $20 output per 1M
    first = (10_000 * 4 + 1_000 * 20) / 1e6
    second = (2_000 * 4 + 10_000 * 0.2 + 5_000 * 20) / 1e6
    assert usage["calls"][0]["cost_usd"] == pytest.approx(first)
    assert t["cost_usd"] == pytest.approx(first + second)


def test_prices_match_litellm():
    for model in ALLOWED_MODELS:
        p = load_prices(model)
        entry = litellm.model_cost[model]
        assert p.input == pytest.approx(entry["input_cost_per_token"] * 1e6)
        assert p.cached_input == pytest.approx(entry["cache_read_input_token_cost"] * 1e6)
        assert p.output == pytest.approx(entry["output_cost_per_token"] * 1e6)
    assert PRICES_PATH.name == "prices.toml"


# L2
def test_settings():
    s = load_draft_settings({})
    assert (s.model, s.effort, s.max_tool_iterations, s.max_tokens) == ("meta/muse-wonder-1.0", "low", 12, 32000)
    assert (s.call_timeout_s, s.sql_row_cap, s.rows_to_model) == (300, 200, 20)
    s = load_draft_settings({"BOOTSTRAP_MODEL": "meta/muse-spark-1.3"}, effort="minimal")
    assert (s.model, s.effort, s.prices.output) == ("meta/muse-spark-1.3", "minimal", 4.25)
    for env, kw in [({"BOOTSTRAP_MODEL": "openai/gpt-x"}, {}), ({}, {"model": "meta/muse-spark-1.3-contributor"}),
                    ({}, {"effort": "none"}), ({"BOOTSTRAP_MAX_TOKENS": "0"}, {}),
                    ({"BOOTSTRAP_SQL_ROW_CAP": "x"}, {})]:
        with pytest.raises(SettingsError):
            load_draft_settings(env, **kw)
