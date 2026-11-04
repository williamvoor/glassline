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


# --- SC1: Board scenarios (specs/scenarios/SPEC.md amendment 2026-11-04) -----------------------

LOG_TS = "2026-11-04 01:00:00,001 INFO explorer_agent.boards: "
TITLES = {"Explorer": "explorer", "Beta": "beta", "Alpha": "alpha"}


class FakeBoardAgent:
    """Appends to the shared log as the real agent does; `warmups` scripts each start's warm-up."""
    instances: list = []
    warmups: list = []

    def __init__(self, level, log_path):
        self.log_path, self.url, self.proc, self.stopped = log_path, "http://agent.test", None, False
        FakeBoardAgent.instances.append(self)

    def log(self, line):
        with self.log_path.open("a") as f:
            f.write(LOG_TS + line + "\n")

    async def start(self):
        w = FakeBoardAgent.warmups.pop(0) if FakeBoardAgent.warmups else 900
        self.log("explorer: listening")
        if w == "failed":
            self.log("board warm-up failed: board=explorer")
        elif w is not None:
            self.log(f"board warm-up: board=explorer ms={w}")

    def stop(self):
        self.stopped = True


def open_reply(controls=({"name": "mode", "kind": "choice"},), visuals=None, model=None):
    comps = [{"id": "board_control_mode", "component": "ChoicePicker",
              "options": [{"label": "All", "value": "all"}, {"label": "a", "value": "0"},
                          {"label": "b", "value": "1"}, {"label": "c", "value": "2"}, {"label": "d", "value": "3"}]}]
    comps += visuals if visuals is not None else [
        {"id": "top", "component": "DataGrid", "action": {"source": "v2", "rows": [0, 1, 2], "boards": [0, 1, 2]}},
        {"id": "share", "component": "VegaChart", "action": {"source": "v1", "rows": [0, 1], "drillable": False}},
        {"id": "tm", "component": "VegaChart", "action": {"source": "v3", "rows": [4], "boards": [4]}},
    ]
    a2ui = [{"updateComponents": {"surfaceId": "turn-0-1-ab", "components": comps}},
            {"updateDataModel": {"surfaceId": "turn-0-1-ab", "path": "/controls",
                                 "value": model if model is not None else {"mode": ["all"], "on": True}}}]
    return {"wall_s": 0.2, "failure": "none", "prose": "", "context_id": "ctx-1",
            "metadata": {"explorerLevel": {"surfaceId": "turn-0-1-ab"}, "explorerModel": None,
                         "explorerBoard": {"id": "b1", "controls": list(controls)}},
            "surface": {"a2ui": a2ui}}


def fake_boards(monkeypatch, *, open_result=None, prefetched_rows=(0,), fail_click=None):
    """Fakes the agent, `open_board` and `act`. Board queries are logged for clicks on rows that
    weren't prefetched and for Control values not seen before, as the real agent's cache would."""
    sent: dict[str, list] = {"open": [], "act": []}
    FakeBoardAgent.instances = []

    async def fake_exchange(client, body):
        sent["open"].append(body)
        return open_result or open_reply()

    async def fake_act(client, name, surface_id, component_id, context, context_id):
        agent = FakeBoardAgent.instances[-1]
        sent["act"].append((name, surface_id, component_id, context, context_id))
        if name == "drill":
            board = "alpha" if context["source"] == "v3" else "beta"
            agent.log("board query: board=explorer query=noise rows=1 ms=1")  # another Board's line
            if context["row"] not in prefetched_rows:
                agent.log(f"board query: board={board} query=totals rows=1 ms=40")
            failed = fail_click == (context["source"], context["row"])
            return {"wall_s": 0.9 if context["row"] not in prefetched_rows else 0.1,
                    "failure": "action_failed" if failed else "none",
                    "metadata": {"explorerTab": {"title": f"{board.title()} · Row {context['row']}"}}}
        seen = agent.__dict__.setdefault("seen", {None})
        value = context["values"]["mode"]
        if value not in seen:
            seen.add(value)
            agent.log("board query: board=explorer query=totals rows=1 ms=500")
        return {"wall_s": 0.6 if value is not None else 0.05, "failure": "none", "metadata": {}}

    monkeypatch.setattr(ec, "Agent", FakeBoardAgent)
    monkeypatch.setattr(ec, "_exchange", fake_exchange)
    monkeypatch.setattr(ec, "act", fake_act)
    monkeypatch.setattr(ec, "secret_values", lambda *a: ["sekrit-value"])
    for name, value in (("WARMUP_TIMEOUT_S", 0.3), ("POLL_S", 0.01), ("PREFETCH_QUIET_S", 0.05),
                        ("PREFETCH_MAX_S", 0.3), ("BOARD_LOG_SETTLE_S", 0)):
        monkeypatch.setattr(sc, name, value)
    return sent


