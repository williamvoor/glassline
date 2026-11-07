"""Settings run Traces (explorer/specs/trace/SPEC.md T10): no network, a stub model."""

import json

import explorer_agent  # noqa: F401  (first: keeps LiteLLM from fetching its price map)
import pytest

from test_server import _app, _client, _model, _wait
from test_settings import _settings_app


def _trace_events(events):
    return [e["event"] for e in events if e.get("type") == "trace"]


@pytest.mark.db
async def test_a_traced_draft_streams_model_and_sql_events(ro_pool, tmp_path):
    app, _ = _app(ro_pool, tmp_path, _model())
    async with _client(app) as c:
        assert (await c.post("/api/report", json={"schemas": ["bs"]})).status_code == 200
        run_id = (await c.post("/api/draft", json={"trace": True})).json()["run_id"]
        done, events = await _wait(c, run_id)
    assert "error" not in done, done
    traced = _trace_events(events)
    kinds = [e["kind"] for e in traced]
    assert kinds[:4] == ["prompt", "model", "sql", "model"], kinds
    run_sql = traced[2]
    assert run_sql["source"] == "run_sql" and run_sql["sql"] == "SELECT count(*) AS n FROM bs.mixed"
    assert run_sql["ok"] and run_sql["row_count"] == 1
    assert traced[1]["reply"]["tool_calls"][0]["function"]["name"] == "run_sql"
    assert traced[1]["tokens"]["latency_ms"] >= 0


@pytest.mark.db
async def test_an_untraced_draft_has_no_trace_events(ro_pool, tmp_path):
    app, _ = _app(ro_pool, tmp_path, _model())
    async with _client(app) as c:
        assert (await c.post("/api/report", json={"schemas": ["bs"]})).status_code == 200
        run_id = (await c.post("/api/draft", json={})).json()["run_id"]
        _, events = await _wait(c, run_id)
    assert _trace_events(events) == []


@pytest.mark.db
async def test_traced_runs_are_saved_without_rows_and_only_the_newest_three_kept(pg, tmp_path):
    env = {"BS_DATABASE_URL": pg.ro_dsn}
    model = _model()
    for _ in range(3):
        model.steps.extend(_model().steps)
    app, data = _settings_app(tmp_path, env, None, model)
    async with _client(app) as c:
        assert (await c.post("/api/connect", json={"connection": "bs"})).status_code == 200
        assert (await c.post("/api/report", json={"schemas": ["bs"]})).status_code == 200
        ids = []
        for _ in range(4):
            run_id = (await c.post("/api/draft", json={"trace": True})).json()["run_id"]
            done, events = await _wait(c, run_id)
            assert "error" not in done, done
            ids.append(run_id)
        listed = (await c.get("/api/traces")).json()["runs"]
    files = sorted(p.stem.split("-", 1)[1] for p in (data / "traces").glob("*.json"))
    assert files == sorted(ids[1:])
    assert [r["run_id"] for r in listed] == list(reversed(ids[1:]))  # newest first
    run = listed[0]
    assert [e["kind"] for e in run["events"]][:3] == ["model", "sql", "model"]
    assert list(run["prompts"].values()) == [None]  # the prompt holds pg_stats values: hash only on disk
    tool = next(m for e in run["events"] if e["kind"] == "model" for m in e["messages"] if m["role"] == "tool")
    assert json.loads(tool["content"])["rows"] == {"rows_omitted": True}
    (saved_file,) = (data / "traces").glob(f"*-{run['run_id']}.json")
    saved = saved_file.read_text()
    assert '"rows": [' not in saved
