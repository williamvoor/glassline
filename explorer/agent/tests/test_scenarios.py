"""Tests for scripts/scenarios.py (explorer/specs/scenarios/SPEC.md). No model, network or database."""

import json
from pathlib import Path

import httpx
import pytest

import effort_check as ec
import scenarios as sc
from explorer_agent.llm import CONTENT_POLICY_PROSE, DRILLS_DROPPED_LOG
from explorer_agent.sql import SqlFail, SqlOk

AUSTENDER_SCENARIOS = Path(__file__).resolve().parent / "fixtures" / "austender" / "scenarios.toml"


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
    scenarios = sc.load(AUSTENDER_SCENARIOS, {"explorer"})
    typed = [s for s in scenarios if s.set == "typed"]
    board = [s for s in scenarios if s.set == "board"]
    assert len(typed) == 10 and all(s.reviewed for s in typed)
    assert {s.id for s in scenarios if s.smoke} == {"write-request"}
    assert all(s.reference or s.expect_empty or s.smoke for s in scenarios)
    # BS9: 9 Board scenarios, 3 of each kind, each with a wrong entry for the filter it could lose
    assert sorted(s.kind for s in board) == ["a"] * 3 + ["b"] * 3 + ["c"] * 3
    assert all(s.reference and s.wrong for s in board)
    assert all("limited" not in v.casefold() for s in board for _, v in s.controls)



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
        sc.main(["boards", "--workspace", "aus", "--runs", "0"])


# ADR 0048: the scenario tools name a Workspace; there is no DATASET_DIR to fall back on.
@pytest.mark.parametrize("cmd", ["run", "boards"])
def test_scenario_tools_require_a_workspace(cmd, capsys):
    with pytest.raises(SystemExit):
        sc.main([cmd])
    assert "--workspace" in capsys.readouterr().err


def test_boards_explorer_cold_is_its_own_measure():
    rec = {"explorer_cold_ms": 1200, "clicks": [{"wall_s": 0.1, "failure": "none", "prefetched": False}]}
    samples = sc.board_samples([rec])
    assert samples["Explorer cold"] == ([1.2], 0) and samples["Entity Board, not prefetched"] == ([0.1], 0)


# --- BS: scored Board scenarios and the model A/B (amendment 2026-11-04, SPEC §21) --------------------


def treemap(leaves, *, source="v1", boards=(0,)):
    values = [{"_id": "root", "_parent": None}] + [{"_id": f"t{i}", "_parent": "root", **leaf} for i, leaf in enumerate(leaves)]
    return {"id": "breakdown__2", "component": "VegaChart",
            "spec": {"data": [{"name": "tree", "values": values, "transform": [{"type": "stratify"}, {"type": "treemap"}]},
                              {"name": "tiles", "source": "tree"}]},
            "action": {"source": source, "rows": [lf["_row"] for lf in leaves if "_row" in lf], "boards": list(boards)}}


def grid(cid, rows, *, source, boards):
    return {"id": cid, "component": "DataGrid", "columns": [], "rows": rows,
            "action": {"source": source, "rows": list(range(len(rows))), "boards": list(boards)}}


TOP_ROWS = [["THALES AUSTRALIA", "$1B", "3"], [r"ACME \* Co", "$2B", "3"]]


def explorer_components(*, extra=(), year_label="Year", pm_options=("open", "selective")):
    pm = [{"label": "All", "value": "all"}] + [{"label": o, "value": str(i)} for i, o in enumerate(pm_options)]
    return [
        {"id": "board_control_pm", "component": "ChoicePicker", "label": "Procurement method", "options": pm},
        {"id": "board_control_year", "component": "ChoicePicker", "label": year_label,
         "options": [{"label": "All", "value": "all"}, {"label": "2023-24", "value": "0"}]},
        {"id": "board_control_on", "component": "CheckBox", "label": "Only open"},
        {"id": "breakdown", "component": "Card", "child": "breakdown__1"},
        treemap([{"_name": "Department of Defence", "_row": 0}, {"_name": "Home Affairs", "_row": 1}, {"_name": "Other"}]),
        grid("top__2", TOP_ROWS, source="v2", boards=(0, 1)),
        {"id": "trend__2", "component": "VegaChart", "spec": {"data": {"values": [{"_row": 0}]}},
         "action": {"source": "v3", "rows": [0], "drillable": True}},
        *extra,
    ]


