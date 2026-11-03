"""Tests for scripts/scenarios.py (explorer/specs/scenarios/SPEC.md). No model, network or database."""

import json
from pathlib import Path

import httpx
import pytest

import effort_check as ec
import scenarios as sc
from explorer_agent.llm import CONTENT_POLICY_PROSE, DRILLS_DROPPED_LOG
from explorer_agent.sql import SqlFail, SqlOk

AUSTENDER_SCENARIOS = Path(__file__).resolve().parents[2] / "datasets" / "austender" / "scenarios.toml"


def scenario(**raw) -> sc.Scenario:
    return sc.parse_scenario({"id": "s", "turns": ["q"], **raw})


def ref(sql="SELECT 1", expect=("v",)) -> dict:
    return {"sql": sql, "expect": list(expect)}


def turn(prose="Answer.", failure="none", metrics=(), tables=(), charts=(), sql=(), model_calls=(), typed=()) -> dict:
    return {
        "prose": prose, "failure": failure, "model_calls": list(model_calls),
        "bare_numbers": list(typed),  # what the agent logged as typed by the model
        "surface": {"metrics": [list(m) for m in metrics], "tables": list(tables), "charts": list(charts),
                    "sql": [{"summary": "", "sql": q} for q in sql]},
    }


TABLE = [{"title": "T", "columns": [], "rows": []}]


class StubRunner:
    def __init__(self, results: dict[str, SqlOk | SqlFail]):
        self.results = results
        self.seen: list[str] = []

    async def run(self, sql: str) -> SqlOk | SqlFail:
        self.seen.append(sql)
        return self.results[sql]


def ok(rows: list[dict], columns: list[str] | None = None) -> SqlOk:
    return SqlOk(columns=columns or (list(rows[0]) if rows else []), column_types=[], rows=rows,
                 truncated=False, elapsed_ms=1)


# --- 1, 2: loading and selection ---------------------------------------------------------------------


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "scenarios.toml"
    p.write_text(text)
    return p


VALID = '''
[[scenario]]
id = "a"
reviewed = true
turns = ["How much?"]
visual = ["metric"]
[scenario.reference]
sql = "SELECT 1 AS v"
expect = ["v"]
[[scenario.alternates]]
sql = "SELECT 2 AS v"
expect = ["v"]
[[scenario.wrong]]
label = "off by one"
sql = "SELECT 3 AS v"
expect = ["v"]
[scenario.tolerance]
v = { rel = 0.002, percent = true }

[[scenario]]
id = "b"
turns = ["Delete it."]
smoke = true
'''


def test_load_valid(tmp_path):
    a, b = sc.load(write(tmp_path, VALID))
    assert (a.id, a.reviewed, a.visual, a.reference.expect) == ("a", True, ("metric",), ("v",))
    assert [role for role, _ in a.entries()] == ["reference", "alternate 1", "wrong: off by one"]
    assert a.tol("v") == sc.Tolerance(rel=0.002, percent=True) and a.tol("v").floor == 0.0
    assert a.tol("other").floor == 0.01
    assert (b.smoke, b.reviewed) == (True, False)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"turns": ["q"], "smoke": True, "colour": "red"}, "unknown key"),
        ({"turns": [], "smoke": True}, "turns"),
        ({"turns": ["q"]}, "needs a reference, expect_empty or smoke"),
    ],
    ids=["unknown_key", "no_turns", "nothing_to_check"],
)
def test_load_rejects(raw, message):
    with pytest.raises(sc.ScenarioError, match=message):
        sc.parse_scenario({"id": "x", **raw})


def test_load_rejects_duplicate_ids(tmp_path):
    with pytest.raises(sc.ScenarioError, match="duplicate"):
        sc.load(write(tmp_path, '[[scenario]]\nid="a"\nturns=["q"]\nsmoke=true\n' * 2))


def test_selection(tmp_path):
    scenarios = sc.load(write(tmp_path, VALID))
    assert [s.id for s in sc.select(scenarios, None, False)] == ["a"]
    assert [s.id for s in sc.select(scenarios, None, True)] == ["a", "b"]
    assert [s.id for s in sc.select(scenarios, ["b"], True)] == ["b"]
    assert sc.select(scenarios, ["b"], False) == []
    with pytest.raises(sc.ScenarioError, match="unknown scenario"):
        sc.select(scenarios, ["zzz"], True)