async def test_boards_fresh_agent_per_run_and_warmup(tmp_path, monkeypatch):
    fake_boards(monkeypatch)
    FakeBoardAgent.warmups = [1100, "failed", None, 950]
    records = sc.read_records(await sc.run_boards(TITLES, 4, tmp_path))
    assert len(FakeBoardAgent.instances) == 4 and all(a.stopped for a in FakeBoardAgent.instances)
    assert [r["explorer_cold_ms"] for r in records] == [1100, None, None, 950]
    assert [r["warmup_failed"] for r in records] == [False, True, True, False]
    assert records[1]["explorer_open"] is None and records[3]["explorer_open"]["failure"] == "none"


async def test_boards_open_reads_the_reply(tmp_path, monkeypatch):
    sent = fake_boards(monkeypatch)
    await sc.run_boards(TITLES, 1, tmp_path)
    (body,) = sent["open"]
    message = body["params"]["message"]
    assert "contextId" not in message
    action = message["parts"][0]["data"]["action"]
    assert action["name"] == "open_board" and action["context"] == {"board": "explorer"}

    controls = ({"name": "mode", "kind": "choice"}, {"name": "on", "kind": "toggle"})
    opened = sc.read_open(open_reply(controls=controls))
    assert opened["context_id"] == "ctx-1" and opened["surface_id"] == "turn-0-1-ab" and opened["board"] == "b1"
    assert opened["options"] == {"mode": [0, 1, 2, 3]}
    assert opened["values"] == {"mode": None, "on": True}
    assert [(v["id"], v["source"], v["boards"]) for v in opened["visuals"]] == [("top", "v2", [0, 1, 2]), ("tm", "v3", [4])]
    assert sc.read_open(open_reply(model={"mode": ["2"]}))["values"] == {"mode": 2}
    assert [sc.wire_value(k, v) for k, v in (("choice", []), ("multi", ["1", "3"]), ("date", ""), ("number", 4))] \
        == [None, [1, 3], None, 4]


async def test_boards_clicks_and_prefetch_evidence(tmp_path, monkeypatch):
    sent = fake_boards(monkeypatch, prefetched_rows=(0, 4))
    (record,) = sc.read_records(await sc.run_boards(TITLES, 1, tmp_path))
    drills = [a for a in sent["act"] if a[0] == "drill"]
    assert [(a[2], a[3]) for a in drills] == [("top", {"source": "v2", "row": 0}), ("top", {"source": "v2", "row": 2}),
                                              ("tm", {"source": "v3", "row": 4})]
    assert all(a[1] == "turn-0-1-ab" and a[4] == "ctx-1" for a in drills)
    clicks = record["clicks"]
    assert [(c["click"], c["board"], c["queries_run"], c["prefetched"]) for c in clicks] == [
        ("first", "beta", 0, True), ("last", "beta", 1, False), ("first", "alpha", 0, True)]
    assert sc.board_name("Nobody · x", TITLES) is None


async def test_boards_control_changes(tmp_path, monkeypatch):
    controls = ({"name": "mode", "kind": "choice"}, {"name": "on", "kind": "toggle"})
    sent = fake_boards(monkeypatch, open_result=open_reply(controls=controls, visuals=[]))
    (record,) = sc.read_records(await sc.run_boards(TITLES, 1, tmp_path))
    changes = [a[3] for a in sent["act"] if a[0] == "controls"]
    assert [c["seq"] for c in changes] == [1, 2, 3, 4]
    assert [c["values"] for c in changes] == [{"mode": 0, "on": True}, {"mode": 1, "on": True},
                                             {"mode": 2, "on": True}, {"mode": None, "on": True}]
    assert all(c["board"] == "b1" for c in changes)
    assert all(a[1:3] == ("turn-0-1-ab", "board_controls") for a in sent["act"] if a[0] == "controls")
    timed = [ch for ch in record["changes"] if "skipped" not in ch]
    assert [(ch["value"], ch["cached"]) for ch in timed] == [(0, False), (1, False), (2, False), (None, True)]
    assert {"control": "on", "skipped": "toggle"} in record["changes"]

    # a Control that starts at an option skips it, then returns to it
    fake_boards(monkeypatch, open_result=open_reply(visuals=[], model={"mode": ["1"]}))
    opened = sc.read_open(open_reply(visuals=[], model={"mode": ["1"]}))
    FakeBoardAgent.instances = [FakeBoardAgent("minimal", tmp_path / "x.log")]
    (tmp_path / "x.log").write_text("")
    out = await sc.control_changes(None, opened, tmp_path / "x.log")
    assert [ch["value"] for ch in out] == [0, 2, 3, 1]