def board_reply(components, *, surface="s0", tab="t1", instance="b1", controls=None, ctx="ctx-1", failure="none",
                metadata=None):
    controls = controls if controls is not None else [
        {"name": "pm", "kind": "choice"}, {"name": "year", "kind": "choice"}, {"name": "on", "kind": "toggle"}]
    a2ui = [{"updateComponents": {"surfaceId": surface, "components": components}},
            {"updateDataModel": {"surfaceId": surface, "path": "/controls", "value": {"pm": ["all"], "year": ["all"], "on": False}}}]
    return {"wall_s": 0.2, "failure": failure, "prose": "", "context_id": ctx,
            "metadata": {"explorerLevel": {"surfaceId": surface}, "explorerModel": None, "explorerTab": {"id": tab, "title": "x"},
                         "explorerBoard": {"id": instance, "controls": controls}, **(metadata or {})},
            "surface": {"a2ui": a2ui}}


ENTITY = [grid("top__2", [["THALES AUSTRALIA", "$1B", "3"], ["BAE", "$2B", "4"]], source="v9", boards=(0, 1))]


def fake_board_scenarios(monkeypatch, *, open_reply_=None, controls_reply=None, drill_failure="none",
                         answer_metrics=(("Total", "$1B"),), log_lines=()):
    """Fakes the agent for Board scenarios: `open_board`, `controls`, `drill`, `ask` and typed asks."""
    sent: list[tuple] = []
    settles: list[int] = []

    def agent_log(line):
        with FakeAgent.instances[-1].log_path.open("a") as f:
            f.write(line + "\n")

    def answer(context_id, tab):
        for line in log_lines:
            agent_log(line)
        t = turn(prose="It won $1B.", metrics=answer_metrics, sql=["SELECT 1"])
        return {**t, "wall_s": 3.5, "repairs": 0, "statuses": [], "context_id": context_id,
                "metadata": {"explorerModel": "meta/served", "explorerTab": {"id": tab}}}

    async def fake_exchange(client, body):
        message = body["params"]["message"]
        sent.append(("open_board", message.get("contextId"), message["parts"][0]["data"]["action"]["context"]))
        return open_reply_ or board_reply(explorer_components())

    async def fake_act(client, name, surface_id, component_id, context, context_id, viewing=None):
        sent.append((name, surface_id, component_id, context, context_id, viewing))
        if name == "controls":
            if controls_reply:
                return controls_reply
            return {"wall_s": 0.4, "failure": "none", "prose": "", "context_id": context_id, "metadata": {},
                    "surface": {"a2ui": []}}
        if name == "drill":
            r = board_reply(ENTITY, surface="s1", tab="t2", instance="b2", controls=[], failure=drill_failure)
            return {**r, "context_id": context_id}
        return answer(context_id, "t9")  # an Ask: a new tab

    async def fake_ask(client, text, context_id=None, viewing=None):
        sent.append(("typed", text, context_id, viewing))
        return answer(context_id or "ctx-w", "t8")

    async def counting_settle(log_path):
        settles.append(len(sent))

    monkeypatch.setattr(ec, "_exchange", fake_exchange)
    monkeypatch.setattr(ec, "act", fake_act)
    monkeypatch.setattr(ec, "ask", fake_ask)
    monkeypatch.setattr(ec, "Agent", FakeAgent)
    monkeypatch.setattr(ec, "LOG_SETTLE_S", 0)
    monkeypatch.setattr(ec, "secret_values", lambda *a: ["sekrit-value"])
    monkeypatch.setattr(sc, "DRILL_SETTLE_S", 0)
    monkeypatch.setattr(sc, "settle_prefetch", counting_settle)
    return sent, settles


def board_scenario(**raw):
    base = {"id": "bs", "reviewed": True, "board": "explorer", "visual": ["metric"],
            "reference": ref(expect=("v",))}
    if "ask_within" not in raw:
        base["turns"] = ["How much here?"]
    return sc.parse_scenario({**base, **raw})