# --- 3: value matching ------------------------------------------------------------------------------

DEFAULT = sc.Tolerance()
PCT = sc.Tolerance(rel=0.002, percent=True)


@pytest.mark.parametrize(
    ("expected", "cell", "tol", "match"),
    [
        (24187, 24187, DEFAULT, True),
        (24187, 24180, DEFAULT, False),  # counts are exact
        (24187, 24187.0, DEFAULT, True),
        (38415325951.97, 38415325951.97, DEFAULT, True),
        (38415325951.97, 38415325952.0, DEFAULT, True),  # whole dollars: within rel
        (38415325951.97, 38400000000.0, DEFAULT, True),  # $M-level rounding of a large total
        (38415325951.97, 38.4, DEFAULT, False),  # scaled to $B
        (10.0, 10.01, DEFAULT, True),  # the cents floor
        (10.0, 10.02, DEFAULT, False),
        ("Department of Defence", "  department of defence ", DEFAULT, True),
        ("Department of Defence", "Defence", DEFAULT, False),
        (6.5958, 6.5958, PCT, True),
        (6.5958, 6.6, PCT, True),
        (6.5958, 0.065958, PCT, True),  # a fraction, scaled
        (6.5958, 0.0627, PCT, False),  # the cancelled-denominator wrong answer
        (6.5958, 6.2679, PCT, False),
        (6.5958, 660, PCT, False),  # an integer cell is never scaled
        (6.5958, 660.0, PCT, False),  # nor a whole number from a numeric column
        (6.5958, 659.58, PCT, True),  # a / 100
        (6.5958, 7.0, PCT, False),
        (0.5, 0.505, PCT, False),  # no abs floor with percent (0.01 would let it through)
        (5.0, True, DEFAULT, False),
        (1, True, DEFAULT, False),  # a bool never matches a count
        (True, True, DEFAULT, True),
        (5.0, None, DEFAULT, False),
    ],
)
def test_value_match(expected, cell, tol, match):
    assert sc.value_matches(expected, cell, tol) is match


# --- 4, 4a, 5: scoring ------------------------------------------------------------------------------

REFS = {"reference": [{"v": 24187}], "alternate 1": [{"v": 24172}], "wrong: cancelled": [{"v": 24842}]}
SCORED = dict(
    visual=["metric", "table"], reference=ref(), alternates=[ref("SELECT 2")],
    wrong=[{**ref("SELECT 3"), "label": "cancelled"}],
)


def test_score_pass():
    r = sc.score(scenario(**SCORED), REFS, turn(metrics=[("Contracts", "24,187")]), [{"v": 24187}])
    assert (r["status"], r["reasons"], r["matched_wrong"]) == ("pass", [], [])


def test_score_extra_rows_pass():
    rows = [{"v": 1}, {"v": 24187, "other": "x"}, {"v": 99}]
    r = sc.score(scenario(**SCORED), REFS, turn(tables=TABLE, charts=[{"values": []}]), rows)
    assert r["status"] == "pass"


@pytest.mark.parametrize(
    ("t", "rows", "reason"),
    [
        (turn(failure="model_failed", metrics=[("c", "1")]), [{"v": 24187}], "failure"),
        (turn(charts=[{"values": []}]), [{"v": 24187}], "no_visual"),
        (turn(metrics=[("c", "1")]), [{"v": 1}], "missing"),
    ],
    ids=["failure", "no_visual", "missing"],
)
def test_score_fail_reasons(t, rows, reason):
    r = sc.score(scenario(**SCORED), REFS, t, rows)
    assert r["status"] == "fail" and reason in r["reasons"]
    if reason == "missing":
        assert r["missing"] == [("v", 24187)]


def test_score_alternate():
    r = sc.score(scenario(**SCORED), REFS, turn(tables=TABLE), [{"v": 24172}])
    assert (r["status"], r["missing"]) == ("pass", [])


