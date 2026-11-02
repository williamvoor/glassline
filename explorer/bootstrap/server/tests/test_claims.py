"""Claim verification states and export blockers (SPEC.md phase 2b: C1, C2)."""

import pytest

from explorer_agent.sql import SqlRunner

from explorer_bootstrap.claims import export_blockers, verify_claim, verify_claims

pytestmark = pytest.mark.db


@pytest.fixture
def runner(ro_pool):
    return SqlRunner(ro_pool, ["bs"], 2000, row_cap=5)


def scalar(sql, value, abs_tol=0, rel_tol=0):
    return {"text": "t", "sql": sql, "expect": {"kind": "scalar", "value": value, "abs_tol": abs_tol, "rel_tol": rel_tol}}


def a_set(sql, values):
    return {"text": "t", "sql": sql, "expect": {"kind": "set", "values": values}}


def none(sql=""):
    return {"text": "t", "sql": sql, "expect": {"kind": "none"}}


# C1
@pytest.mark.parametrize(
    ("claim", "state", "message"),
    [
        (scalar("SELECT count(*) FROM bs.mixed", 100), "verified", "within"),
        (scalar("SELECT count(*) FROM bs.mixed", 98, abs_tol=2), "verified", "within"),
        (scalar("SELECT count(*) FROM bs.mixed", 105, rel_tol=0.05), "verified", "within"),
        (scalar("SELECT count(*) FROM bs.mixed", 97, abs_tol=2), "failed", "differs"),
        (scalar("SELECT avg(amount) FROM bs.mixed", 75.75, abs_tol=0.001), "verified", "within"),
        (scalar("SELECT count(*), 1 FROM bs.mixed", 100), "error", "1 row x 1 column"),
        (scalar("SELECT id FROM bs.mixed", 1), "error", "truncated"),  # 100 rows > cap 5
        (scalar("SELECT max(name) FROM bs.mixed", 1), "error", "not a finite number"),
        (scalar("SELECT NULL::int FROM bs.mixed LIMIT 1", 1), "error", "not a finite number"),
        (scalar("SELECT ratio FROM bs.mixed WHERE id = 100", 1), "error", "not a finite number"),  # NaN
        (scalar("SELECT true FROM bs.mixed LIMIT 1", 1), "error", "not a finite number"),
        (scalar("SELECT count(*) FROM bs.nope", 1), "error", "SQL failed"),
        (scalar("SELECT 1 FROM generate_series(1, 9)", 1), "error", "truncated"),  # 9 rows > cap 5
        (scalar("DELETE FROM bs.mixed", 1), "error", "SQL failed"),
        (scalar("SELECT count(*) FROM mart.contract", 1), "error", "SQL failed"),  # schema not chosen
        (a_set("SELECT DISTINCT name FROM bs.mixed WHERE name IS NOT NULL", ["b", "a"]), "verified", "match"),
        (a_set("SELECT DISTINCT name FROM bs.mixed WHERE name IS NOT NULL", ["a", "c"]), "failed",
         "missing 'c'; extra 'b'"),
        (a_set("SELECT DISTINCT name, id FROM bs.mixed", ["a"]), "error", "expected 1 column"),
        (a_set("SELECT DISTINCT grade FROM bs.mixed", ["1"]), "error", "truncated"),  # 7 rows > cap 5
        (a_set("SELECT DISTINCT grade FROM bs.mixed WHERE grade < 3", ["1", "2"]), "verified", "match"),  # ints as text
        (none(), "needs_review", "no SQL"),
        (none("SELECT count(*) FROM bs.mixed"), "needs_review", "review the result"),
        (none("SELECT broken"), "error", "SQL failed"),
    ],
)
async def test_claim_states(runner, claim, state, message):
    r = await verify_claim(runner, claim)
    assert r["state"] == state, r
    assert message in r["message"]


async def test_a_failing_claim_does_not_stop_the_rest(runner):
    out = await verify_claims(runner, [scalar("SELECT broken", 1), scalar("SELECT count(*) FROM bs.mixed", 100)])
    assert [c["result"]["state"] for c in out] == ["error", "verified"]
    assert out[1]["result"]["actual"] == {"columns": ["count"], "rows": [{"count": 100}]}


# C2
def test_export_blockers():
    def c(state, dismissed=False):
        return {"text": state or "unchecked", "dismissed": dismissed,
                "result": {"state": state} if state else None}

    claims = [c("verified"), c("needs_review"), c("failed"), c("error"), c("failed", True), c("error", True), c(None)]
    blockers = export_blockers(claims)
    assert blockers == ["claim 3 is failed: failed", "claim 4 is error: error", "claim 7 is not checked: unchecked"]