async def run_board(tmp_path, s, *, runner_rows=({"v": "$1B"},), commit=None):
    runner = StubRunner({"SELECT 1": ok(list(runner_rows))})
    jsonl = await sc.run([s], 1, "minimal", tmp_path, runner, ("mart",), model="meta/m", commit=commit)
    return sc.read_records(jsonl)


BOARD_FILE = '''
[[scenario]]
id = "a"
board = "explorer"
controls = { "Procurement method" = "open" }
turns = ["q"]
expect_empty = true

[[scenario]]
id = "b"
board = "explorer"
click = [{ visual = "breakdown", row = "Department of Defence" }]
turns = ["q"]
expect_empty = true

[[scenario]]
id = "c"
board = "explorer"
click = [{ visual = "breakdown", row = "Department of Defence" }]
ask_within = { visual = "top", row = "THALES AUSTRALIA" }
expect_empty = true

[[scenario]]
id = "t"
turns = ["q"]
expect_empty = true
'''


def test_board_scenario_load(tmp_path):
    a, b, c, t = sc.load(write(tmp_path, BOARD_FILE), {"explorer"})
    assert [s.kind for s in (a, b, c, t)] == ["a", "b", "c", None]
    assert [s.set for s in (a, b, c, t)] == ["board", "board", "board", "typed"]
    assert a.controls == (("Procurement method", "open"),)
    assert b.click == (("breakdown", "Department of Defence"),) and b.turns == ("q",)
    assert c.ask_within == ("top", "THALES AUSTRALIA") and c.turns == ()


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"click": [{"visual": "top", "row": "x", "col": 1}]}, "unknown key"),
        ({"click": [{"visual": "top"}]}, "needs a visual and a row"),
        ({"board": "", "controls": {"a": "b"}}, "controls need"),
        ({"board": "", "click": [{"visual": "top", "row": "x"}]}, "click need"),
        ({"board": "", "turns": None, "ask_within": {"visual": "top", "row": "x"}}, "ask_within need"),
        ({"ask_within": {"visual": "top", "row": "x"}}, "not both"),
        ({"turns": None}, "turns must be"),
        ({"board": "agency"}, "no Board without a key"),
        ({"controls": {"a": 1}}, "controls must map"),
    ],
)
def test_board_scenario_rejects(raw, message):
    full = {"id": "s", "turns": ["q"], "board": "explorer", "expect_empty": True, **raw}
    full = {k: v for k, v in full.items() if v is not None}
    with pytest.raises(sc.ScenarioError, match=message):
        sc.parse_scenario(full, {"explorer"})


class _Board:
    def __init__(self, name, key=""):
        self.name, self.key = name, key


def fake_main_run(monkeypatch, tmp_path, scenarios_text):
    """`_main_run` with its settings, pool and `run` stubbed; returns what `run` was called with."""
    import types
    dataset = types.SimpleNamespace(name="ds", allowed_schemas={"mart"},
                                    boards={"explorer": _Board("explorer"), "agency": _Board("agency", "agency_key")})
    settings = types.SimpleNamespace(dataset=dataset, dataset_dir=tmp_path, model="meta/configured",
                                     database_url="x", sql_timeout_ms=1, sql_row_cap=1)
    (tmp_path / "scenarios.toml").write_text(scenarios_text)

    class Pool:
        async def open(self, **k): ...
        async def close(self): ...
    called = {}

    async def fake_run(chosen, runs, level, out_dir, runner, allowed, **kw):
        called.update(chosen=chosen, **kw)
    monkeypatch.setattr(sc, "load_dotenv", lambda *a, **k: None)
    def fake_settings(env, name, *roots):
        called.setdefault("workspace", name)
        return settings
    monkeypatch.setattr(sc, "named_workspace_settings", fake_settings)
    monkeypatch.setattr(sc, "open_pool", lambda url: Pool())
    monkeypatch.setattr(sc, "run", fake_run)
    monkeypatch.setattr(sc, "repo_commit", lambda paths: {"sha": "s", "dirty": False, "paths": paths})
    return called


def test_main_run_passes_model_commit_and_keyless_boards(tmp_path, monkeypatch):
    called = fake_main_run(monkeypatch, tmp_path, BOARD_FILE)
    sc.main(["run", "--workspace", "aus", "--include-unreviewed"])
    assert called["workspace"] == "aus"
    assert called["model"] == "meta/configured"
    assert called["commit"]["paths"] == [ec.AGENT_DIR, tmp_path]
    assert [s.id for s in called["chosen"]] == ["a", "b", "c", "t"]