def test_boards_report():
    records = [
        {"run": 1, "warmup_failed": False, "explorer_cold_ms": 1200, "explorer_open": {"wall_s": 0.2, "failure": "none"},
         "clicks": [{"wall_s": 0.1, "failure": "none", "prefetched": True},
                    {"wall_s": 0.9, "failure": "none", "prefetched": False},
                    {"wall_s": 5.0, "failure": "action_failed", "prefetched": False}],
         "changes": [{"control": "m", "wall_s": 1.4, "failure": "none", "cached": False},
                     {"control": "m", "wall_s": 0.05, "failure": "none", "cached": True},
                     {"control": "on", "skipped": "toggle"}]},
        {"run": 2, "warmup_failed": True, "explorer_cold_ms": None, "explorer_open": None, "clicks": [], "changes": []},
    ]
    samples = sc.board_samples(records)
    assert samples["Explorer cold"] == ([1.2], 0)
    assert samples["Entity Board, not prefetched"] == ([0.9], 1)
    text = sc.boards_report(records)
    assert "warm-up failed in 1" in text and "on (toggle)" in text
    assert "| Explorer warm | 1 | 0.20 s | 0.20 s | < 0.5 s | 0 | ok |" in text
    assert "| Control change, not cached | 1 | 1.40 s | 1.40 s | < 1.0 s | 0 | miss |" in text
    assert "| Entity Board, not prefetched | 1 | 0.90 s | 0.90 s | < 1.5 s | 1 | ok |" in text
    assert "| Explorer cold | 1 | 1.20 s |" in text
    assert "| Entity Board, prefetched | 1 | 0.10 s |" in text
    assert "| Explorer warm | 0 | — | — | < 0.5 s | 0 | — |" in sc.boards_report([records[1]])


def test_boards_refused_without_boards():
    with pytest.raises(SystemExit, match="no Explorer Board"):
        sc.board_titles("d", {})
    with pytest.raises(SystemExit, match="no Explorer Board"):
        sc.board_titles("d", {"beta": object()})


async def test_boards_secret_scan_and_part_way_failure(tmp_path, monkeypatch):
    fake_boards(monkeypatch)
    FakeBoardAgent.warmups = ["sekrit-value-in-log"]  # lands in the agent log: no ms, so a timeout
    with pytest.raises(SystemExit, match="secret found"):
        await sc.run_boards(TITLES, 1, tmp_path / "a")
    assert list((tmp_path / "a").glob("boards-report-*.md"))  # written, then scanned

    fake_boards(monkeypatch)
    real_act = ec.act

    async def boom_on_second_run(*a):
        if len(FakeBoardAgent.instances) == 2:
            raise RuntimeError("boom")
        return await real_act(*a)
    monkeypatch.setattr(ec, "act", boom_on_second_run)
    with pytest.raises(RuntimeError, match="boom"):
        await sc.run_boards(TITLES, 3, tmp_path / "b")
    (report_file,) = (tmp_path / "b").glob("boards-report-*.md")
    assert "Runs: 1," in report_file.read_text()  # the finished run is still reported
    assert all(a.stopped for a in FakeBoardAgent.instances)


async def test_boards_failed_actions_are_not_counted_as_prefetched_or_cached(tmp_path, monkeypatch):
    fake_boards(monkeypatch, prefetched_rows=(0, 4), fail_click=("v3", 4))
    (record,) = sc.read_records(await sc.run_boards(TITLES, 1, tmp_path))
    failed = next(c for c in record["clicks"] if c["failure"] != "none")
    assert failed["prefetched"] is None
    samples = sc.board_samples([record])
    assert samples["Entity Board, prefetched"][1] == 0 and samples["Entity Board, not prefetched"][1] == 1
    assert sc.board_samples([{"changes": [{"control": "m", "wall_s": 3, "failure": "action_failed", "cached": None}]}]
                            )["Control change, not cached"] == ([], 1)


async def test_boards_settle_before_each_click(tmp_path, monkeypatch):
    fake_boards(monkeypatch)
    settles = []

    async def counting_settle(log_path):
        settles.append(1)
    monkeypatch.setattr(sc, "settle_prefetch", counting_settle)
    (record,) = sc.read_records(await sc.run_boards(TITLES, 1, tmp_path))
    assert len(settles) == len(record["clicks"]) == 3


async def test_boards_failed_open_records_only_the_open(tmp_path, monkeypatch):
    sent = fake_boards(monkeypatch, open_result={**open_reply(), "failure": "action_failed"})
    (record,) = sc.read_records(await sc.run_boards(TITLES, 1, tmp_path))
    assert record["explorer_open"]["failure"] == "action_failed"
    assert record["clicks"] == [] and record["changes"] == [] and sent["act"] == []
    assert "| Explorer warm | 0 | — | — | < 0.5 s | 1 | — |" in sc.boards_report([record])


def test_boards_runs_must_be_positive():
    with pytest.raises(SystemExit):
        sc.main(["boards", "--runs", "0"])


def test_boards_explorer_cold_is_its_own_measure():
    rec = {"explorer_cold_ms": 1200, "clicks": [{"wall_s": 0.1, "failure": "none", "prefetched": False}]}
    samples = sc.board_samples([rec])
    assert samples["Explorer cold"] == ([1.2], 0) and samples["Entity Board, not prefetched"] == ([0.1], 0)
