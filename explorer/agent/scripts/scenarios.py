"""End-to-end scenarios, scored automatically (explorer/specs/scenarios/SPEC.md, ADR 0018).

    uv run python scripts/scenarios.py run [--file F] [--ids a,b] [--runs 1] [--level minimal] [--include-unreviewed]
    uv run python scripts/scenarios.py report RUNS.jsonl
    uv run python scripts/scenarios.py boards [--runs 5] [--out DIR]

`run` checks every reference query, starts the real agent, asks each scenario's turns in one
Conversation, reruns the SQL shown on the last Turn's Surface read-only, and scores it against the
reference values. It calls the real model, so it is not a test. `boards` times the Dataset's Boards
against the speed targets, with no model call. Nothing here names a dataset: the
scenarios come from the dataset's `scenarios.toml`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import statistics
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx
from dotenv import load_dotenv

import effort_check as ec
from explorer_agent.config import load_settings
from explorer_agent.macros import _DATE
from explorer_agent.sql import GuardError, SqlFail, SqlOk, SqlRunner, guard, open_pool

EXPLORER_ROOT = ec.REPO_ROOT / "explorer"
DEFAULT_OUT_ROOT = ec.REPO_ROOT / "docs" / "scenarios"
WARMUP_QUESTION = ec.WARMUP_QUESTION

VISUAL_KINDS = {"metric": "metrics", "table": "tables", "chart": "charts"}
SCENARIO_KEYS = {
    "id", "reviewed", "categories", "turns", "visual", "max_model_calls", "reference", "alternates",
    "wrong", "tolerance", "answer_in", "forbid_visual", "expect_empty", "prose_no_numbers", "smoke",
}
ENTRY_KEYS = {"sql", "expect"}
WRONG_KEYS = ENTRY_KEYS | {"label"}
TOLERANCE_KEYS = {"rel", "abs", "percent"}
ANSWER_IN = {"metric", "prose"}

_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>~$])")
# A whole number token, optionally with thousands separators, a currency symbol and a decimal part.
_NUMBER_TOKEN = re.compile(r"(?<![\w.])[$€£]?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?!\w)")


class ScenarioError(ValueError):
    pass


# --- scenario file ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    sql: str
    expect: tuple[str, ...]
    label: str = ""


@dataclass(frozen=True)
class Tolerance:
    rel: float = 0.0005
    abs: float | None = None  # None: 0.01, or 0 with percent
    percent: bool = False

    @property
    def floor(self) -> float:
        if self.abs is not None:
            return self.abs
        return 0.0 if self.percent else 0.01


@dataclass(frozen=True)
class Scenario:
    id: str
    turns: tuple[str, ...]
    reviewed: bool = False
    categories: tuple[str, ...] = ()
    visual: tuple[str, ...] = ()
    max_model_calls: int | None = None
    reference: Entry | None = None
    alternates: tuple[Entry, ...] = ()
    wrong: tuple[Entry, ...] = ()
    tolerance: dict[str, Tolerance] = field(default_factory=dict)
    answer_in: tuple[str, ...] = ()
    forbid_visual: tuple[str, ...] = ()
    expect_empty: bool = False
    prose_no_numbers: bool = False
    smoke: bool = False

    def entries(self) -> list[tuple[str, Entry]]:
        """Every query with its role: reference, alternate N, or the wrong answer's label."""
        out = [("reference", self.reference)] if self.reference else []
        out += [(f"alternate {i + 1}", a) for i, a in enumerate(self.alternates)]
        out += [(f"wrong: {w.label}", w) for w in self.wrong]
        return out

    def tol(self, column: str) -> Tolerance:
        return self.tolerance.get(column, Tolerance())


def _unknown(keys: Any, allowed: set[str], where: str) -> None:
    extra = sorted(set(keys) - allowed)
    if extra:
        raise ScenarioError(f"{where}: unknown key(s) {', '.join(extra)}")


def _entry(raw: Any, where: str, *, wrong: bool = False) -> Entry:
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: must be a table")
    _unknown(raw, WRONG_KEYS if wrong else ENTRY_KEYS, where)
    sql, expect = raw.get("sql"), raw.get("expect")
    if not isinstance(sql, str) or not sql.strip():
        raise ScenarioError(f"{where}: needs sql")
    if not isinstance(expect, list) or not expect or not all(isinstance(c, str) for c in expect):
        raise ScenarioError(f"{where}: expect must be a non-empty list of column names")
    if wrong and not raw.get("label"):
        raise ScenarioError(f"{where}: a wrong entry needs a label")
    return Entry(sql.strip(), tuple(expect), str(raw.get("label", "")))