def test_board_scenario_rejected_before_the_agent_starts(tmp_path, monkeypatch):
    called = fake_main_run(monkeypatch, tmp_path, BOARD_FILE.replace('board = "explorer"\ncontrols', 'board = "agency"\ncontrols'))
    with pytest.raises(SystemExit, match="no Board without a key named 'agency'"):  # agency has a key
        sc.main(["run", "--workspace", "aus", "--include-unreviewed"])
    assert called == {"workspace": "aus"}  # `run` (references, then the agent) never started


def test_repo_commit_sees_modified_and_untracked_files(tmp_path):
    import subprocess

    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "agent").mkdir()
    (tmp_path / "other").mkdir()
    (tmp_path / "agent" / "a.py").write_text("1")
    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init")
    paths = [tmp_path / "agent"]
    clean = sc.repo_commit(paths, tmp_path)
    assert len(clean["sha"]) == 40 and clean["dirty"] is False
    (tmp_path / "other" / "x.txt").write_text("elsewhere")  # outside the paths: still clean
    assert sc.repo_commit(paths, tmp_path)["dirty"] is False
    (tmp_path / "agent" / "new.py").write_text("untracked")
    assert sc.repo_commit(paths, tmp_path)["dirty"] is True
    (tmp_path / "agent" / "new.py").unlink()
    (tmp_path / "agent" / "a.py").write_text("2")
    assert sc.repo_commit(paths, tmp_path)["dirty"] is True


async def test_run_stops_when_the_agent_dies_in_setup(tmp_path, monkeypatch):
    fake_board_scenarios(monkeypatch, open_reply_=board_reply([], failure="transport"))

    class Dead:
        def poll(self):
            return 1
    original = FakeAgent.start

    async def start(self):
        await original(self)
        self.proc = Dead()
    monkeypatch.setattr(FakeAgent, "start", start)
    with pytest.raises(SystemExit, match="the agent exited"):
        await run_board(tmp_path, board_scenario())


async def test_board_setup_sequence(tmp_path, monkeypatch):
    sent, settles = fake_board_scenarios(monkeypatch)
    s = board_scenario(controls={" procurement METHOD ": "Open", "Year": "all"},
                       click=[{"visual": "breakdown", "row": "department of defence"}])
    (record,) = await run_board(tmp_path, s)
    assert sent[1] == ("open_board", None, {"board": "explorer"})  # sent[0] is the warm-up
    assert sent[2] == ("controls", "s0", "board_controls", {"board": "b1", "seq": 1, "values": {"pm": 0, "year": None, "on": False}}, "ctx-1", None)
    assert sent[3] == ("controls", "s0", "board_controls", {"board": "b1", "seq": 2, "values": {"pm": 0, "year": None, "on": False}}, "ctx-1", None)
    assert sent[4] == ("drill", "s0", "breakdown__2", {"source": "v1", "row": 0}, "ctx-1", None)
    assert settles == [4]  # the prefetch settled just before the click
    assert sent[5] == ("typed", "How much here?", "ctx-1", "t2")  # the Entity Board is now in view
    assert [st["step"] for st in record["setup"]["steps"]] == ["open_board", "controls  procurement METHOD ", "controls Year", "click breakdown"]
    assert record["score"]["status"] == "pass"


async def test_board_setup_merges_the_controls_reply(tmp_path, monkeypatch):
    requeried = [grid("top__4", [[r"ACME \* Co", "$2B", "3"], ["THALES AUSTRALIA", "$1B", "3"]], source="v5", boards=(0, 1))]
    reply = {"wall_s": 0.4, "failure": "none", "prose": "", "context_id": "ctx-1", "metadata": {},
             "surface": {"a2ui": [{"updateComponents": {"surfaceId": "s0", "components": requeried}}]}}
    sent, _ = fake_board_scenarios(monkeypatch, controls_reply=reply)
    s = board_scenario(controls={"Procurement method": "open"}, click=[{"visual": "top", "row": "THALES AUSTRALIA"}])
    (record,) = await run_board(tmp_path, s)
    # the old top__2 (source v2, Thales at 0) is gone; the click uses the new source and row
    assert sent[3] == ("drill", "s0", "top__4", {"source": "v5", "row": 1}, "ctx-1", None)
    assert record["score"]["status"] == "pass"

    superseded = {**reply, "metadata": {"explorerSuperseded": True}}
    fake_board_scenarios(monkeypatch, controls_reply=superseded)
    (record,) = await run_board(tmp_path / "b", s)
    assert record["score"]["status"] == "setup_failed" and "superseded" in record["score"]["reasons"][0]


