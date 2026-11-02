"""Check every claim in a draft by running its SQL (SPEC.md phase 2b, "Claims").

A claim is never `verified` unless a comparison passed. States: verified, failed, error,
needs_review. Failed and error claims block export until fixed or dismissed.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any

from explorer_agent.results import to_json_safe
from explorer_agent.sql import SqlFail, SqlRunner

PREVIEW_ROWS = 20
MAX_LISTED = 10
BLOCKING = ("failed", "error")


def _result(state: str, message: str, actual: Any = None) -> dict[str, Any]:
    return {"state": state, "message": message, "actual": actual}


def _preview(columns: list[str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"columns": columns, "rows": [{k: _safe(v) for k, v in r.items()} for r in rows[:PREVIEW_ROWS]]}


def _safe(v: Any) -> Any:
    v = to_json_safe(v)
    return None if isinstance(v, float) and not math.isfinite(v) else v


def _number(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int | float | Decimal):
        f = float(v)
        return f if math.isfinite(f) else None
    return None


def as_text(v: Any) -> str:
    """How a value compares in a `set` expectation: strings as is, anything else as JSON."""
    v = to_json_safe(v)
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def _listing(values: set[str]) -> str:
    shown = sorted(values)[:MAX_LISTED]
    more = len(values) - len(shown)
    return ", ".join(repr(v) for v in shown) + (f" (+{more} more)" if more > 0 else "")


async def verify_claim(runner: SqlRunner, claim: dict[str, Any]) -> dict[str, Any]:
    expect = claim["expect"]
    kind = expect["kind"]
    sql = (claim.get("sql") or "").strip()
    if kind == "none" and not sql:
        return _result("needs_review", "no SQL; review by hand")
    outcome = await runner.run(sql)
    if isinstance(outcome, SqlFail):
        return _result("error", f"SQL failed: {outcome.message}")
    preview = _preview(outcome.columns, outcome.rows)
    if kind == "none":
        return _result("needs_review", "can't be checked mechanically; review the result", preview)
    if kind == "scalar":
        if outcome.truncated:
            return _result("error", "too many rows: the result was truncated", preview)
        if len(outcome.columns) != 1 or len(outcome.rows) != 1:
            return _result("error", f"expected 1 row x 1 column, got {len(outcome.rows)} x {len(outcome.columns)}",
                           preview)
        actual = _number(next(iter(outcome.rows[0].values())))
        if actual is None:
            return _result("error", "the result is not a finite number", preview)
        value = float(expect["value"])
        allowed = max(float(expect["abs_tol"]), float(expect["rel_tol"]) * abs(value))
        diff = abs(actual - value)
        if diff <= allowed:
            return _result("verified", f"{actual:g} is within {allowed:g} of {value:g}", preview)
        return _result("failed", f"{actual:g} differs from {value:g} by {diff:g} (allowed {allowed:g})", preview)
    # set
    if len(outcome.columns) != 1:
        return _result("error", f"expected 1 column, got {len(outcome.columns)}", preview)
    if outcome.truncated:
        return _result("error", "too many rows: the result was truncated", preview)
    col = outcome.columns[0]
    actual_set = {as_text(r[col]) for r in outcome.rows}
    expected = set(expect["values"])
    if actual_set == expected:
        return _result("verified", f"all {len(expected)} values match", preview)
    parts = []
    if expected - actual_set:
        parts.append(f"missing {_listing(expected - actual_set)}")
    if actual_set - expected:
        parts.append(f"extra {_listing(actual_set - expected)}")
    return _result("failed", "; ".join(parts), preview)


async def verify_claims(runner: SqlRunner, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every claim with a fresh `result`; one failing claim never stops the rest."""
    out = []
    for claim in claims:
        try:
            result = await verify_claim(runner, claim)
        except Exception as e:  # a claim must never take the draft down
            result = _result("error", f"could not run the claim ({type(e).__name__})")
        out.append({**claim, "result": result})
    return out


def export_blockers(claims: list[dict[str, Any]]) -> list[str]:
    """Claims that block export: failed, error or never checked, and not dismissed."""
    out = []
    for i, c in enumerate(claims):
        state = (c.get("result") or {}).get("state")
        if (state in BLOCKING or state is None) and not c.get("dismissed"):
            out.append(f"claim {i + 1} is {state or 'not checked'}: {c['text']}")
    return out