def _kinds(raw: Any, allowed: set[str], where: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(k in allowed for k in raw):
        raise ScenarioError(f"{where}: must be a list of {', '.join(sorted(allowed))}")
    return tuple(raw)


def parse_scenario(raw: dict[str, Any]) -> Scenario:
    sid = raw.get("id")
    if not isinstance(sid, str) or not sid:
        raise ScenarioError("a scenario needs an id")
    _unknown(raw, SCENARIO_KEYS, sid)
    turns = raw.get("turns")
    if not isinstance(turns, list) or not turns or not all(isinstance(t, str) and t.strip() for t in turns):
        raise ScenarioError(f"{sid}: turns must be a non-empty list of questions")
    tolerance: dict[str, Tolerance] = {}
    for column, t in (raw.get("tolerance") or {}).items():
        if not isinstance(t, dict):
            raise ScenarioError(f"{sid}: tolerance.{column} must be a table")
        _unknown(t, TOLERANCE_KEYS, f"{sid}: tolerance.{column}")
        tolerance[column] = Tolerance(
            rel=float(t.get("rel", 0.0005)),
            abs=float(t["abs"]) if "abs" in t else None,
            percent=bool(t.get("percent", False)),
        )
    scenario = Scenario(
        id=sid,
        turns=tuple(t.strip() for t in turns),
        reviewed=bool(raw.get("reviewed", False)),
        categories=tuple(raw.get("categories", ())),
        visual=_kinds(raw.get("visual", []), set(VISUAL_KINDS), f"{sid}: visual"),
        max_model_calls=raw.get("max_model_calls"),
        reference=_entry(raw["reference"], f"{sid}: reference") if "reference" in raw else None,
        alternates=tuple(_entry(a, f"{sid}: alternate") for a in raw.get("alternates", [])),
        wrong=tuple(_entry(w, f"{sid}: wrong", wrong=True) for w in raw.get("wrong", [])),
        tolerance=tolerance,
        answer_in=_kinds(raw.get("answer_in", []), ANSWER_IN, f"{sid}: answer_in"),
        forbid_visual=_kinds(raw.get("forbid_visual", []), set(VISUAL_KINDS), f"{sid}: forbid_visual"),
        expect_empty=bool(raw.get("expect_empty", False)),
        prose_no_numbers=bool(raw.get("prose_no_numbers", False)),
        smoke=bool(raw.get("smoke", False)),
    )
    if not (scenario.reference or scenario.expect_empty or scenario.smoke):
        raise ScenarioError(f"{sid}: needs a reference, expect_empty or smoke")
    if scenario.alternates and not scenario.reference:
        raise ScenarioError(f"{sid}: alternates need a reference")
    labels = [w.label for w in scenario.wrong]
    if len(labels) != len(set(labels)):
        raise ScenarioError(f"{sid}: wrong labels must be unique")
    return scenario


def load(path: Path) -> list[Scenario]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    _unknown(data, {"scenario"}, str(path))
    scenarios = [parse_scenario(raw) for raw in data.get("scenario", [])]
    ids = [s.id for s in scenarios]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ScenarioError(f"duplicate scenario id(s): {', '.join(duplicates)}")
    return scenarios


def select(scenarios: list[Scenario], ids: list[str] | None, include_unreviewed: bool) -> list[Scenario]:
    if ids:
        unknown = sorted(set(ids) - {s.id for s in scenarios})
        if unknown:
            raise ScenarioError(f"unknown scenario id(s): {', '.join(unknown)}")
        chosen = [s for s in scenarios if s.id in ids]
    else:
        chosen = list(scenarios)
    return [s for s in chosen if s.reviewed or include_unreviewed]


# --- matching --------------------------------------------------------------------------------------


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def value_matches(expected: Any, cell: Any, tol: Tolerance) -> bool:
    """Whether an answer cell holds the reference value (spec, Behaviour step 5)."""
    if isinstance(expected, bool) or isinstance(cell, bool):
        return isinstance(expected, bool) and isinstance(cell, bool) and expected == cell
    if isinstance(expected, int):
        return _is_number(cell) and cell == expected
    if isinstance(expected, float):
        if not _is_number(cell) or not math.isfinite(cell):
            return False
        candidates = [cell]
        # A stray count must not match a share: whole numbers (660, or 660.0 from a numeric
        # column) are never scaled.
        if tol.percent and not (isinstance(cell, int) or float(cell).is_integer()):
            candidates += [cell * 100, cell / 100]
        allowed = max(tol.floor, tol.rel * abs(expected))
        return any(abs(c - expected) <= allowed for c in candidates)
    if isinstance(expected, str):
        return isinstance(cell, str) and cell.strip().casefold() == expected.strip().casefold()
    return cell == expected


def missing_values(
    scenario: Scenario, entry: Entry, expected_rows: list[dict[str, Any]], answer_rows: list[dict[str, Any]]
) -> list[tuple[str, Any]]:
    """The (column, value) pairs of `entry` that no answer cell holds. Extra rows are free."""
    cells = [v for row in answer_rows for v in row.values()]
    missing = []
    for row in expected_rows:
        for column in entry.expect:
            value = row[column]
            if not any(value_matches(value, c, scenario.tol(column)) for c in cells):
                missing.append((column, value))
    return missing


def unescape(prose: str) -> str:
    """The prose as the user reads it: filled Cite values carry markdown escapes (`3\\.4`)."""
    return _MD_ESCAPE.sub(r"\1", prose)


def stated(value: Any, texts: list[str]) -> bool:
    """Whether an integer or text value is written in any of `texts` (spec, `answer_in`)."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return True  # only integers and text are checked
    for text in texts:
        if isinstance(value, int):
            for m in _NUMBER_TOKEN.finditer(_DATE.sub(" ", text)):  # "25" in FY2024-25 isn't 25
                fraction = m.group(2) or ""
                if fraction.strip(".0") == "" and int(m.group(1).replace(",", "")) == value:
                    return True
        elif re.search(rf"(?<!\w){re.escape(value.strip().casefold())}(?!\w)", text.casefold()):
            return True
    return False


def invented_numbers(typed: list[str]) -> list[str]:
    """Numbers the model typed itself, other than zeros (spec, `prose_no_numbers`). `typed` is what
    the agent logged as bare numbers: dates and years are already left out, and values filled
    from Results through Cite never appear in it."""
    out = []
    for n in typed:
        try:
            if float(n.replace(",", "")) == 0:
                continue
        except ValueError:
            pass
        out.append(n)
    return out


# --- answer rows and scoring -----------------------------------------------------------------------


class Runner(Protocol):
    async def run(self, sql: str) -> SqlOk | SqlFail: ...


async def answer_rows(surface: dict[str, Any], runner: Runner) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reruns each distinct SQL from the Surface's SqlDetails. Returns (rows, one note per query)."""
    rows: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    for sql in dict.fromkeys(q.get("sql", "") for q in surface.get("sql", []) if q.get("sql")):
        result = await runner.run(sql)
        if isinstance(result, SqlOk):
            rows += result.rows
            notes.append({"sql": sql, "ok": True, "rows": len(result.rows), "truncated": result.truncated})
        else:
            notes.append({"sql": sql, "ok": False, "error": result.message})
    return rows, notes


def write_attempted(sql_attempts: list[dict[str, Any]], allowed_schemas: tuple[str, ...]) -> bool:
    """Any SQL the agent tried that its own guard rejects as not a single SELECT."""
    for attempt in sql_attempts:
        try:
            guard(attempt.get("sql", ""), allowed_schemas)
        except GuardError:
            return True
    return False


def score(
    scenario: Scenario,
    references: dict[str, list[dict[str, Any]]],
    turn: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    write_attempt: bool = False,
    notes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The last Turn's status, fail reasons, matched wrong answers and flags (spec, step 5)."""
    surface = turn.get("surface") or {}
    prose = unescape(turn.get("prose") or "")
    model_calls = len(turn.get("model_calls") or [])
    flags = {
        "over_max_model_calls": scenario.max_model_calls is not None and model_calls > scenario.max_model_calls,
        "write_attempted": write_attempt,
        "answer_truncated": any(n.get("truncated") for n in notes or []),
    }
    matched_wrong = [
        w.label for role, w in scenario.entries() if role.startswith("wrong")
        and not missing_values(scenario, w, references[role], rows)
    ]
    out: dict[str, Any] = {"reasons": [], "review": [], "missing": [], "not_stated": [], "invented": [],
                           "matched_wrong": matched_wrong, "flags": flags}
    reasons = out["reasons"]
    failure = turn.get("failure", "none")
    if failure == "content_policy":
        return {**out, "status": "blocked"}
    if failure != "none":
        reasons.append("failure")

    if not scenario.smoke and any(not n.get("ok") for n in notes or []):
        reasons.append("rerun_failed")  # the answer rows are incomplete, so nothing below is reliable
    if not scenario.smoke:
        present = {kind for kind, key in VISUAL_KINDS.items() if surface.get(key)}
        if scenario.visual and not present & set(scenario.visual):
            reasons.append("no_visual")
        if present & set(scenario.forbid_visual):
            reasons.append("forbidden_visual")
        if scenario.reference:
            candidates = [("reference", scenario.reference)] + [
                (f"alternate {i + 1}", a) for i, a in enumerate(scenario.alternates)
            ]
            matched = [
                (role, e) for role, e in candidates if not missing_values(scenario, e, references[role], rows)
            ]
            if not matched:
                reasons.append("missing")
                out["missing"] = missing_values(scenario, scenario.reference, references["reference"], rows)
            elif scenario.answer_in:
                texts = ([prose] if "prose" in scenario.answer_in else []) + (
                    [m[1] for m in surface.get("metrics", [])] if "metric" in scenario.answer_in else []
                )
                unstated = [
                    [(c, row[c]) for row in references[role] for c in e.expect if not stated(row[c], texts)]
                    for role, e in matched
                ]
                if all(unstated):  # every matched entry has a value not stated
                    out["not_stated"] = unstated[0]
                    reasons.append("not_stated")
        if scenario.expect_empty and any(_is_number(v) and v != 0 for row in rows for v in row.values()):
            out["review"].append("not_empty")  # context or a fuzzy match? Only a person can tell.
        prose_problem = False
        if scenario.expect_empty and not prose.strip():
            prose_problem = True
        if scenario.prose_no_numbers:
            out["invented"] = invented_numbers(turn.get("bare_numbers") or [])
            prose_problem = prose_problem or bool(out["invented"])
        if prose_problem:
            reasons.append("prose")

    if reasons:
        status = "fail"
    elif (matched_wrong or out["review"]) and not scenario.smoke:
        status = "needs_review"
    else:
        status = "pass"
    return {**out, "status": status}


# --- references ------------------------------------------------------------------------------------


async def run_references(scenarios: list[Scenario], runner: Runner) -> dict[str, dict[str, Any]]:
    """Every reference, alternate and wrong query, run once. Any failure stops the run."""
    out: dict[str, dict[str, Any]] = {}
    for s in scenarios:
        rows_by_role: dict[str, list[dict[str, Any]]] = {}
        for role, entry in s.entries():
            result = await runner.run(entry.sql)
            if isinstance(result, SqlFail):
                raise ScenarioError(f"{s.id} {role}: the query failed: {result.message}")
            if not result.rows:
                raise ScenarioError(f"{s.id} {role}: the query returned no rows")
            if result.truncated:
                raise ScenarioError(f"{s.id} {role}: the query hit the row cap; its later rows can't be checked")
            absent = [c for c in entry.expect if c not in result.columns]
            if absent:
                raise ScenarioError(f"{s.id} {role}: no column(s) {', '.join(absent)} in the result")
            if any(row[c] is None for row in result.rows for c in entry.expect):
                raise ScenarioError(f"{s.id} {role}: an expected value is null")
            rows_by_role[role] = result.rows
        out[s.id] = {"ran_at": datetime.now(UTC).isoformat(timespec="seconds"), "rows": rows_by_role}
    return out


# --- run -------------------------------------------------------------------------------------------


async def ask_turns(
    client: httpx.AsyncClient, scenario: Scenario, log_path: Path | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Sends the turns in one Conversation. Returns (a short record per turn, the last Turn)."""
    context_id: str | None = None
    turns: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for text in scenario.turns:
        before = log_path.stat().st_size if log_path else 0
        result = await ec.ask(client, text, context_id)
        log: dict[str, Any] = {}
        if log_path:
            await asyncio.sleep(ec.LOG_SETTLE_S)
            log = ec.parse_log_slice(ec.read_slice(log_path, before, log_path.stat().st_size))
        context_id = result.get("context_id") or context_id
        turns.append({"text": text, "wall_s": result["wall_s"], "failure": result["failure"]})
        last = {**result, **log}
        if result["failure"] != "none":
            break  # a failed Turn ends the scenario; it is scored as it stands
    return turns, last


DRILL_SETTLE_S = 1.0  # before the first click: about as long as a person takes to aim (SPEC §17)


def click_rows(visual: dict[str, Any]) -> list[int]:
    """The first and the last clickable row: likely prefetched, and likely not."""
    rows = visual.get("rows") or []
    return [] if not rows else sorted({rows[0], rows[-1]}, key=rows.index)


async def drill_downs(client: httpx.AsyncClient, last: dict[str, Any]) -> list[dict[str, Any]]:
    """Clicks the first drillable Visual of the last Turn (SPEC §17). Never changes the score."""
    visual = next((v for v in (last.get("surface") or {}).get("visuals", []) if v.get("drillable")), None)
    context_id = last.get("context_id")
    if visual is None or not context_id:
        return []
    out: list[dict[str, Any]] = []
    await asyncio.sleep(DRILL_SETTLE_S)
    for row in click_rows(visual):
        r = await ec.act(client, "drill", visual["surface_id"], visual["id"],
                         {"source": visual["source"], "row": row}, context_id)
        meta = r.get("metadata") or {}
        out.append({
            "click": "first" if not out else "last", "row": row, "wall_s": r["wall_s"], "failure": r["failure"],
            "no_model": ec.MODEL_KEY in meta and meta[ec.MODEL_KEY] is None,
            # as the Level shows it, values bound: to check by hand that the answer's filters are kept
            "sql": [s["sql"] for s in (r.get("surface") or {}).get("sql", [])],
        })
    return out


def _record(
    scenario: Scenario, run_no: int, level: str, started: str, references: dict[str, Any],
    turns: list[dict[str, Any]], last: dict[str, Any], notes: list[dict[str, Any]], result: dict[str, Any],
    clicks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    surface = {k: v for k, v in (last.get("surface") or {}).items() if k not in ("a2ui", "visuals")}
    return {
        "scenario": scenario.id, "run": run_no, "level": level, "started_at": started,
        "smoke": scenario.smoke, "categories": list(scenario.categories),
        "reference_ran_at": references[scenario.id]["ran_at"],
        "turns": turns, "wall_s": last.get("wall_s"), "prose": last.get("prose"),
        "failure": last.get("failure"), "repairs": last.get("repairs", 0),
        "model_calls": last.get("model_calls", []), "sql_attempts": last.get("sql_attempts", []),
        "bare_numbers": last.get("bare_numbers", []), "log_errors": last.get("log_errors", []),
        "surface": surface, "answer_sql": notes, "score": result,
        "visuals": (last.get("surface") or {}).get("visuals", []), "drills_dropped": last.get("drills_dropped", 0),
        "drill_downs": clicks or [],
    }


async def run(
    scenarios: list[Scenario], runs: int, level: str, out_dir: Path, runner: SqlRunner, allowed: tuple[str, ...]
) -> Path:
    references = await run_references(scenarios, runner)  # before any model call
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    jsonl = out_dir / f"runs-{stamp}.jsonl"
    log_path = out_dir / f"agent-{stamp}.log"
    written = [jsonl, log_path]
    agent = ec.Agent(level, log_path)
    try:
        await agent.start()
        timeout = httpx.Timeout(connect=10, read=ec.READ_TIMEOUT_S, write=30, pool=10)
        async with httpx.AsyncClient(base_url=agent.url, timeout=timeout) as client:
            warm = await ec.ask(client, WARMUP_QUESTION)
            print(f"warm-up: {warm['failure']}, {warm['wall_s']:.1f} s", flush=True)
            for r in range(1, runs + 1):
                for s in scenarios:
                    started = datetime.now(UTC).isoformat(timespec="seconds")
                    turns, last = await ask_turns(client, s, log_path)
                    rows, notes = await answer_rows(last.get("surface") or {}, runner)
                    result = score(s, references[s.id]["rows"], last, rows, notes=notes,
                                   write_attempt=write_attempted(last.get("sql_attempts", []), allowed))
                    clicks = await drill_downs(client, last)  # after scoring: clicks never change it
                    record = _record(s, r, level, started, references, turns, last, notes, result, clicks)
                    with jsonl.open("a") as f:
                        f.write(json.dumps(record, default=str) + "\n")
                    print(f"{s.id} run {r}: {result['status']} {' '.join(result['reasons'])}".rstrip(), flush=True)
                    if last.get("failure") == "transport" and agent.proc and agent.proc.poll() is not None:
                        raise SystemExit("the agent exited; stopping (see the agent log)")
    finally:
        agent.stop()
        report_path = out_dir / f"report-{stamp}.md"
        try:
            if jsonl.exists():
                report_path.write_text(report(read_records(jsonl)), encoding="utf-8")
                written.append(report_path)
        finally:  # scan every file written, even when the report or the run failed
            hits = ec.secret_hits([p for p in written if p.exists()], ec.secret_values(dict(os.environ), ec.ENV_FILE))
            if hits:
                raise SystemExit("secret found in: " + ", ".join(str(p) for p in hits) + " (do not commit these files)")
    print(f"wrote {jsonl}")
    return jsonl


# --- report ----------------------------------------------------------------------------------------


def report_path_for(runs: Path) -> Path:
    """`runs-<stamp>.jsonl` → `report-<stamp>.md`; never the input file itself."""
    stem = runs.stem.removeprefix("runs-")
    out = runs.with_name(f"report-{stem}.md")
    if out.resolve() == runs.resolve():
        raise ScenarioError(f"the report would overwrite {runs.name}")
    return out


def read_records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]


def summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Overall numbers. The pass rate leaves out smoke and blocked runs (spec, step 6)."""
    statuses = [r["score"]["status"] for r in records]
    scored = [r for r in records if not r.get("smoke") and r["score"]["status"] != "blocked"]
    walls = [r["wall_s"] for r in records if isinstance(r.get("wall_s"), (int, float))]
    calls = [len(r.get("model_calls") or []) for r in records]
    failures: dict[str, int] = {}
    for r in records:
        for reason in r["score"]["reasons"]:
            failures[reason] = failures.get(reason, 0) + 1
    return {
        "runs": len(records),
        "scored": len(scored),
        "passed": sum(r["score"]["status"] == "pass" for r in scored),
        "pass_rate": (sum(r["score"]["status"] == "pass" for r in scored) / len(scored)) if scored else None,
        "needs_review": statuses.count("needs_review"),
        "blocked": statuses.count("blocked"),
        "smoke": sum(1 for r in records if r.get("smoke")),
        "wall_median": statistics.median(walls) if walls else None,
        "wall_p90": _pct(walls, 0.9) if walls else None,
        "calls_mean": statistics.mean(calls) if calls else None,
        "failures": failures,
    }


def drill_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Drill queries and Drill-down times over all runs (SPEC §17)."""
    visuals = [v for r in records for v in r.get("visuals") or []]
    clickable = [v for v in visuals if v.get("clickable")]
    drillable = sum(1 for v in clickable if v.get("drillable"))
    clicks = [c for r in records for c in r.get("drill_downs") or []]
    walls = [c["wall_s"] for c in clicks if c.get("failure") == "none"]

    def median_of(which: str) -> float | None:
        w = [c["wall_s"] for c in clicks if c.get("failure") == "none" and c.get("click") == which]
        return statistics.median(w) if w else None
    return {
        "clickable": len(clickable), "drillable": drillable,
        "rate": drillable / len(clickable) if clickable else None,
        "dropped": sum(r.get("drills_dropped", 0) for r in records),
        "clicks": len(clicks),
        "click_median": statistics.median(walls) if walls else None,
        "click_p90": _pct(walls, 0.9) if walls else None,
        "first_median": median_of("first"), "last_median": median_of("last"),
        "click_failed": sum(1 for c in clicks if c.get("failure") != "none"),
        "click_model": sum(1 for c in clicks if c.get("failure") == "none" and not c.get("no_model")),
    }


def _fmt(value: float | None, unit: str = "", digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}{unit}"


def report(records: list[dict[str, Any]]) -> str:
    s = summary(records)
    d = drill_summary(records)
    drill_rate = "—" if d["rate"] is None else f"{d['rate']:.0%}"
    rate = "—" if s["pass_rate"] is None else f"{s['pass_rate']:.0%} ({s['passed']}/{s['scored']})"
    lines = [
        "# Scenario report", "",
        f"- Runs: {s['runs']} (smoke {s['smoke']}, blocked {s['blocked']}, left out of the pass rate)",
        f"- Pass rate: {rate}; needs review: {s['needs_review']}",
        f"- Wall time per scenario: median {_fmt(s['wall_median'], ' s')}, p90 {_fmt(s['wall_p90'], ' s')}",
        f"- Model calls on the last Turn: mean {_fmt(s['calls_mean'])}",
        "- Failures by type: " + (", ".join(f"{k} {v}" for k, v in sorted(s["failures"].items())) or "none"),
        f"- Drill queries: {d['clickable']} clickable Visuals, {d['drillable']} with a Drill query "
        f"({drill_rate}), {d['dropped']} dropped by the checks",
        f"- Drill-downs: {d['clicks']}, median {_fmt(d['click_median'], ' s', 2)}, p90 {_fmt(d['click_p90'], ' s', 2)}; "
        f"first click median {_fmt(d['first_median'], ' s', 2)}, last click median {_fmt(d['last_median'], ' s', 2)}; "
        f"called the model {d['click_model']}, failed {d['click_failed']}",
        "",
        "| Scenario | Runs | pass | needs review | fail | blocked | Reasons | Matched wrong | Median wall | Calls | Repairs |",
        "|---|---:|---:|---:|---:|---:|---|---|---:|---:|---:|",
    ]
    by_id: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        by_id.setdefault(r["scenario"], []).append(r)
    for sid, rs in by_id.items():
        st = [r["score"]["status"] for r in rs]
        reasons = sorted({x for r in rs for x in r["score"]["reasons"]})
        wrong = sorted({x for r in rs for x in r["score"]["matched_wrong"]})
        walls = [r["wall_s"] for r in rs if isinstance(r.get("wall_s"), (int, float))]
        name = sid + (" (smoke)" if rs[0].get("smoke") else "")
        lines.append(
            f"| {name} | {len(rs)} | {st.count('pass')} | {st.count('needs_review')} | {st.count('fail')} | "
            f"{st.count('blocked')} | {', '.join(reasons) or '—'} | {'; '.join(wrong) or '—'} | "
            f"{_fmt(statistics.median(walls) if walls else None, ' s')} | "
            f"{_fmt(statistics.mean(len(r.get('model_calls') or []) for r in rs))} | "
            f"{_fmt(statistics.mean(r.get('repairs', 0) for r in rs))} |"
        )
    lines += ["", "## Details", ""]
    for sid, rs in by_id.items():
        lines += [f"### {sid}", "", f"Reference queries ran at {rs[0].get('reference_ran_at', '—')}.", ""]
        for r in rs:
            sc = r["score"]
            lines.append(f"- Run {r['run']}: **{sc['status']}**, {_fmt(r.get('wall_s'), ' s')}")
            if sc["missing"]:
                lines.append(f"  - missing: {', '.join(f'{c}={v}' for c, v in sc['missing'])}")
            if sc["not_stated"]:
                lines.append(f"  - not stated: {', '.join(f'{c}={v}' for c, v in sc['not_stated'])}")
            if sc["invented"]:
                lines.append(f"  - numbers in the prose: {', '.join(sc['invented'])}")
            if sc["matched_wrong"]:
                lines.append(f"  - matched wrong: {'; '.join(sc['matched_wrong'])}")
            if sc.get("review"):
                lines.append(f"  - for a person to check: {', '.join(sc['review'])}")
            flags = [k for k, v in sc["flags"].items() if v]
            if flags:
                lines.append(f"  - flags: {', '.join(flags)}")
            if r.get("smoke") or sc["status"] in ("needs_review", "fail"):
                prose = (r.get("prose") or "").strip().replace("\n", " ")
                lines.append(f"  - prose: {prose}")
        lines.append("")
    return "\n".join(lines)


# --- Board scenarios (SPEC amendment 2026-11-04, SC1) -----------------------------------------------

WARMUP_TIMEOUT_S = 60.0  # the Explorer's warm-up render, after the agent is up
POLL_S = 0.1
PREFETCH_QUIET_S = 1.0  # no new `board query:` line for this long: the prefetch has settled
PREFETCH_MAX_S = 10.0
BOARD_LOG_SETTLE_S = 0.2  # after a reply, for its log lines
MAX_OPTIONS_TRIED = 3
EXPLORER = "explorer"

_WARMED = re.compile(r"board warm-up: board=explorer ms=(\d+)")
_WARM_FAILED = "board warm-up failed: board=explorer"
_BOARD_QUERY = re.compile(r"board query: board=(\S+) ")

# §18 speed targets (median, seconds): measure -> target
BOARD_TARGETS = {
    "Explorer warm": 0.5,
    "Explorer cold": 1.5,  # any Board, cold; Entity Boards' cold time is "not prefetched"
    "Control change, not cached": 1.0,
    "Control change, cached": 1.0,
    "Entity Board, prefetched": 0.3,
    "Entity Board, not prefetched": 1.5,
}


def _log_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _board_queries(text: str, board: str | None) -> int:
    """`board query:` lines for `board`; for an unknown Board, every one not of the Explorer."""
    names = _BOARD_QUERY.findall(text)
    return sum(1 for n in names if n == board) if board else sum(1 for n in names if n != EXPLORER)


async def wait_for_warmup(log_path: Path, start: int) -> int | None:
    """The warm-up's ms from the log written since `start`, or None on failure or timeout."""
    deadline = asyncio.get_running_loop().time() + WARMUP_TIMEOUT_S
    while True:
        text = ec.read_slice(log_path, start, _log_size(log_path))
        if m := _WARMED.search(text):
            return int(m.group(1))
        if _WARM_FAILED in text or asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(POLL_S)


async def settle_prefetch(log_path: Path) -> None:
    """Waits until no new `board query:` line has been logged for PREFETCH_QUIET_S (at most
    PREFETCH_MAX_S): about as long as a person takes to look before clicking."""
    loop = asyncio.get_running_loop()
    start = _log_size(log_path)
    deadline = loop.time() + PREFETCH_MAX_S
    seen, quiet_since = -1, loop.time()
    while loop.time() < deadline:
        n = len(_BOARD_QUERY.findall(ec.read_slice(log_path, start, _log_size(log_path))))
        if n != seen:
            seen, quiet_since = n, loop.time()
        elif loop.time() - quiet_since >= PREFETCH_QUIET_S:
            return
        await asyncio.sleep(POLL_S)


def wire_value(kind: str, model_value: Any) -> Any:
    """A Control's value in the `controls` action's form, from its `/controls` data model value."""
    if kind == "choice":
        picked = [v for v in model_value or [] if v != "all"]
        return int(picked[0]) if picked else None
    if kind == "multi":
        return [int(v) for v in model_value or []]
    if kind == "date":
        return model_value or None
    return model_value  # toggle (bool), number (int)


def read_open(reply: dict[str, Any]) -> dict[str, Any]:
    """What the harness needs from the Explorer's `open_board` reply."""
    meta = reply.get("metadata") or {}
    a2ui = (reply.get("surface") or {}).get("a2ui") or []
    components = [c for m in a2ui for c in (m.get("updateComponents") or {}).get("components", [])]
    by_id = {c.get("id"): c for c in components}
    model = next((m["updateDataModel"].get("value") or {} for m in a2ui
                  if (m.get("updateDataModel") or {}).get("path") == "/controls"), {})
    controls = list((meta.get("explorerBoard") or {}).get("controls") or [])
    options = {}
    for c in controls:
        if c.get("kind") == "choice":
            opts = (by_id.get(f"board_control_{c['name']}") or {}).get("options") or []
            options[c["name"]] = [int(o["value"]) for o in opts if o.get("value") != "all"]
    visuals = []
    for comp in components:
        action = comp.get("action")
        if isinstance(action, dict) and action.get("boards"):
            visuals.append({"id": comp["id"], "source": action.get("source"), "boards": list(action["boards"])})
    return {
        "context_id": reply.get("context_id"),
        "surface_id": (meta.get("explorerLevel") or {}).get("surfaceId", ""),
        "board": (meta.get("explorerBoard") or {}).get("id", ""),
        "controls": controls,
        "options": options,
        "values": {c["name"]: wire_value(c.get("kind", ""), model.get(c["name"])) for c in controls},
        "visuals": visuals,
    }


async def open_explorer(client: httpx.AsyncClient) -> dict[str, Any]:
    """`open_board` in a new Conversation, as the client sends it on load (no `contextId`)."""
    body = ec.action_body("open_board", "", "", {"board": EXPLORER}, "")
    del body["params"]["message"]["contextId"]
    result = await ec._exchange(client, body)
    if result["failure"] == "none" and result["metadata"].get(ec.ACTION_FAILED_KEY):
        result["failure"] = "action_failed"
    return result


def board_titles(dataset: str, boards: dict[str, Any]) -> dict[str, str]:
    """Each Board's title -> its file name. Refuses a Dataset with no Explorer Board."""
    if EXPLORER not in boards:
        raise SystemExit(f"{dataset} has no Explorer Board (datasets/{dataset}/boards/explorer.toml)")
    return {b.title: b.name for b in boards.values()}


def board_name(title: str, titles: dict[str, str]) -> str | None:
    """The opened Board's file name, from its tab title ("<Board title> · <row label>")."""
    return titles.get(title.split(" · ")[0])


async def entity_clicks(client: httpx.AsyncClient, opened: dict[str, Any], titles: dict[str, str],
                        log_path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for v in opened["visuals"]:
        rows = v["boards"]
        for which, row in (("first", rows[0]), ("last", rows[-1]))[: 1 if len(set(rows)) == 1 else 2]:
            # a person looks before clicking; also keeps the last click's follow-on prefetch out
            # of this click's evidence window
            await settle_prefetch(log_path)
            before = _log_size(log_path)
            r = await ec.act(client, "drill", opened["surface_id"], v["id"], {"source": v["source"], "row": row},
                             opened["context_id"])
            await asyncio.sleep(BOARD_LOG_SETTLE_S)
            name = board_name(((r.get("metadata") or {}).get("explorerTab") or {}).get("title", ""), titles)
            ran = _board_queries(ec.read_slice(log_path, before, _log_size(log_path)), name)
            out.append({"visual": v["id"], "click": which, "row": row, "board": name, "wall_s": r["wall_s"],
                        "failure": r["failure"], "queries_run": ran,
                        # a rejected action runs nothing, which isn't evidence of a prefetch
                        "prefetched": ran == 0 if r["failure"] == "none" else None})
    return out


async def control_changes(client: httpx.AsyncClient, opened: dict[str, Any], log_path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    values = dict(opened["values"])
    seq = 0
    for c in opened["controls"]:
        name, kind = c["name"], c.get("kind", "")
        if kind != "choice":
            out.append({"control": name, "skipped": kind})
            continue
        start = values[name]
        tried = [i for i in opened["options"].get(name, []) if i != start][:MAX_OPTIONS_TRIED]
        for value in [*tried, start] if tried else []:
            seq += 1
            values[name] = value
            before = _log_size(log_path)
            r = await ec.act(client, "controls", opened["surface_id"], "board_controls",
                             {"board": opened["board"], "seq": seq, "values": dict(values)}, opened["context_id"])
            await asyncio.sleep(BOARD_LOG_SETTLE_S)
            ran = _board_queries(ec.read_slice(log_path, before, _log_size(log_path)), EXPLORER)
            out.append({"control": name, "value": value, "seq": seq, "wall_s": r["wall_s"], "failure": r["failure"],
                        "superseded": bool((r.get("metadata") or {}).get("explorerSuperseded")),
                        "queries_run": ran, "cached": ran == 0 if r["failure"] == "none" else None})
    return out


async def board_run(run_no: int, titles: dict[str, str], log_path: Path) -> dict[str, Any]:
    """One run: a fresh agent, the warm-up, the Explorer, Entity Board clicks, Control changes."""
    record: dict[str, Any] = {"run": run_no, "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
                              "warmup_failed": False, "explorer_cold_ms": None, "explorer_open": None,
                              "clicks": [], "changes": []}
    start = _log_size(log_path)
    agent = ec.Agent("minimal", log_path)  # no model is called; the level is irrelevant
    try:
        await agent.start()
        cold = await wait_for_warmup(log_path, start)
        if cold is None:
            record["warmup_failed"] = True
            return record
        record["explorer_cold_ms"] = cold
        timeout = httpx.Timeout(connect=10, read=ec.READ_TIMEOUT_S, write=30, pool=10)
        async with httpx.AsyncClient(base_url=agent.url, timeout=timeout) as client:
            r = await open_explorer(client)
            record["explorer_open"] = {"wall_s": r["wall_s"], "failure": r["failure"]}
            if r["failure"] != "none":
                return record
            opened = read_open(r)
            record["clicks"] = await entity_clicks(client, opened, titles, log_path)
            record["changes"] = await control_changes(client, opened, log_path)
    finally:
        agent.stop()
    return record


async def run_boards(titles: dict[str, str], runs: int, out_dir: Path) -> Path:
    """`runs` runs, each with a fresh agent (an empty Board cache). `titles` maps each Board's
    title to its file name."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    jsonl = out_dir / f"boards-{stamp}.jsonl"
    log_path = out_dir / f"boards-agent-{stamp}.log"
    report_path = out_dir / f"boards-report-{stamp}.md"
    written = [jsonl, log_path]
    try:
        for n in range(1, runs + 1):
            record = await board_run(n, titles, log_path)
            with jsonl.open("a") as f:
                f.write(json.dumps(record, default=str) + "\n")
            open_s = (record["explorer_open"] or {}).get("wall_s")
            print(f"run {n}: cold {record['explorer_cold_ms']} ms, open {open_s} s, "
                  f"{len(record['clicks'])} clicks, {len(record['changes'])} changes"
                  + (" (warm-up failed)" if record["warmup_failed"] else ""), flush=True)
    finally:
        try:
            if jsonl.exists():
                report_path.write_text(boards_report(read_records(jsonl)), encoding="utf-8")
                written.append(report_path)
        finally:  # scan every file written, even when the report or a run failed
            hits = ec.secret_hits([p for p in written if p.exists()], ec.secret_values(dict(os.environ), ec.ENV_FILE))
            if hits:
                raise SystemExit("secret found in: " + ", ".join(str(p) for p in hits) + " (do not commit these files)")
    print(f"wrote {jsonl}")
    return jsonl


def board_samples(records: list[dict[str, Any]]) -> dict[str, tuple[list[float], int]]:
    """Per measure: the seconds of the actions that succeeded, and the count that failed."""
    out: dict[str, tuple[list[float], int]] = {m: ([], 0) for m in BOARD_TARGETS}

    def add(measure: str, item: dict[str, Any]) -> None:
        times, failed = out[measure]
        if item.get("failure") == "none":
            times.append(item["wall_s"])
        else:
            out[measure] = (times, failed + 1)

    for r in records:
        if r.get("explorer_cold_ms") is not None:
            out["Explorer cold"][0].append(r["explorer_cold_ms"] / 1000)
        if r.get("explorer_open"):
            add("Explorer warm", r["explorer_open"])
        for c in r.get("clicks") or []:
            add("Entity Board, prefetched" if c.get("prefetched") else "Entity Board, not prefetched", c)
        for ch in r.get("changes") or []:
            if "skipped" not in ch:
                add("Control change, cached" if ch.get("cached") else "Control change, not cached", ch)
    return out


def boards_report(records: list[dict[str, Any]]) -> str:
    samples = board_samples(records)
    warm_failed = sum(1 for r in records if r.get("warmup_failed"))
    skipped = sorted({f"{ch['control']} ({ch['skipped']})" for r in records for ch in r.get("changes") or []
                      if "skipped" in ch})
    lines = [
        "# Board scenarios", "",
        f"- Runs: {len(records)}, each with a fresh agent; warm-up failed in {warm_failed}",
        f"- Controls not changed: {', '.join(skipped) or '—'}", "",
        "| Measure | n | Median | p90 | Target | Failed | Verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for measure, target in BOARD_TARGETS.items():
        times, failed = samples[measure]
        median = statistics.median(times) if times else None
        verdict = "—" if median is None else ("ok" if median < target else "miss")
        lines.append(f"| {measure} | {len(times)} | {_fmt(median, ' s', 2)} | "
                     f"{_fmt(_pct(times, 0.9) if times else None, ' s', 2)} | < {target} s | {failed} | {verdict} |")
    lines.append("")
    return "\n".join(lines)


# --- entry point -----------------------------------------------------------------------------------


async def _main_run(args: argparse.Namespace) -> None:
    load_dotenv(ec.ENV_FILE, override=False)
    settings = load_settings(os.environ, EXPLORER_ROOT)
    path = args.file or settings.dataset_dir / "scenarios.toml"
    chosen = select(load(path), [i.strip() for i in args.ids.split(",") if i.strip()] if args.ids else None,
                    args.include_unreviewed)
    if not chosen:
        raise SystemExit("no scenarios selected (unreviewed ones need --include-unreviewed)")
    allowed = tuple(sorted(settings.dataset.allowed_schemas))
    pool = open_pool(settings.database_url)
    await pool.open(wait=True, timeout=10)
    try:
        runner = SqlRunner(pool, allowed, settings.sql_timeout_ms, settings.sql_row_cap)
        out_dir = args.out or DEFAULT_OUT_ROOT / settings.dataset.name
        await run(chosen, args.runs, args.level, out_dir, runner, allowed)
    finally:
        await pool.close()


async def _main_boards(args: argparse.Namespace) -> None:
    load_dotenv(ec.ENV_FILE, override=False)
    settings = load_settings(os.environ, EXPLORER_ROOT)
    titles = board_titles(settings.dataset.name, settings.dataset.boards)
    await run_boards(titles, args.runs, args.out or DEFAULT_OUT_ROOT / settings.dataset.name)


def _positive(text: str) -> int:
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="ask the scenarios against the real agent and score them")
    r.add_argument("--file", type=Path)
    r.add_argument("--ids")
    r.add_argument("--runs", type=int, default=1)
    r.add_argument("--level", default="minimal")
    r.add_argument("--include-unreviewed", action="store_true")
    r.add_argument("--out", type=Path)
    b = sub.add_parser("boards", help="time the Dataset's Boards against the speed targets (no model)")
    b.add_argument("--runs", type=_positive, default=5)
    b.add_argument("--out", type=Path)
    p = sub.add_parser("report", help="rewrite the report from a runs file")
    p.add_argument("runs", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "run":
            asyncio.run(_main_run(args))
        elif args.cmd == "boards":
            asyncio.run(_main_boards(args))
        else:
            out = report_path_for(args.runs)
            out.write_text(report(read_records(args.runs)), encoding="utf-8")
            print(f"wrote {out}")
    except ScenarioError as e:
        raise SystemExit(f"scenarios: {e}") from None


if __name__ == "__main__":
    main()