@pytest.mark.parametrize(
    ("visual", "row", "component", "index"),
    [
        ("top", "acme * co", "top__2", 1),  # unescaped, case-folded
        ("top", "  THALES australia ", "top__2", 0),  # trimmed
        ("top", r"ACME \* Co", "top__2", 1),  # as escaped in the cell, too
        ("breakdown", "Home Affairs", "breakdown__2", 1),  # a Treemap sends `_row`
    ],
)
def test_board_row_matching(visual, row, component, index):
    model = sc.read_board(board_reply(explorer_components()))
    comp, i = sc.find_row(model, visual, row)
    assert (comp["id"], i) == (component, index)


def test_board_row_matching_treemap_sends_row_not_position():
    leaves = [{"_name": "B", "_row": 7}, {"_name": "A", "_row": 3}]
    model = sc.read_board(board_reply([treemap(leaves, boards=(3, 7))]))
    assert sc.find_row(model, "breakdown", "a")[1] == 3


@pytest.mark.parametrize(
    ("raw", "kwargs", "reason"),
    [
        ({}, {"open_reply_": board_reply([], failure="action_failed")}, "open_board failed"),
        ({"controls": {"Nope": "open"}}, {}, "Control 'Nope' matches nothing"),
        ({"controls": {"Procurement method": "open"}},  # two Controls with the same label
         {"open_reply_": board_reply(explorer_components(year_label="procurement method"))},
         "Control 'Procurement method' matches 2"),
        ({"controls": {"Procurement method": "limited"}}, {}, "option 'limited' of 'Procurement method' matches nothing"),
        ({"controls": {"Procurement method": "open"}},
         {"open_reply_": board_reply(explorer_components(pm_options=("open", "OPEN")))}, "matches 2"),
        ({"controls": {"Only open": "yes"}}, {}, "only choice Controls"),
        ({"click": [{"visual": "nope", "row": "x"}]}, {}, "clickable Visual 'nope' matches nothing"),
        ({"click": [{"visual": "top", "row": "x"}]},
         {"open_reply_": board_reply(explorer_components(extra=[grid("top__7", [["x"]], source="v7", boards=(0,))]))},
         "clickable Visual 'top' matches 2"),
        ({"click": [{"visual": "trend", "row": "x"}]}, {}, "only DataGrid and Treemap"),
        ({"click": [{"visual": "top", "row": "Nobody"}]}, {}, "row 'Nobody' in 'top' matches nothing"),
        ({"click": [{"visual": "top", "row": "3"}]}, {}, "row '3' in 'top' matches 2"),
        ({"click": [{"visual": "breakdown", "row": "Home Affairs"}]}, {}, "doesn't open a Board"),
        ({"click": [{"visual": "breakdown", "row": "Other"}]}, {}, "isn't clickable"),
        ({"controls": {"Procurement method": "open"}},
         {"controls_reply": {"wall_s": 0.1, "failure": "transport", "prose": "", "metadata": {}, "surface": {"a2ui": []}}},
         "controls Procurement method failed"),
        ({"click": [{"visual": "breakdown", "row": "Department of Defence"}]}, {"drill_failure": "action_failed"},
         "click breakdown failed"),
        ({"turns": None, "ask_within": {"visual": "top", "row": "Nobody"}}, {}, "row 'Nobody' in 'top' matches nothing"),
        ({"turns": None, "ask_within": {"visual": "top", "row": "THALES AUSTRALIA"}},
         {"open_reply_": board_reply(explorer_components()[:5] + [grid("top__2", TOP_ROWS, source="v2", boards=(1,))])},
         "doesn't open a Board"),
        ({}, {"open_reply_": board_reply(explorer_components(), metadata={"explorerTab": {}})}, "no tab"),
    ],
)
async def test_board_setup_failed(tmp_path, monkeypatch, raw, kwargs, reason):
    sent, _ = fake_board_scenarios(monkeypatch, **kwargs)
    s = board_scenario(**{k: v for k, v in raw.items() if v is not None})
    if "turns" in raw:
        s = sc.parse_scenario({"id": "bs", "reviewed": True, "board": "explorer", "reference": ref(),
                               "ask_within": raw["ask_within"]})
    (record,) = await run_board(tmp_path, s)
    sc_ = record["score"]
    assert sc_["status"] == "setup_failed" and reason in sc_["reasons"][0]
    assert sc_["matched_wrong"] == [] and sc_["flags"] == {}
    assert [x for x in sent if x[0] == "typed"][1:] == [] and not [x for x in sent if x[0] == "ask"]  # only the warm-up
    assert record["setup"]["reason"] == sc_["reasons"][0] and record["drill_downs"] == []