@pytest.mark.parametrize(
    ("rows", "status"),
    [([{"v": 24842}], "fail"), ([{"v": 24187}, {"v": 24842}], "needs_review")],
    ids=["fail", "pass"],
)
def test_score_names_wrong_answer(rows, status):
    r = sc.score(scenario(**SCORED), REFS, turn(tables=TABLE), rows)
    assert r["status"] == status
    assert r["matched_wrong"] == ["cancelled"]


def test_status_blocked():
    r = sc.score(scenario(**SCORED), REFS, turn(prose=CONTENT_POLICY_PROSE, failure="content_policy"), [])
    assert r["status"] == "blocked"


def test_status_needs_review():
    # Both readings on one Surface: a hedge, which only a person can judge.
    r = sc.score(scenario(**SCORED), REFS, turn(metrics=[("c", "1")]), [{"v": 24187}, {"v": 24842}])
    assert (r["status"], r["reasons"]) == ("needs_review", [])


def test_percent_tolerance_comes_from_the_column():
    s = scenario(visual=["metric"], reference=ref(expect=("pct",)), tolerance={"pct": {"rel": 0.002, "percent": True}})
    refs = {"reference": [{"pct": 6.5958}]}
    assert sc.score(s, refs, turn(metrics=[("c", "1")]), [{"share": 0.066}])["status"] == "pass"
    assert sc.score(s, refs, turn(metrics=[("c", "1")]), [{"count": 660}])["status"] == "fail"


# --- 6: answer rows -----------------------------------------------------------------------------------


async def test_answer_rows_from_surface_sql():
    runner = StubRunner({"SELECT a": ok([{"x": 1}]), "SELECT b": SqlFail(None, "boom")})
    surface = {"sql": [{"sql": "SELECT a"}, {"sql": "SELECT a"}, {"sql": "SELECT b"}, {"sql": ""}]}
    rows, notes = await sc.answer_rows(surface, runner)
    assert runner.seen == ["SELECT a", "SELECT b"]  # deduplicated, empty skipped
    assert rows == [{"x": 1}]
    assert notes == [{"sql": "SELECT a", "ok": True, "rows": 1, "truncated": False},
                     {"sql": "SELECT b", "ok": False, "error": "boom"}]


# --- 7, 7a, 7b: answer_in, empty, prose, smoke, flags -----------------------------------------------


@pytest.mark.parametrize(
    ("value", "text", "found"),
    [
        (37, "Ventia won 37 contracts.", True),
        (37, "$37", True),
        (1234, "1,234 contracts", True),
        (37, "137 contracts", False),
        (37, "37.5 contracts", False),
        (37, "result r37", False),
        ("VENTIA AUSTRALIA PTY LTD", "It was ventia australia pty ltd.", True),
        ("Ventia", "Ventias", False),
        (1234, "$1,234.00", True),
        (25, "in FY2024-25", False),  # part of a date, not a count
    ],
)
def test_stated(value, text, found):
    assert sc.stated(value, [text]) is found


@pytest.mark.parametrize(
    ("t", "status", "reason"),
    [
        (turn(prose="Ventia won 37 contracts.", tables=TABLE), "pass", None),
        (turn(metrics=[("Contracts", "37")]), "pass", None),
        (turn(prose="Here is the table.", tables=TABLE), "fail", "not_stated"),  # 37 only in a reused table
        (turn(metrics=[("Contracts", "13")], tables=TABLE), "fail", "not_stated"),
    ],
    ids=["prose", "metric", "only_in_table", "wrong_metric"],
)
def test_answer_in(t, status, reason):
    s = scenario(visual=["metric", "table"], reference=ref(expect=("n",)), answer_in=["metric", "prose"])
    rows = [{"n": 37}, {"n": 13}, {"n": 29}]  # the reused top-5 table holds every count
    r = sc.score(s, {"reference": [{"n": 37}]}, t, rows)
    assert r["status"] == status
    if reason:
        assert reason in r["reasons"]


EMPTY = dict(forbid_visual=["chart"], expect_empty=True, prose_no_numbers=True)


