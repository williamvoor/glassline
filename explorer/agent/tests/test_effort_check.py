"""Tests for scripts/effort_check.py (explorer/specs/effort-check/SPEC.md). No model, network or database."""

import json

import httpx
import pytest

import effort_check as ec
from explorer_agent.executor import GENERIC_ERROR, UI_ERROR_PREFIX
from explorer_agent.llm import (
    CONTENT_POLICY_PROSE,
    MODEL_FAILED_PROSE,
    NO_VIEW_PROSE,
    REPAIR_STATUS,
    STEP_LIMIT_PROSE,
    TIMEOUT_PROSE,
    LlmLoop,
)
from explorer_agent.server import build_app
from explorer_agent.testing.stub_server import FIXTURE_SQL, STATUS_TEXT, stub_answerer

URL = "http://agent.test"


def stub_client() -> httpx.AsyncClient:
    app = build_app(stub_answerer, url=URL, client_origin="http://127.0.0.1:5173")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL)


def sse(*events: dict) -> str:
    return "".join(f"data: {json.dumps({'jsonrpc': '2.0', 'id': '1', 'result': e})}\n\n" for e in events)


def status_event(text: str, *, final: bool = False, parts: list | None = None) -> dict:
    return {
        "kind": "status-update", "taskId": "t", "contextId": "c", "final": final,
        "status": {"state": "completed" if final else "working",
                   "message": {"kind": "message", "role": "agent", "messageId": "m",
                               "parts": parts if parts is not None else [{"kind": "text", "text": text}]}},
    }


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=URL)