async def test_board_turns_view_the_board(tmp_path, monkeypatch):
    sent, _ = fake_board_scenarios(monkeypatch)
    s = board_scenario(turns=["one?", "two?"], controls={"Procurement method": "open"})
    await run_board(tmp_path, s)
    typed = [x for x in sent if x[0] == "typed"][1:]
    # each reply opens a new tab (t8); later turns still name the Board's tab
    assert typed == [("typed", "one?", "ctx-1", "t1"), ("typed", "two?", "ctx-1", "t1")]


async def test_ask_within_is_scored(tmp_path, monkeypatch):
    call = "2026-11-04 01:00:00,001 INFO explorer_agent.llm: model call: x prompt=900 cached=800 completion=50 reasoning=0 latency_ms=1200"
    sent, _ = fake_board_scenarios(monkeypatch, log_lines=[call])
    s = sc.parse_scenario({"id": "bs", "reviewed": True, "board": "explorer", "visual": ["metric"],
                           "reference": ref(expect=("v",)), "answer_in": ["metric"],
                           "click": [{"visual": "breakdown", "row": "Department of Defence"}],
                           "ask_within": {"visual": "top", "row": "bae"}})
    (record,) = await run_board(tmp_path, s)
    assert sent[3] == ("ask", "s1", "top__2", {"source": "v9", "row": 1}, "ctx-1", "t2")
    assert [x for x in sent if x[0] == "typed"][1:] == []
    assert record["score"]["status"] == "pass" and record["wall_s"] == 3.5
    assert record["model_calls"] == [{"prompt": 900, "cached": 800, "completion": 50, "reasoning": 0, "latency_ms": 1200}]
    assert record["turns"][0]["text"].startswith("ask within filters")


async def test_board_records_and_report(tmp_path, monkeypatch):
    fake_board_scenarios(monkeypatch)
    commit = {"sha": "abc", "dirty": False}
    s = board_scenario(controls={"Procurement method": "open"})
    (record,) = await run_board(tmp_path, s, commit=commit)
    assert (record["set"], record["kind"], record["reviewed"], record["model"], record["served_model"],
            record["level"], record["commit"]) == ("board", "a", True, "meta/m", "meta/served", "minimal", commit)
    assert [st["wall_s"] for st in record["setup"]["steps"]] == [0.2, 0.4] and record["setup"]["reason"] is None

    failed = {**record, "scenario": "bf", "wall_s": None, "score": sc.setup_failed_score("row 'x' matches nothing")}
    typed = {**record, "scenario": "t", "set": "typed", "wall_s": 9.0}
    typed.pop("setup")
    s_ = sc.summary([record, failed, typed])
    assert (s_["scored"], s_["setup_failed"], s_["passed"]) == (2, 1, 2)  # setup_failed is left out
    text = sc.report([record, failed, typed])
    assert "Model: meta/m; level: minimal" in text
    assert "Board set: pass rate 100% (1/1), median wall 3.5 s (2 runs, setup failed 1)" in text
    assert "Typed set: pass rate 100% (1/1), median wall 9.0 s" in text
    assert "| setup failed |" in text and "| bf | 1 | 0 | 0 | 0 | 0 | 1 |" in text