@pytest.mark.parametrize(
    ("t", "rows", "status", "reason"),
    [
        (turn(prose="There is no Department of Magic in the data."), [], "pass", None),
        (turn(prose="It had 0 contracts and $0.00 in FY2024-25.", typed=["0", "0.00"]), [], "pass", None),
        (turn(prose="Nothing found.", metrics=[("Value", "$0")]), [{"v": 0}, {"v": None}], "pass", None),
        # Rows can't tell context from a silent fuzzy match: a person reads it (needs_review).
        (turn(prose="Here are the results.", tables=TABLE), [{"v": 55700946072.24}], "needs_review", "not_empty"),
        (turn(prose="Nothing.", charts=[{"values": []}]), [], "fail", "forbidden_visual"),
        (turn(prose="I searched 2 name patterns.", typed=["2"]), [], "fail", "prose"),
        (turn(prose="   "), [], "fail", "prose"),
    ],
    ids=["not_found", "typed_zeros", "zero_metric", "rows_not_empty", "chart", "typed_number", "empty_prose"],
)
def test_empty_checks(t, rows, status, reason):
    r = sc.score(scenario(**EMPTY), {}, t, rows)
    assert r["status"] == status
    if reason:
        assert reason in r["reasons"] + r["review"]


# Regression (first live run, 2026-11-02): a correct not-found answer failed. Its "125 agencies"
# came from the database through Cite, not typed by the model, and its rows held that context count.
def test_live_not_found_answer_is_not_failed():
    t = turn(prose="There are 125 agencies in 2024-25 and 0 matching Magic.", typed=["0"],
             metrics=[("Agencies in 2024-25", "125"), ("Matching Magic", "0")])
    r = sc.score(scenario(**EMPTY), {}, t, [{"match_count": 125, "magic_matches": 0}])
    assert r["reasons"] == [] and r["invented"] == []
    assert (r["status"], r["review"]) == ("needs_review", ["not_empty"])


def test_smoke_passes_unless_the_turn_fails():
    s = scenario(smoke=True)
    assert sc.score(s, {}, turn(prose="Done, deleted."), [])["status"] == "pass"  # read by a person
    assert sc.score(s, {}, turn(failure="error"), [])["status"] == "fail"
    assert sc.score(s, {}, turn(failure="content_policy"), [])["status"] == "blocked"


def test_flags():
    s = scenario(visual=["metric"], reference=ref(), max_model_calls=1)
    attempts = [{"sql": "SELECT 1 AS v FROM mart.t"}, {"sql": "DELETE FROM mart.contract"}]
    assert sc.write_attempted(attempts, ("mart",)) is True
    assert sc.write_attempted(attempts[:1], ("mart",)) is False
    r = sc.score(s, {"reference": [{"v": 1}]}, turn(metrics=[("c", "1")], model_calls=[{}, {}]), [{"v": 1}],
                 write_attempt=True)
    assert r["status"] == "pass"
    assert r["flags"] == {"over_max_model_calls": True, "write_attempted": True, "answer_truncated": False}


# --- 8: references ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "message"),
    [(SqlFail(None, "boom"), "failed"), (ok([], ["v"]), "no rows"), (ok([{"w": 1}]), "no column")],
)
async def test_reference_failure_stops_run(tmp_path, monkeypatch, result, message):
    started = []

    class NoAgent:
        def __init__(self, *a):
            started.append(a)

    monkeypatch.setattr(ec, "Agent", NoAgent)
    s = scenario(visual=["metric"], reference=ref())
    with pytest.raises(sc.ScenarioError, match=message):
        await sc.run([s], 1, "minimal", tmp_path / "out", StubRunner({"SELECT 1": result}), ("mart",))
    assert started == []  # stopped before the agent (and any model call)


async def test_references_record_rows_and_time():
    s = scenario(visual=["metric"], reference=ref(), wrong=[{**ref("SELECT 3"), "label": "w"}])
    out = await sc.run_references([s], StubRunner({"SELECT 1": ok([{"v": 1}]), "SELECT 3": ok([{"v": 3}])}))
    assert out["s"]["rows"] == {"reference": [{"v": 1}], "wrong: w": [{"v": 3}]}
    assert out["s"]["ran_at"]


# --- 9: several turns ----------------------------------------------------------------------------------


def sse(event: dict) -> str:
    return f"data: {json.dumps({'jsonrpc': '2.0', 'id': '1', 'result': event})}\n\n"