def sse_response(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


# H1
async def test_ask_against_stub_agent():
    async with stub_client() as c:
        r = await ec.ask(c, "top suppliers?")
    assert r["failure"] == "none"
    assert r["statuses"] == [STATUS_TEXT]
    assert "Supplier A" in r["prose"]
    s = r["surface"]
    assert s["metrics"] == [["Top supplier value", "$3,441.8M"]]
    (table,) = s["tables"]
    assert table["title"] == "Suppliers"
    assert table["columns"] == ["Supplier", "Contracts", "Value"]
    assert table["rows"][0] == ["Supplier A", "37", "$3,441,812,603"]
    (chart,) = s["charts"]
    assert chart["title"] == "Value by supplier"
    assert chart["values"][0]["supplier"] == "Supplier A"
    assert len(s["sql"]) == 3 and all(q["sql"] == FIXTURE_SQL for q in s["sql"])
    assert s["a2ui"], "raw A2UI messages are kept"
    assert r["context_id"], "the real A2A server reports the Conversation's context ID"


# H2
async def test_ask_sends_extension_and_fresh_conversation():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return sse_response(sse(status_event("done", final=True)))

    async with mock_client(handler) as c:
        await ec.ask(c, "q")
    (req,) = seen
    assert req.headers["X-A2A-Extensions"] == ec.EXT_URI
    message = json.loads(req.content)["params"]["message"]
    assert "contextId" not in message
    assert message["metadata"] == ec.CAPS
    assert json.loads(req.content)["method"] == "message/stream"


# H3
@pytest.mark.parametrize(
    ("prose", "kind"),
    [
        (TIMEOUT_PROSE + " Partial answer.", "timeout"),
        (STEP_LIMIT_PROSE, "step_limit"),
        (NO_VIEW_PROSE + "\n- bad prop", "no_view"),
        (UI_ERROR_PREFIX + " boom", "ui_error"),
        (MODEL_FAILED_PROSE.format(name="APIError"), "model_failed"),
        (CONTENT_POLICY_PROSE, "content_policy"),
        (GENERIC_ERROR, "error"),
        ("Defence awarded $38.4B across 24,187 contracts.", "none"),
    ],
)
def test_classify_failure(prose, kind):
    assert ec.classify_failure(prose) == kind


# H3 regression: the agent puts partial prose *before* the failure sentence (LlmLoop._give_up).
@pytest.mark.parametrize(
    ("explanation", "errors", "kind"),
    [(TIMEOUT_PROSE, [], "timeout"), (STEP_LIMIT_PROSE, [], "step_limit"), (NO_VIEW_PROSE, ["bad prop"], "no_view")],
)
@pytest.mark.parametrize("last_prose", ["", "Defence got $38B in 2014."])
def test_classify_failure_from_give_up(explanation, errors, kind, last_prose):
    loop = LlmLoop.__new__(LlmLoop)  # _give_up uses no instance state
    prose = loop._give_up(explanation, last_prose, errors).prose
    assert ec.classify_failure(prose) == kind


# H4
async def test_transport_failures():
    def http_500(request):
        return httpx.Response(500, text="nope")

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    def no_final(request):
        return sse_response(sse(status_event("Running SQL: x")))

    for handler, expect in ((http_500, "HTTP 500"), (refused, "ConnectError"), (no_final, "without a final")):
        async with mock_client(handler) as c:
            r = await ec.ask(c, "q")
        assert r["failure"] == "transport", handler.__name__
        assert expect in r["prose"]
        assert r["surface"]["tables"] == []


# H4 regression: a final event without a message, or not completed, must not crash or pass as success.
async def test_odd_final_events():
    no_message = status_event("", final=True)
    del no_message["status"]["message"]
    failed = status_event("", final=True, parts=[])
    failed["status"]["state"] = "failed"
    for event, expect in ((no_message, "completed"), (failed, "failed")):
        async with mock_client(lambda request, e=event: sse_response(sse(e))) as c:
            r = await ec.ask(c, "q")
        assert r["failure"] == "transport"
        assert expect in r["prose"]


# H5
async def test_repairs_counted():
    def handler(request):
        return sse_response(sse(
            status_event("Running SQL: a"), status_event(REPAIR_STATUS), status_event(REPAIR_STATUS),
            status_event("fine", final=True),
        ))

    async with mock_client(handler) as c:
        r = await ec.ask(c, "q")
    assert r["repairs"] == 2
    assert r["statuses"] == ["Running SQL: a", REPAIR_STATUS, REPAIR_STATUS]


LOG = """\
explorer: listening on http://127.0.0.1:65390
2026-11-02 01:00:00,001 INFO explorer_agent.tools: run_sql (defence total): SELECT sum(current_valu)
FROM mart.contract
WHERE agency_name = 'Department of Defence'
2026-11-02 01:00:00,050 INFO explorer_agent.tools: run_sql failed: 42703 column "current_valu" does not exist
2026-11-02 01:00:01,000 INFO explorer_agent.llm: model call: model=meta/muse-spark-1.3 prompt=12,345 cached=11,000 completion=210 reasoning=0 latency_ms=2100 cache_rate=89%
2026-11-02 01:00:01,100 INFO explorer_agent.tools: run_sql (defence total): SELECT sum(current_value)
FROM mart.contract
2026-11-02 01:00:01,300 INFO explorer_agent.sql: sql ok: 1 rows in 180 ms
2026-11-02 01:00:02,000 INFO explorer_agent.llm: model call: model=meta/muse-spark-1.3 prompt=13,000 cached=- completion=400 reasoning=- latency_ms=3000 cache_rate=0%
2026-11-02 01:00:02,100 WARNING explorer_agent.ui: reply has numbers typed by the model: 38.4, 24187
2026-11-02 01:00:02,200 WARNING explorer_agent.llm: turn timed out after 60 s
"""


# H6
def test_parse_log_slice():
    p = ec.parse_log_slice(LOG)
    assert p["model_calls"] == [
        {"prompt": 12345, "cached": 11000, "completion": 210, "reasoning": 0, "latency_ms": 2100},
        {"prompt": 13000, "cached": None, "completion": 400, "reasoning": None, "latency_ms": 3000},
    ]
    first, second = p["sql_attempts"]
    assert first["ok"] is False and first["sqlstate"] == "42703" and "current_valu" in first["message"]
    assert first["purpose"] == "defence total"
    assert first["sql"].endswith("WHERE agency_name = 'Department of Defence'"), "multi-line SQL kept"
    assert second == {"purpose": "defence total", "sql": "SELECT sum(current_value)\nFROM mart.contract", "ok": True}
    assert p["bare_numbers"] == ["38.4", "24187"]
    assert p["log_errors"] == ["WARNING explorer_agent.llm: turn timed out after 60 s"]


# H6 regression: parallel tool calls interleave their log lines, so outcomes can't be paired.
def test_parse_log_slice_parallel_calls():
    log = (
        "2026-11-02 01:00:00,001 INFO explorer_agent.tools: run_sql (a): SELECT 1\n"
        "2026-11-02 01:00:00,002 INFO explorer_agent.tools: run_sql (b): SELECT 2\n"
        "2026-11-02 01:00:00,100 INFO explorer_agent.tools: run_sql failed: 42703 no such column\n"
        "2026-11-02 01:00:00,200 INFO explorer_agent.sql: sql ok: 1 rows in 5 ms\n"
        "2026-11-02 01:00:01,001 INFO explorer_agent.tools: run_sql (c): SELECT 3\n"
        "2026-11-02 01:00:01,200 INFO explorer_agent.sql: sql ok: 1 rows in 5 ms\n"
    )
    a, b, c = ec.parse_log_slice(log)["sql_attempts"]
    assert a["ok"] is None and b["ok"] is None, "outcome unknown when calls overlap"
    assert a["parallel"] and b["parallel"]
    assert c["ok"] is True and not c.get("parallel")
    assert ec.parse_log_slice(log)["sql_failures"] == 1


# H7
async def test_log_offsets(tmp_path):
    log = tmp_path / "agent.log"
    log.write_text("2026-11-02 00:00:00,000 INFO explorer_agent.tools: run_sql (earlier run): SELECT 1\n")

    def handler(request):
        with log.open("a") as f:
            f.write("2026-11-02 00:00:05,000 INFO explorer_agent.tools: run_sql (this run): SELECT 2\n")
        return sse_response(sse(status_event("answer", final=True)))

    ask = ec.Ask(3, "q3", 2, False)
    async with mock_client(handler) as c:
        rec = await ec.run_one(c, ask, "low", log)
    assert [a["purpose"] for a in rec["sql_attempts"]] == ["this run"]
    assert (rec["level"], rec["question"], rec["run"], rec["warmup"]) == ("low", 3, 2, False)


# H8
def test_agent_env():
    base = {"PATH": "/bin", "MODEL": "meta/muse-spark-1.3", "REASONING_EFFORT": "high", "LOG_LEVEL": "DEBUG"}
    env = ec.agent_env(base, "minimal", 4321)
    assert env["REASONING_EFFORT"] == "minimal"
    assert env["AGENT_PORT"] == "4321"
    assert env["LOG_LEVEL"] == "INFO"
    assert env["PATH"] == "/bin" and env["MODEL"] == "meta/muse-spark-1.3"
    assert base["REASONING_EFFORT"] == "high", "base env not mutated"


# H9
def test_schedule():
    asks = ec.schedule(2, [1, 2, 3, 4, 5])
    assert asks[0] == ec.Ask(0, ec.WARMUP_QUESTION, 0, True)
    assert [(a.question, a.run) for a in asks[1:]] == [(q, r) for r in (1, 2) for q in (1, 2, 3, 4, 5)]
    assert ec.WARMUP_QUESTION not in ec.QUESTIONS.values()
    assert [a.question for a in ec.schedule(1, [3])] == [0, 3]


# H10
def test_secret_scan(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("META_API_KEY=sk-file-key-123\nDATABASE_URL=postgresql://ro:pa55word-x@localhost:5434/db\n")
    secrets = ec.secret_values({"META_API_KEY": "sk-env-key-456"}, env_file)
    assert set(secrets) == {"sk-file-key-123", "pa55word-x", "sk-env-key-456"}
    clean, leaky = tmp_path / "clean.jsonl", tmp_path / "leaky.log"
    clean.write_text('{"prose": "fine"}\n')
    leaky.write_text("connecting with pa55word-x\n")
    assert ec.secret_hits([clean, leaky], secrets) == [leaky]


# H10 regression: percent-encoded passwords and EXPLORER_ENV_FILE.
def test_secret_values_encoded_and_override(tmp_path):
    other = tmp_path / "other.env"
    other.write_text("DATABASE_URL=postgresql://ro:p%40ss%2Fword@localhost/db\n")
    secrets = ec.secret_values({"EXPLORER_ENV_FILE": str(other)}, tmp_path / "missing.env")
    assert {"p%40ss%2Fword", "p@ss/word"} <= set(secrets)


# H10 regression: the scan runs even when a level fails part-way.
async def test_secret_scan_runs_on_early_exit(tmp_path, monkeypatch):
    scanned = []

    async def boom(level, asks, stamp, out_dir, jsonl, written):
        written.append(out_dir / "agent-x.log")
        raise SystemExit("agent did not start")

    monkeypatch.setattr(ec, "run_level", boom)
    monkeypatch.setattr(ec, "secret_hits", lambda paths, secrets: scanned.extend(paths) or [])
    with pytest.raises(SystemExit):
        await ec.run(["minimal"], 1, [1], tmp_path)
    assert any(p.name == "agent-x.log" for p in scanned)
    assert any(p.suffix == ".jsonl" for p in scanned)


def record(level, q, run, *, warmup=False, wall=10.0, failure="none", calls=(), rows=None):
    return {
        "level": level, "question": q, "question_text": ec.QUESTIONS.get(q, ec.WARMUP_QUESTION), "run": run,
        "warmup": warmup, "wall_s": wall, "statuses": [], "repairs": 0, "prose": f"answer {level} {q} {run}",
        "failure": failure, "bare_numbers": [], "sql_attempts": [], "log_errors": [],
        "model_calls": [{"prompt": p, "cached": c, "completion": 1, "reasoning": 0, "latency_ms": 1} for p, c in calls],
        "surface": {"metrics": [["Total", "$1.0B"]],
                    "tables": [{"title": "T", "columns": ["a"], "rows": rows or [["x"]], "caption": None}],
                    "charts": [], "sql": [{"summary": "s", "sql": "SELECT 1"}], "a2ui": []},
    }


# H11
def test_sheet():
    recs = [
        record("minimal", 0, 0, warmup=True, wall=99.0, failure="timeout"),
        record("minimal", 1, 1, rows=[[str(i)] for i in range(25)]),
        record("minimal", 1, 2, failure="no_view"),
        record("low", 1, 1),
    ]
    text = ec.render_sheet(recs, "runs.jsonl")
    assert ec.WARMUP_QUESTION not in text
    assert "answer minimal 0 0" not in text
    for head in ("### Q1 · minimal · run 1", "### Q1 · minimal · run 2", "### Q1 · low · run 1"):
        assert head in text
    assert text.count("Score: ") == 3
    assert "| 19 |" in text and "| 20 |" not in text and "5 more rows" in text
    assert "Failure: `no_view`" in text
    assert "reference-values.md#q1" in text


# H12
def test_summary():
    recs = [
        record("minimal", 0, 0, warmup=True, wall=100.0, failure="timeout", calls=[(1000, 0)]),
        record("minimal", 1, 1, wall=10.0, calls=[(1000, 0), (1000, 900)]),
        record("minimal", 2, 1, wall=20.0, calls=[(2000, 1800)]),
        record("minimal", 3, 1, wall=40.0, failure="timeout"),
        record("low", 1, 1, wall=5.0, calls=[(None, None)]),
    ]
    s = ec.summary(recs)
    assert s["minimal"] == {
        "runs": 3, "median_wall_s": 20.0, "max_wall_s": 40.0, "model_calls": 3,
        "prompt_tokens": 4000, "cached_tokens": 2700, "cache_rate": 67.5, "failures": 1,
    }
    assert s["low"]["cache_rate"] is None and s["low"]["model_calls"] == 1


# H12 regression: a call with a known prompt but unknown cached count is left out of the cache rate.
def test_summary_unknown_cached():
    recs = [record("low", 1, 1, calls=[(1000, 500), (1000, None)])]
    s = ec.summary(recs)["low"]
    assert s["cache_rate"] == 50.0
    assert s["prompt_tokens"] == 2000


# A note and a heading the model wrote in a plain Column are not a metric; Metric's Card is.
def test_surface_summary_metrics_come_from_cards():
    comps = [
        {"id": "root", "component": "Column", "children": ["title", "note", "m"]},
        {"id": "title", "component": "Text", "text": "Overseas share", "variant": "h2"},
        {"id": "note", "component": "Text", "text": "Basis: 9 of 10", "variant": "caption"},
        {"id": "m", "component": "Card", "child": "m__col"},
        {"id": "m__col", "component": "Column", "children": ["m__l", "m__v", "m__sql"]},
        {"id": "m__l", "component": "Text", "text": "Overseas value", "variant": "caption"},
        {"id": "m__v", "component": "Text", "text": "$4,106.3M", "variant": "h2"},
        {"id": "m__sql", "component": "SqlDetails", "summary": "s", "sql": "SELECT 1"},
    ]
    summary = ec.surface_summary([{"updateComponents": {"surfaceId": "s", "components": comps}}])
    assert summary["metrics"] == [["Overseas value", "$4,106.3M"]]
    assert summary["visuals"] == [], "a Metric's Card is not a Visual (SPEC §17 D3)"


# --- SPEC §17: drill counting and clicks ------------------------------------------------------


# D3
def test_surface_summary_lists_visuals_with_their_click():
    comps = [
        {"id": "root", "component": "Column", "children": ["c1", "t1", "c2"]},
        {"id": "c1", "component": "VegaChart", "spec": {},
         "action": {"source": "src-1", "rows": [0, 1, 2], "drillable": True}},
        {"id": "t1", "component": "DataGrid", "columns": [], "rows": [],
         "action": {"source": "src-2", "rows": [0], "drillable": False}},
        {"id": "c2", "component": "VegaChart", "spec": {}},
    ]
    summary = ec.surface_summary([{"updateComponents": {"surfaceId": "turn-1-1-x", "components": comps}}])
    assert summary["visuals"] == [
        {"kind": "chart", "id": "c1", "surface_id": "turn-1-1-x", "title": "", "clickable": True,
         "drillable": True, "source": "src-1", "rows": [0, 1, 2]},
        {"kind": "table", "id": "t1", "surface_id": "turn-1-1-x", "title": "", "clickable": True,
         "drillable": False, "source": "src-2", "rows": [0]},
        {"kind": "chart", "id": "c2", "surface_id": "turn-1-1-x", "title": "", "clickable": False,
         "drillable": False, "source": None, "rows": []},
    ]


# D4
def test_parse_log_slice_counts_dropped_drills():
    from explorer_agent.llm import DRILLS_DROPPED_LOG

    line = f"2026-11-03 01:00:00,001 WARNING explorer_agent.llm: {DRILLS_DROPPED_LOG}0, 2\n"
    assert ec.parse_log_slice(line)["drills_dropped"] == 2
    assert ec.parse_log_slice(LOG)["drills_dropped"] == 0


# D5
async def test_act_sends_the_client_action_message():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        final = status_event("", final=True, parts=[])
        final["status"]["message"]["metadata"] = {"explorerModel": None}
        return sse_response(sse(final))

    async with mock_client(handler) as c:
        r = await ec.act(c, "drill", "turn-1-1-x", "c1", {"source": "src-1", "row": 2}, "ctx-9")
    (req,) = seen
    assert req.headers["X-A2A-Extensions"] == ec.EXT_URI
    message = json.loads(req.content)["params"]["message"]
    assert message["contextId"] == "ctx-9" and message["metadata"] == ec.CAPS
    (part,) = message["parts"]
    assert part["kind"] == "data" and part["metadata"] == {"mimeType": "application/json+a2ui"}
    action = part["data"]["action"]
    assert part["data"]["version"] == "v0.9"
    assert {k: action[k] for k in ("name", "surfaceId", "sourceComponentId", "context")} == {
        "name": "drill", "surfaceId": "turn-1-1-x", "sourceComponentId": "c1", "context": {"source": "src-1", "row": 2},
    }
    assert isinstance(action["timestamp"], str) and action["timestamp"]
    assert r["failure"] == "none" and r["metadata"] == {"explorerModel": None} and r["wall_s"] >= 0


async def test_act_transport_failure_and_rejected_action():
    async with mock_client(lambda req: httpx.Response(500)) as c:
        r = await ec.act(c, "drill", "s", "c", {"source": "x", "row": 0}, "ctx")
    assert r["failure"] == "transport" and r["metadata"] == {}

    def rejected(request: httpx.Request) -> httpx.Response:
        final = status_event("That view is no longer available.", final=True)
        final["status"]["message"]["metadata"] = {"explorerActionFailed": True}
        return sse_response(sse(final))

    async with mock_client(rejected) as c:
        r = await ec.act(c, "drill", "s", "c", {"source": "x", "row": 0}, "ctx")
    assert r["failure"] == "action_failed"


async def test_act_drills_down_on_the_stub_agent_without_the_model():
    from explorer_agent.testing.stub_server import stub_drills

    app = build_app(stub_answerer, url=URL, client_origin="http://127.0.0.1:5173", drills=stub_drills(), model="stub")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL) as c:
        answer = await ec.ask(c, "top suppliers?")
        (visual,) = [v for v in answer["surface"]["visuals"] if v["drillable"]]
        r = await ec.act(c, "drill", visual["surface_id"], visual["id"],
                         {"source": visual["source"], "row": visual["rows"][0]}, answer["context_id"])
    assert answer["metadata"]["explorerModel"] == "stub"
    assert r["failure"] == "none", r["prose"]
    assert r["metadata"]["explorerModel"] is None, "a Drill-down is database only"
    assert r["surface"]["visuals"], "the Drill-down's Visual came back"