def test_report_reads_old_records():
    records = [record("a", 1, "pass", wall=4.0), record("b", 1, "fail", reasons=["missing"], wall=6.0)]
    text = sc.report(records)
    assert "Pass rate: 50% (1/2)" in text and "Model: —; level: —" in text
    assert "Typed set: pass rate 50% (1/2)" in text and "Board set: pass rate —" in text


# --- compare ------------------------------------------------------------------------------------------


def crec(sid, status, *, set_="board", wall=10.0, model="meta/a", run=1, reviewed=True, level="minimal",
         sha="abc", dirty=False, smoke=False, calls=(), repairs=0, write=False):
    return {"scenario": sid, "run": run, "set": set_, "reviewed": reviewed, "model": model, "level": level,
            "commit": {"sha": sha, "dirty": dirty}, "smoke": smoke, "wall_s": wall, "model_calls": list(calls),
            "repairs": repairs, "score": {"status": status, "reasons": [], "flags": {"write_attempted": write}}}


def side(model, *, board_pass, typed_not_pass, board_wall=10.0, typed_wall=10.0, n=10, **kw):
    out = [crec(f"b{i}", "pass" if i < board_pass else "fail", wall=board_wall, model=model, **kw) for i in range(n)]
    out += [crec(f"t{i}", "fail" if i < typed_not_pass else "pass", set_="typed", wall=typed_wall, model=model, **kw)
            for i in range(n)]
    return out


EDGE_A = dict(board_pass=5, typed_not_pass=1)
EDGE_B = dict(board_pass=6, typed_not_pass=2, board_wall=11.5, typed_wall=11.5)  # every gate exactly at its edge


def test_compare_figures():
    calls = [{"prompt": 100, "cached": 80, "completion": 10}, {"prompt": 50, "cached": None, "completion": 5}]
    a = [crec("b0", "pass", wall=2.0, calls=calls, repairs=1), crec("b1", "needs_review", wall=4.0, write=True),
         crec("t0", "pass", set_="typed", wall=1.0)]
    f = sc._by_set(a)
    assert (f["board"]["passes"], f["board"]["runs"], f["board"]["not_pass"]) == (1, 2, 1)  # needs_review isn't a pass
    assert f["board"]["rate"] == sc.Fraction(1, 2) and f["board"]["wall_median"] == 3.0 and f["board"]["wall_p90"] == 4.0
    assert (f["board"]["calls_mean"], f["board"]["repairs"], f["board"]["write_attempted"]) == (1, 1, 1)
    assert (f["board"]["prompt_mean"], f["board"]["cached_mean"], f["board"]["output_mean"]) == (75, 40, 7.5)
    assert f["typed"]["runs"] == 1


@pytest.mark.parametrize(
    ("b_changes", "failed"),
    [
        ({}, []),  # exactly +10 points, +1 not a pass, 1.15×: B wins
        ({"board_pass": 5}, ["1: Board-set pass rate"]),
        ({"typed_not_pass": 3}, ["2: typed-set runs"]),
        ({"board_wall": 11.51}, ["3: board-set median"]),
        ({"typed_wall": 11.51}, ["3: typed-set median"]),
    ],
)
def test_compare_gate(b_changes, failed):
    result = sc.compare(side("meta/a", **EDGE_A), side("meta/b", **{**EDGE_B, **b_changes}))
    assert result["invalid"] == []
    assert len(result["failed"]) == len(failed)
    assert all(x.startswith(f) for x, f in zip(result["failed"], failed, strict=True))
    assert result["verdict"] == ("A stays" if failed else "B wins")