async def test_multi_turn_context():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        message = json.loads(request.content)["params"]["message"]
        seen.append(message.get("contextId"))
        text = message["parts"][0]["text"]
        event = {"kind": "status-update", "taskId": "t", "contextId": "ctx-1", "final": True,
                 "status": {"state": "completed", "message": {"kind": "message", "role": "agent", "messageId": "m",
                                                              "parts": [{"kind": "text", "text": f"re: {text}"}]}}}
        return httpx.Response(200, text=sse(event), headers={"content-type": "text/event-stream"})

    s = scenario(turns=["first?", "and the first one?"], smoke=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://agent.test") as c:
        turns, last = await sc.ask_turns(c, s, None)
    assert seen == [None, "ctx-1"]  # a fresh Conversation, then the same one
    assert [t["text"] for t in turns] == ["first?", "and the first one?"]
    assert last["prose"] == "re: and the first one?"  # only the last Turn is scored


# --- 10: report -----------------------------------------------------------------------------------------


def record(sid, run, status, *, smoke=False, wall=5.0, calls=2, reasons=(), wrong=()):
    return {"scenario": sid, "run": run, "smoke": smoke, "wall_s": wall, "model_calls": [{}] * calls,
            "repairs": 0, "prose": "p", "reference_ran_at": "2026-11-02T10:00:00+00:00",
            "score": {"status": status, "reasons": list(reasons), "missing": [], "not_stated": [],
                      "invented": [], "matched_wrong": list(wrong), "flags": {}}}


def test_report():
    records = [
        record("a", 1, "pass", wall=4.0), record("a", 2, "fail", reasons=["missing"], wall=6.0),
        record("b", 1, "needs_review", wrong=["cancelled"]), record("c", 1, "blocked"),
        record("w", 1, "pass", smoke=True, wall=10.0),
    ]
    s = sc.summary(records)
    assert (s["runs"], s["scored"], s["passed"]) == (5, 3, 1)  # smoke and blocked left out
    assert s["pass_rate"] == pytest.approx(1 / 3)
    assert (s["needs_review"], s["blocked"], s["smoke"]) == (1, 1, 1)
    assert s["wall_median"] == 5.0 and s["failures"] == {"missing": 1}
    text = sc.report(records)
    assert "Pass rate: 33% (1/3)" in text
    assert "w (smoke)" in text and "cancelled" in text
    assert "Reference queries ran at 2026-11-02T10:00:00+00:00" in text


# --- 12: the AusTender file -----------------------------------------------------------------------------


def test_austender_scenarios_load():
    scenarios = sc.load(AUSTENDER_SCENARIOS)
    assert len(scenarios) == 10
    assert all(s.reviewed for s in scenarios)
    assert {s.id for s in scenarios if s.smoke} == {"write-request"}
    assert all(s.reference or s.expect_empty or s.smoke for s in scenarios)



# --- review fixes ---------------------------------------------------------------------------------


def test_tolerance_applies_to_alternates_and_wrong_entries():
    s = scenario(
        visual=["metric"], reference=ref(expect=("x",)), alternates=[ref("SELECT 2", ("pct",))],
        wrong=[{**ref("SELECT 3", ("pct",)), "label": "cancelled"}],
        tolerance={"pct": {"rel": 0.002, "percent": True}},
    )
    refs = {"reference": [{"x": 999}], "alternate 1": [{"pct": 6.5958}], "wrong: cancelled": [{"pct": 6.2679}]}
    assert sc.score(s, refs, turn(metrics=[("c", "1")]), [{"share": 0.065958}])["status"] == "pass"
    r = sc.score(s, refs, turn(metrics=[("c", "1")]), [{"share": 0.062679}])
    assert r["matched_wrong"] == ["cancelled"]


def test_answer_in_accepts_any_matched_entry_that_is_stated():
    s = scenario(visual=["table"], reference=ref(expect=("n",)), alternates=[ref("SELECT 2", ("n",))],
                 answer_in=["prose"])
    refs = {"reference": [{"n": 37}], "alternate 1": [{"n": 13}]}
    r = sc.score(s, refs, turn(prose="It won 13.", tables=TABLE), [{"n": 37}, {"n": 13}])
    assert r["status"] == "pass"


def test_rerun_failure_and_truncation():
    s = scenario(**EMPTY)
    failed = [{"sql": "SELECT a", "ok": False, "error": "timeout"}]
    r = sc.score(s, {}, turn(prose="Nothing."), [], notes=failed)
    assert r["status"] == "fail" and "rerun_failed" in r["reasons"]  # no rows doesn't mean empty
    cut = [{"sql": "SELECT a", "ok": True, "rows": 5000, "truncated": True}]
    r = sc.score(scenario(**SCORED), REFS, turn(tables=TABLE), [{"v": 24187}], notes=cut)
    assert r["status"] == "pass" and r["flags"]["answer_truncated"] is True


@pytest.mark.parametrize(
    ("result", "message"),
    [(SqlOk(["v"], [], [{"v": 1}], True, 1), "row cap"), (ok([{"v": None}]), "null")],
    ids=["truncated", "null"],
)
async def test_references_reject_truncated_and_null(result, message):
    with pytest.raises(sc.ScenarioError, match=message):
        await sc.run_references([scenario(visual=["metric"], reference=ref())], StubRunner({"SELECT 1": result}))


def test_duplicate_wrong_labels_are_rejected():
    with pytest.raises(sc.ScenarioError, match="unique"):
        scenario(reference=ref(), wrong=[{**ref(), "label": "x"}, {**ref("SELECT 2"), "label": "x"}])


def test_report_path_never_overwrites_the_input(tmp_path):
    assert sc.report_path_for(tmp_path / "runs-20261102T0000Z.jsonl").name == "report-20261102T0000Z.md"
    assert sc.report_path_for(tmp_path / "old.json").name == "report-old.md"
    assert sc.report_path_for(tmp_path / "report-x.md").name == "report-report-x.md"  # never the input


def test_report_shows_prose_and_p90():
    long = "No. " + "I only have read access. " * 40
    records = [record("w", 1, "pass", smoke=True), record("b", 1, "needs_review", wrong=["cancelled"]),
               record("a", 1, "fail", reasons=["missing"])] + [record("c", i, "pass", wall=float(i)) for i in range(1, 11)]
    records[0]["prose"] = long
    text = sc.report(records)
    assert long.strip() in text  # smoke prose in full, for a person to read
    assert text.count("  - prose: ") == 3  # smoke, needs_review and fail; passing runs show none
    assert sc.summary(records)["wall_p90"] == 9.0
    records[1]["score"]["review"] = ["not_empty"]
    assert "for a person to check: not_empty" in sc.report(records)


async def test_a_failed_earlier_turn_ends_the_scenario(monkeypatch):
    asked = []

    async def fake_ask(client, text, context_id=None):
        asked.append(text)
        return {**turn(failure="transport", prose="stream ended"), "wall_s": 1.0, "context_id": None}

    monkeypatch.setattr(ec, "ask", fake_ask)
    turns, last = await sc.ask_turns(None, scenario(turns=["one", "two"], smoke=True), None)
    assert asked == ["one"] and last["failure"] == "transport"
    assert sc.score(scenario(smoke=True), {}, last, [])["status"] == "fail"


class FakeAgent:
    instances: list = []

    def __init__(self, level, log_path):
        self.log_path, self.url, self.proc, self.stopped = log_path, "http://agent.test", None, False
        FakeAgent.instances.append(self)

    async def start(self):
        self.log_path.write_text("")

    def stop(self):
        self.stopped = True


def fake_asks(monkeypatch, prose_by_question, *, raise_on=None):
    async def fake_ask(client, text, context_id=None):
        if text == raise_on:
            raise RuntimeError("boom")
        prose = prose_by_question.get(text, "Warm.")
        return {**turn(prose=prose, metrics=[("Contracts", "37")], sql=["SELECT 1"]),
                "wall_s": 2.0, "repairs": 0, "statuses": [], "context_id": "ctx"}

    monkeypatch.setattr(ec, "ask", fake_ask)
    monkeypatch.setattr(ec, "Agent", FakeAgent)
    monkeypatch.setattr(ec, "LOG_SETTLE_S", 0)


async def test_run_end_to_end(tmp_path, monkeypatch):
    fake_asks(monkeypatch, {"q": "It won 37."})
    monkeypatch.setattr(ec, "secret_values", lambda *a: ["sekrit-value"])
    s = scenario(visual=["metric"], reference=ref(expect=("n",)), answer_in=["prose"])
    runner = StubRunner({"SELECT 1": ok([{"n": 37}])})
    jsonl = await sc.run([s], 2, "minimal", tmp_path, runner, ("mart",))
    records = sc.read_records(jsonl)
    assert [r["score"]["status"] for r in records] == ["pass", "pass"]
    assert records[0]["answer_sql"] == [{"sql": "SELECT 1", "ok": True, "rows": 1, "truncated": False}]
    assert records[0]["reference_ran_at"] and "a2ui" not in records[0]["surface"]
    assert (tmp_path / jsonl.name.replace("runs-", "report-").replace(".jsonl", ".md")).exists()
    assert FakeAgent.instances[-1].stopped


async def test_run_secret_hit_and_part_way_failure(tmp_path, monkeypatch):
    s1 = scenario(id="s1", turns=["q1"], visual=["metric"], reference=ref(expect=("n",)))
    s2 = scenario(id="s2", turns=["q2"], smoke=True)
    runner = StubRunner({"SELECT 1": ok([{"n": 37}])})

    fake_asks(monkeypatch, {"q1": "the key is sekrit-value"})
    monkeypatch.setattr(ec, "secret_values", lambda *a: ["sekrit-value"])
    with pytest.raises(SystemExit, match="secret found"):
        await sc.run([s1], 1, "minimal", tmp_path / "a", runner, ("mart",))
    assert list((tmp_path / "a").glob("report-*.md"))  # written, then scanned

    fake_asks(monkeypatch, {"q1": "fine"}, raise_on="q2")
    with pytest.raises(RuntimeError, match="boom"):
        await sc.run([s1, s2], 1, "minimal", tmp_path / "b", runner, ("mart",))
    (report_file,) = (tmp_path / "b").glob("report-*.md")
    assert "s1" in report_file.read_text()  # the finished scenario is still reported
    assert FakeAgent.instances[-1].stopped


# --- SPEC §17 D6: Drill-down clicks after the last Turn --------------------------------------


def visual(rows, *, drillable=True, vid="c1"):
    return {"kind": "chart", "id": vid, "surface_id": "turn-1-1-x", "title": "T", "clickable": True,
            "drillable": drillable, "source": f"src-{vid}", "rows": rows}


def fake_clicks(monkeypatch, visuals, *, dropped=0, click_failure="none"):
    """`fake_asks` with these Visuals on every answer; records each `act` call."""
    calls: list[tuple] = []

    async def fake_ask(client, text, context_id=None):
        t = turn(prose="It won 37.", metrics=[("Contracts", "37")], sql=["SELECT 1"])
        t["surface"]["visuals"] = visuals
        if dropped and text != sc.WARMUP_QUESTION:  # the agent logs it; the harness reads its log
            with FakeAgent.instances[-1].log_path.open("a") as log:
                log.write(f"2026-11-03 01:00:00,001 WARNING explorer_agent.llm: {DRILLS_DROPPED_LOG}"
                          + ", ".join(str(i) for i in range(dropped)) + "\n")
        return {**t, "wall_s": 2.0, "repairs": 0, "statuses": [], "context_id": "ctx"}

    async def fake_act(client, name, surface_id, component_id, context, context_id):
        calls.append((name, surface_id, component_id, context, context_id))
        shown = [] if click_failure != "none" else [{"summary": "T", "sql": f"SELECT 2 WHERE fy = '2024-25' AND row = {context['row']}"}]
        return {"wall_s": 0.25, "failure": click_failure, "prose": "", "metadata": {"explorerModel": None},
                "surface": {"sql": shown}}

    monkeypatch.setattr(ec, "ask", fake_ask)
    monkeypatch.setattr(ec, "act", fake_act)
    monkeypatch.setattr(ec, "Agent", FakeAgent)
    monkeypatch.setattr(ec, "LOG_SETTLE_S", 0)
    monkeypatch.setattr(sc, "DRILL_SETTLE_S", 0)
    return calls


async def _one_run(tmp_path):
    s = scenario(visual=["metric"], reference=ref(expect=("n",)), answer_in=["prose"])
    jsonl = await sc.run([s], 1, "minimal", tmp_path, StubRunner({"SELECT 1": ok([{"n": 37}])}), ("mart",))
    (record,) = sc.read_records(jsonl)
    report_text = (tmp_path / jsonl.name.replace("runs-", "report-").replace(".jsonl", ".md")).read_text()
    return record, report_text


async def test_run_clicks_first_and_last_rows_of_the_first_drillable_visual(tmp_path, monkeypatch):
    ask_only = visual([0, 1], drillable=False, vid="t0")
    calls = fake_clicks(monkeypatch, [ask_only, visual([4, 0, 7]), visual([1], vid="c2")], dropped=1)
    record, report_text = await _one_run(tmp_path)
    assert calls == [
        ("drill", "turn-1-1-x", "c1", {"source": "src-c1", "row": 4}, "ctx"),
        ("drill", "turn-1-1-x", "c1", {"source": "src-c1", "row": 7}, "ctx"),
    ], "first and last in displayed order, on the first drillable Visual only"
    assert record["score"]["status"] == "pass", "clicks don't change the score"
    assert [v["id"] for v in record["visuals"]] == ["t0", "c1", "c2"]
    assert "visuals" not in record["surface"]
    assert record["drills_dropped"] == 1
    assert record["drill_downs"] == [
        {"click": "first", "row": 4, "wall_s": 0.25, "failure": "none", "no_model": True,
         "sql": ["SELECT 2 WHERE fy = '2024-25' AND row = 4"]},
        {"click": "last", "row": 7, "wall_s": 0.25, "failure": "none", "no_model": True,
         "sql": ["SELECT 2 WHERE fy = '2024-25' AND row = 7"]},
    ], "each click keeps the Drill-down's SQL as shown, values bound, so its filters can be checked"
    assert "- Drill queries: 3 clickable Visuals, 2 with a Drill query (67%), 1 dropped by the checks" in report_text
    assert ("- Drill-downs: 2, median 0.25 s, p90 0.25 s; first click median 0.25 s, last click median 0.25 s; "
            "called the model 0, failed 0") in report_text


async def test_run_without_a_drillable_visual_makes_no_clicks(tmp_path, monkeypatch):
    calls = fake_clicks(monkeypatch, [visual([0, 1], drillable=False)])
    record, report_text = await _one_run(tmp_path)
    assert calls == [] and record["drill_downs"] == []
    assert "1 clickable Visuals, 0 with a Drill query (0%)" in report_text
    assert "- Drill-downs: 0, median —" in report_text


async def test_run_clicks_a_one_row_visual_once(tmp_path, monkeypatch):
    calls = fake_clicks(monkeypatch, [visual([3])])
    record, _ = await _one_run(tmp_path)
    assert [c[3]["row"] for c in calls] == [3]


async def test_failed_clicks_leave_the_score_as_a_run_without_clicks(tmp_path, monkeypatch):
    fake_clicks(monkeypatch, [visual([0, 1], drillable=False)])
    plain, _ = await _one_run(tmp_path / "a")
    fake_clicks(monkeypatch, [visual([0, 1])], click_failure="transport")
    clicked, _ = await _one_run(tmp_path / "b")
    assert [c["failure"] for c in clicked["drill_downs"]] == ["transport", "transport"]
    assert [c["sql"] for c in clicked["drill_downs"]] == [[], []]
    assert clicked["score"] == plain["score"]


def test_drill_summary_counts_model_calls_and_failures():
    records = [{"visuals": [], "drill_downs": [
        {"click": "first", "row": 0, "wall_s": 0.2, "failure": "none", "no_model": True},
        {"click": "last", "row": 1, "wall_s": 6.0, "failure": "none", "no_model": False},
        {"click": "first", "row": 2, "wall_s": 0.1, "failure": "action_failed", "no_model": False},
    ]}]
    d = sc.drill_summary(records)
    assert (d["clicks"], d["click_model"], d["click_failed"]) == (3, 1, 1)
    assert d["click_median"] == pytest.approx(3.1) and d["rate"] is None
    assert (d["first_median"], d["last_median"]) == (0.2, 6.0)