def _invalid(a, b):
    return sc.compare(a, b)["invalid"]


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda a, b: ([{k: v for k, v in r.items() if k != "reviewed"} for r in a], b), "without reviewed"),
        (lambda a, b: ([{**a[0], "commit": None}, *a[1:]], b), "without commit"),
        (lambda a, b: ([{**a[0], "model": "meta/z"}, *a[1:]], b), "more than one model"),
        (lambda a, b: ([{**a[0], "level": "low"}, *a[1:]], b), "more than one level"),
        (lambda a, b: ([{**a[0], "commit": {"sha": "zzz", "dirty": False}}, *a[1:]], b), "more than one commit"),
        (lambda a, b: (a, [{**r, "model": "meta/a"} for r in b]), "same model"),
        (lambda a, b: (a, [{**r, "level": "low"} for r in b]), "differ in level"),
        (lambda a, b: (a, [{**r, "commit": {"sha": "def", "dirty": False}} for r in b]), "differ in commit"),
        (lambda a, b: (a, [{**r, "commit": {"sha": "abc", "dirty": True}} for r in b]), "uncommitted"),
        (lambda a, b: (a, [{**b[0], "scenario": "new"}, *b[1:]]), "different scenarios"),
        (lambda a, b: (a, [*b, {**b[0], "run": 2}]), "different number of runs"),
        (lambda a, b: (a, [{**b[0], "score": {**b[0]["score"], "status": "setup_failed"}}, *b[1:]]), "setup_failed or blocked"),
        (lambda a, b: ([{**a[0], "score": {**a[0]["score"], "status": "blocked"}}, *a[1:]], b), "setup_failed or blocked"),
        (lambda a, b: ([r for r in a if r["set"] == "typed"], [r for r in b if r["set"] == "typed"]), "no reviewed board-set"),
        (lambda a, b: ([], b), "A has no records"),
    ],
)
def test_compare_invalid(make, message):
    a, b = make(side("meta/a", **EDGE_A), side("meta/b", **EDGE_B))
    result = sc.compare(a, b)
    assert any(message in x for x in result["invalid"]), result["invalid"]
    assert result["verdict"] is None


def test_compare_filters_before_matching_ids():
    a, b = side("meta/a", **EDGE_A), side("meta/b", **EDGE_B)
    a.append(crec("only-a", "pass", model="meta/a", reviewed=False))  # not in B: unreviewed, so filtered out
    b.append(crec("blocked-u", "blocked", model="meta/b", reviewed=False))
    b.append(crec("setup-u", "setup_failed", model="meta/b", reviewed=False))
    a.append(crec("smoke", "pass", model="meta/a", smoke=True))
    assert _invalid(a, b) == []


def test_compare_close_call():
    edge = sc.compare(side("meta/a", **EDGE_A), side("meta/b", **EDGE_B))
    assert edge["verdict"] == "B wins" and any("flipping" in x for x in edge["close"])
    assert any("1.15×" in x for x in edge["close"])
    # a win by 2 runs on gates 1 and 2 at 1.05× is clear
    clear = sc.compare(side("meta/a", board_pass=5, typed_not_pass=1),
                       side("meta/b", board_pass=7, typed_not_pass=0, board_wall=10.5, typed_wall=10.5))
    assert clear["verdict"] == "B wins" and clear["close"] == []
    for ratio, close in ((1.10, True), (1.20, True), (1.09, False), (1.21, False)):
        r = sc.compare(side("meta/a", board_pass=5, typed_not_pass=1),
                       side("meta/b", board_pass=7, typed_not_pass=0, board_wall=10 * ratio, typed_wall=10.5))
        assert bool(r["close"]) is close, (ratio, r["close"])


def test_compare_output(tmp_path, monkeypatch):
    monkeypatch.setattr(ec, "Agent", lambda *a: pytest.fail("compare must not start an agent"))
    monkeypatch.setattr(ec, "ask", lambda *a, **k: pytest.fail("compare must not ask"))
    a_path, b_path = tmp_path / "runs-A.jsonl", tmp_path / "runs-B.jsonl"
    for path, records in ((a_path, side("meta/a", **EDGE_A)), (b_path, side("meta/b", **EDGE_B))):
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
    sc.main(["compare", str(a_path), str(b_path)])
    out = tmp_path / "compare-runs-A-vs-runs-B.md"
    text = out.read_text()
    assert "**Verdict: B wins** (A meta/a, B meta/b); close: rerun at 5" in text
    assert "| board | A | 5 | 10 | 50% |" in text
    sc.main(["compare", str(a_path), str(b_path), "--out", str(tmp_path / "o")])
    assert (tmp_path / "o" / "compare-runs-A-vs-runs-B.md").exists()
    invalid = sc.compare_report(sc.compare(side("meta/a", **EDGE_A), side("meta/a", **EDGE_B)), "a", "b")
    assert "**Verdict: invalid**" in invalid and "same model" in invalid
