"""End-to-end scenarios, scored automatically (explorer/specs/scenarios/SPEC.md, ADR 0018).

    uv run python scripts/scenarios.py run [--file F] [--ids a,b] [--runs 1] [--level minimal] [--include-unreviewed]
    uv run python scripts/scenarios.py report RUNS.jsonl
    uv run python scripts/scenarios.py boards [--runs 5] [--out DIR]
    uv run python scripts/scenarios.py compare A.jsonl B.jsonl [--out DIR]

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
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fractions import Fraction
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
    "board", "controls", "click", "ask_within",
}
STEP_KEYS = {"visual", "row"}
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
    # A Board scenario (amendment 2026-11-04, §21): set up on a Board before any model call.
    board: str = ""
    controls: tuple[tuple[str, str], ...] = ()  # (Control label, option label), in order
    click: tuple[tuple[str, str], ...] = ()  # (layout name, row text): each opens an Entity Board
    ask_within: tuple[str, str] | None = None  # (layout name, row text): "Ask within filters", the last step

    @property
    def set(self) -> str:
        """`board` when the scenario starts from a Board, else `typed`; `compare` gates them apart."""
        return "board" if self.board else "typed"

    @property
    def kind(self) -> str | None:
        """The Board kind from the shape: (a) Controls, (b) a click then turns, (c) Ask within."""
        if self.ask_within:
            return "c"
        if self.click:
            return "b"
        return "a" if self.controls else None

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


def _step(raw: Any, where: str) -> tuple[str, str]:
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: must be a table with visual and row")
    _unknown(raw, STEP_KEYS, where)
    visual, row = raw.get("visual"), raw.get("row")
    if not (isinstance(visual, str) and visual.strip() and isinstance(row, str) and row.strip()):
        raise ScenarioError(f"{where}: needs a visual and a row")
    return visual.strip(), row


def _board_keys(raw: dict[str, Any], sid: str, boards: set[str] | None) -> dict[str, Any]:
    board = raw.get("board", "")
    if not isinstance(board, str):
        raise ScenarioError(f"{sid}: board must be a Board name")
    without = [k for k in ("controls", "click", "ask_within") if k in raw and not board]
    if without:
        raise ScenarioError(f"{sid}: {', '.join(without)} need(s) board")
    if board and boards is not None and board not in boards:
        raise ScenarioError(f"{sid}: no Board without a key named {board!r} in the Dataset")
    controls = raw.get("controls", {})
    if not isinstance(controls, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in controls.items()):
        raise ScenarioError(f"{sid}: controls must map Control labels to option labels")
    click = raw.get("click", [])
    if not isinstance(click, list):
        raise ScenarioError(f"{sid}: click must be a list of steps")
    return {
        "board": board,
        "controls": tuple(controls.items()),
        "click": tuple(_step(c, f"{sid}: click") for c in click),
        "ask_within": _step(raw["ask_within"], f"{sid}: ask_within") if "ask_within" in raw else None,
    }


def parse_scenario(raw: dict[str, Any], boards: set[str] | None = None) -> Scenario:
    """`boards`: the Dataset's Board names without a key; None skips that check."""
    sid = raw.get("id")
    if not isinstance(sid, str) or not sid:
        raise ScenarioError("a scenario needs an id")
    _unknown(raw, SCENARIO_KEYS, sid)
    board_keys = _board_keys(raw, sid, boards)
    if ("turns" in raw) == (board_keys["ask_within"] is not None):
        raise ScenarioError(f"{sid}: needs turns or ask_within, not both" if "turns" in raw
                            else f"{sid}: turns must be a non-empty list of questions")
    turns = raw.get("turns", [])
    if board_keys["ask_within"] is None and (
        not isinstance(turns, list) or not turns or not all(isinstance(t, str) and t.strip() for t in turns)
    ):
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
        **board_keys,
    )
    if not (scenario.reference or scenario.expect_empty or scenario.smoke):
        raise ScenarioError(f"{sid}: needs a reference, expect_empty or smoke")
    if scenario.alternates and not scenario.reference:
        raise ScenarioError(f"{sid}: alternates need a reference")
    labels = [w.label for w in scenario.wrong]
    if len(labels) != len(set(labels)):
        raise ScenarioError(f"{sid}: wrong labels must be unique")
    return scenario


def load(path: Path, boards: set[str] | None = None) -> list[Scenario]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    _unknown(data, {"scenario"}, str(path))
    scenarios = [parse_scenario(raw, boards) for raw in data.get("scenario", [])]
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
    client: httpx.AsyncClient, scenario: Scenario, log_path: Path | None,
    context_id: str | None = None, viewing: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Sends the turns in one Conversation: a new one, or the Board's (`context_id`), with every
    Turn naming the Board's tab as the one in view. Returns (a short record per turn, the last Turn)."""
    turns: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for text in scenario.turns:
        before = log_path.stat().st_size if log_path else 0
        # without `viewing`, the call is exactly as before (a typed scenario)
        result = await (ec.ask(client, text, context_id, viewing=viewing) if viewing
                        else ec.ask(client, text, context_id))
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
    clicks: list[dict[str, Any]] | None = None, *, model: str = "", commit: dict[str, Any] | None = None,
    setup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    surface = {k: v for k, v in (last.get("surface") or {}).items() if k not in ("a2ui", "visuals")}
    return {
        "scenario": scenario.id, "run": run_no, "level": level, "started_at": started,
        "set": scenario.set, "kind": scenario.kind, "reviewed": scenario.reviewed,
        "model": model, "served_model": (last.get("metadata") or {}).get(ec.MODEL_KEY), "commit": commit,
        **({"setup": setup} if scenario.board else {}),
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


async def run_one(
    client: httpx.AsyncClient, s: Scenario, log_path: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any] | None, str | None]:
    """One scenario's Turns: (turns, last Turn, the Board set-up record, the set-up failure)."""
    if not s.board:
        turns, last = await ask_turns(client, s, log_path)
        return turns, last, None, None
    board, setup = await board_setup(client, s, log_path)
    if board is None:
        return [], {}, setup, setup["reason"]
    if not board["tab"]:  # without it, no Board pair would be sent: a typed question in disguise
        setup["reason"] = "no tab in the Board's reply"
        return [], {}, setup, setup["reason"]
    try:
        if s.ask_within:
            turns, last = await ask_within(client, s, board, log_path)
        else:
            turns, last = await ask_turns(client, s, log_path, board["context_id"], board["tab"])
    except SetupFailed as e:
        setup["reason"] = str(e)
        return [], {}, setup, str(e)
    return turns, last, setup, None


async def run(
    scenarios: list[Scenario], runs: int, level: str, out_dir: Path, runner: SqlRunner, allowed: tuple[str, ...],
    *, model: str = "", commit: dict[str, Any] | None = None,
) -> Path:
    """`model` is the configured `MODEL` and `commit` the code's identity, both recorded for `compare`."""
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
                    turns, last, setup, failed = await run_one(client, s, log_path)
                    if failed is not None:  # no model was called
                        rows, notes, result, clicks = [], [], setup_failed_score(failed), []
                    else:
                        rows, notes = await answer_rows(last.get("surface") or {}, runner)
                        result = score(s, references[s.id]["rows"], last, rows, notes=notes,
                                       write_attempt=write_attempted(last.get("sql_attempts", []), allowed))
                        clicks = await drill_downs(client, last)  # after scoring: clicks never change it
                    record = _record(s, r, level, started, references, turns, last, notes, result, clicks,
                                     model=model, commit=commit, setup=setup)
                    with jsonl.open("a") as f:
                        f.write(json.dumps(record, default=str) + "\n")
                    print(f"{s.id} run {r}: {result['status']} {' '.join(result['reasons'])}".rstrip(), flush=True)
                    if (failed is not None or last.get("failure") == "transport") and agent.proc \
                            and agent.proc.poll() is not None:
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
    """Overall numbers. The pass rate leaves out smoke, blocked and setup_failed runs (spec, step 6)."""
    statuses = [r["score"]["status"] for r in records]
    scored = [r for r in records if not r.get("smoke") and r["score"]["status"] not in ("blocked", "setup_failed")]
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
        "setup_failed": statuses.count("setup_failed"),
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


def record_set(record: dict[str, Any]) -> str:
    """A record's set; a runs file from before the Board scenarios has only typed ones."""
    return record.get("set") or "typed"


def _rate(s: dict[str, Any]) -> str:
    return "—" if s["pass_rate"] is None else f"{s['pass_rate']:.0%} ({s['passed']}/{s['scored']})"


def report(records: list[dict[str, Any]]) -> str:
    s = summary(records)
    d = drill_summary(records)
    drill_rate = "—" if d["rate"] is None else f"{d['rate']:.0%}"
    rate = _rate(s)
    models = sorted({str(r["model"]) for r in records if r.get("model")}) or ["—"]
    levels = sorted({str(r["level"]) for r in records if r.get("level")}) or ["—"]
    sets = []
    for name in ("board", "typed"):
        sub = summary([r for r in records if record_set(r) == name])
        sets.append(f"- {name.title()} set: pass rate {_rate(sub)}, median wall {_fmt(sub['wall_median'], ' s')}"
                    f" ({sub['runs']} runs, setup failed {sub['setup_failed']})")
    lines = [
        "# Scenario report", "",
        f"- Model: {', '.join(models)}; level: {', '.join(levels)}",
        *sets,
        f"- Runs: {s['runs']} (smoke {s['smoke']}, blocked {s['blocked']}, setup failed {s['setup_failed']}, "
        "left out of the pass rate)",
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
        "| Scenario | Runs | pass | needs review | fail | blocked | setup failed | Reasons | Matched wrong | Median wall | Calls | Repairs |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|",
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
            f"{st.count('blocked')} | {st.count('setup_failed')} | {', '.join(reasons) or '—'} | {'; '.join(wrong) or '—'} | "
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


def _components(reply: dict[str, Any]) -> list[dict[str, Any]]:
    a2ui = (reply.get("surface") or {}).get("a2ui") or []
    return [c for m in a2ui for c in (m.get("updateComponents") or {}).get("components", [])]


def read_board(reply: dict[str, Any]) -> dict[str, Any]:
    """The Board model from a reply that opens a Board (`open_board` or a click to an Entity
    Board): the Conversation, Surface, tab and instance; each Control's name, kind, label and
    options as (label, wire value); the current wire values; and the components by ID."""
    meta = reply.get("metadata") or {}
    a2ui = (reply.get("surface") or {}).get("a2ui") or []
    by_id = {c.get("id"): c for c in _components(reply)}
    model = next((m["updateDataModel"].get("value") or {} for m in a2ui
                  if (m.get("updateDataModel") or {}).get("path") == "/controls"), {})
    controls = []
    for c in (meta.get("explorerBoard") or {}).get("controls") or []:
        comp = by_id.get(f"board_control_{c['name']}") or {}
        options = [(str(o.get("label", "")), None if o.get("value") == "all" else int(o["value"]))
                   for o in comp.get("options") or []] if c.get("kind") == "choice" else []
        controls.append({"name": c["name"], "kind": c.get("kind", ""), "label": str(comp.get("label", c["name"])),
                         "options": options})
    return {
        "context_id": reply.get("context_id"),
        "surface_id": (meta.get("explorerLevel") or {}).get("surfaceId", ""),
        "tab": (meta.get("explorerTab") or {}).get("id"),
        "board": (meta.get("explorerBoard") or {}).get("id", ""),
        "controls": controls,
        "values": {c["name"]: wire_value(c["kind"], model.get(c["name"])) for c in controls},
        "components": by_id,
    }


def read_open(reply: dict[str, Any]) -> dict[str, Any]:
    """What the `boards` subcommand needs from the Explorer's `open_board` reply."""
    b = read_board(reply)
    visuals = []
    for comp in b["components"].values():
        action = comp.get("action")
        if isinstance(action, dict) and action.get("boards"):
            visuals.append({"id": comp["id"], "source": action.get("source"), "boards": list(action["boards"])})
    return {
        "context_id": b["context_id"],
        "surface_id": b["surface_id"],
        "board": b["board"],
        "controls": [{"name": c["name"], "kind": c["kind"]} for c in b["controls"]],
        "options": {c["name"]: [w for _, w in c["options"] if w is not None]
                    for c in b["controls"] if c["kind"] == "choice"},
        "values": b["values"],
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
        raise SystemExit(f"{dataset} has no Explorer Board ({dataset} dataset: boards/explorer.toml)")
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


# --- scored Board scenarios (amendment 2026-11-04, `../../SPEC.md` §21) -----------------------------


class SetupFailed(Exception):
    """A Board scenario's set-up can't go on. The run is `setup_failed` and no model is called."""


def _norm(text: Any) -> str:
    return unescape(str(text)).strip().casefold()


def _one(found: list[Any], what: str) -> Any:
    if not found:
        raise SetupFailed(f"{what} matches nothing")
    if len(found) > 1:
        raise SetupFailed(f"{what} matches {len(found)}")
    return found[0]


def layout_name(component_id: str) -> str:
    """The layout component a built component belongs to: its ID up to the first `__`."""
    return component_id.split("__")[0]


def merge_components(model: dict[str, Any], reply: dict[str, Any]) -> None:
    """Replaces, in the Board model, every component of the layout names the reply re-sends. A
    re-queried panel can come back with other components (rows, or "no rows"), so a stale one
    mustn't stay behind to be clicked."""
    sent = _components(reply)
    names = {layout_name(str(c.get("id"))) for c in sent}
    kept = {cid: c for cid, c in model["components"].items() if layout_name(str(cid)) not in names}
    model["components"] = {**kept, **{c.get("id"): c for c in sent}}


def _is_treemap(comp: dict[str, Any]) -> bool:
    data = (comp.get("spec") or {}).get("data")
    return isinstance(data, list) and any(
        t.get("type") == "treemap" for d in data if isinstance(d, dict) for t in d.get("transform") or [])


def find_row(model: dict[str, Any], visual: str, row: str) -> tuple[dict[str, Any], int]:
    """The clickable component named `visual` and the row index of the row showing `row`
    (spec, Scenario file): a `DataGrid` cell, or a Treemap leaf's `_name`."""
    comp = _one([c for cid, c in model["components"].items()
                 if isinstance(c.get("action"), dict) and layout_name(str(cid)) == visual],
                f"clickable Visual {visual!r}")
    want = _norm(row)
    if comp.get("component") == "DataGrid":
        index = _one([i for i, cells in enumerate(comp.get("rows") or []) if any(_norm(c) == want for c in cells)],
                     f"row {row!r} in {visual!r}")
    elif comp.get("component") == "VegaChart" and _is_treemap(comp):
        leaf = _one([v for v in ec.chart_values(comp.get("spec") or {}) if "_name" in v and _norm(v["_name"]) == want],
                    f"row {row!r} in {visual!r}")
        if "_row" not in leaf:
            raise SetupFailed(f"row {row!r} in {visual!r} isn't clickable")
        index = int(leaf["_row"])
    else:
        raise SetupFailed(f"{visual!r} is a {comp.get('component')}; only DataGrid and Treemap rows are supported")
    return comp, index


def _control(model: dict[str, Any], label: str, option: str) -> tuple[str, int | None]:
    c = _one([c for c in model["controls"] if _norm(c["label"]) == _norm(label)], f"Control {label!r}")
    if c["kind"] != "choice":
        raise SetupFailed(f"Control {label!r} is {c['kind']}; only choice Controls are supported")
    _, wire = _one([o for o in c["options"] if _norm(o[0]) == _norm(option)], f"option {option!r} of {label!r}")
    return c["name"], wire


async def _setup_step(steps: list[dict[str, Any]], what: str, call: Any) -> dict[str, Any]:
    r = await call
    steps.append({"step": what, "wall_s": r["wall_s"], "failure": r["failure"]})
    if r["failure"] != "none":
        raise SetupFailed(f"{what} failed: {r['failure']}")
    return r


async def board_setup(
    client: httpx.AsyncClient, scenario: Scenario, log_path: Path | None
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Opens the Board, sets the Controls and clicks through (spec, step 2). Returns (the Board
    model, or None after a set-up failure; the `setup` record: each step's time and the reason)."""
    steps: list[dict[str, Any]] = []
    setup: dict[str, Any] = {"steps": steps, "reason": None}
    try:
        body = ec.action_body("open_board", "", "", {"board": scenario.board}, "")
        del body["params"]["message"]["contextId"]
        r = await _setup_step(steps, "open_board", _exchange_action(client, body))
        model = read_board(r)
        seq = 0
        for label, option in scenario.controls:
            name, wire = _control(model, label, option)
            seq += 1
            model["values"][name] = wire
            r = await _setup_step(steps, f"controls {label}", ec.act(
                client, "controls", model["surface_id"], "board_controls",
                {"board": model["board"], "seq": seq, "values": dict(model["values"])}, model["context_id"]))
            if (r.get("metadata") or {}).get("explorerSuperseded"):
                raise SetupFailed(f"controls {label} was superseded")
            merge_components(model, r)
        for visual, row in scenario.click:
            if log_path:
                await settle_prefetch(log_path)
            comp, index = find_row(model, visual, row)
            if index not in (comp["action"].get("boards") or []):
                raise SetupFailed(f"row {row!r} in {visual!r} doesn't open a Board")
            r = await _setup_step(steps, f"click {visual}", ec.act(
                client, "drill", model["surface_id"], comp["id"], {"source": comp["action"].get("source"), "row": index},
                model["context_id"]))
            model = {**read_board(r), "context_id": r.get("context_id") or model["context_id"]}
        return model, setup
    except SetupFailed as e:
        setup["reason"] = str(e)
        return None, setup


async def _exchange_action(client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    result = await ec._exchange(client, body)
    if result["failure"] == "none" and result["metadata"].get(ec.ACTION_FAILED_KEY):
        result["failure"] = "action_failed"
    return result


async def ask_within(
    client: httpx.AsyncClient, scenario: Scenario, model: dict[str, Any], log_path: Path | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """"Ask within filters" on a row, scored as the last Turn (spec, step 4). A row that can't be
    found raises SetupFailed before anything is sent."""
    visual, row = scenario.ask_within or ("", "")
    comp, index = find_row(model, visual, row)
    if index not in (comp["action"].get("boards") or []):
        raise SetupFailed(f"row {row!r} in {visual!r} doesn't open a Board")
    before = log_path.stat().st_size if log_path else 0
    result = await ec.act(client, "ask", model["surface_id"], comp["id"],
                          {"source": comp["action"].get("source"), "row": index}, model["context_id"],
                          viewing=model["tab"])
    log: dict[str, Any] = {}
    if log_path:
        await asyncio.sleep(ec.LOG_SETTLE_S)
        log = ec.parse_log_slice(ec.read_slice(log_path, before, log_path.stat().st_size))
    turns = [{"text": f"ask within filters: {visual} / {row}", "wall_s": result["wall_s"], "failure": result["failure"]}]
    return turns, {**result, "context_id": result.get("context_id") or model["context_id"], **log}


def setup_failed_score(reason: str) -> dict[str, Any]:
    return {"status": "setup_failed", "reasons": [reason], "review": [], "missing": [], "not_stated": [],
            "invented": [], "matched_wrong": [], "flags": {}}


def repo_commit(paths: list[Path], root: Path | None = None) -> dict[str, Any]:
    """HEAD, and whether `paths` (the agent and the Dataset) have uncommitted changes, untracked
    files included."""
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=root or ec.REPO_ROOT, capture_output=True, text=True,
                              check=True).stdout
    return {"sha": git("rev-parse", "HEAD").strip(),
            "dirty": bool(git("status", "--porcelain", "--", *(str(p) for p in paths)).strip())}


# --- compare: the answer model A/B gate (`../../SPEC.md` §21) ---------------------------------------

SETS = ("board", "typed")
GATE_BOARD_POINTS = Fraction(1, 10)  # 1. B's Board-set pass rate ≥ A's + 10 points
GATE_TYPED_SLACK = 1  # 2. B's typed-set runs that are not a pass ≤ A's + 1
GATE_TIME = 1.15  # 3. B's median Turn wall time ≤ 1.15 × A's, in each set
CLOSE_TIME = (1.10, 1.20)  # B's median time ratio in this range (inclusive): close
_EPS = 1e-9  # float slack at the gate's edges
IDENTITY = ("reviewed", "model", "level", "commit")


def _identity(name: str, records: list[dict[str, Any]]) -> tuple[list[str], dict[str, Any]]:
    """Problems with one file's run identity, and its model, level and commit."""
    problems = []
    if not records:
        return [f"{name} has no records"], {}
    lacking = sorted({k for r in records for k in IDENTITY if k not in r or (k != "reviewed" and not r[k])})
    if lacking:
        return [f"{name}: records without {', '.join(lacking)}"], {}
    ident = {}
    for key in ("model", "level"):
        values = {r[key] for r in records}
        if len(values) > 1:
            problems.append(f"{name}: more than one {key} ({', '.join(sorted(map(str, values)))})")
        ident[key] = sorted(map(str, values))[0]
    shas = {(r["commit"] or {}).get("sha") for r in records}
    if len(shas) > 1:
        problems.append(f"{name}: more than one commit")
    ident["commit"] = sorted(map(str, shas))[0]
    if any((r["commit"] or {}).get("dirty") for r in records):
        problems.append(f"{name}: run with uncommitted changes in the agent or the Dataset")
    return problems, ident


def _status(r: dict[str, Any]) -> str:
    return r["score"]["status"]


def set_figures(records: list[dict[str, Any]]) -> dict[str, Any]:
    """One set's figures in one file (spec, step 7.3). `needs_review` and `fail` are not a pass."""
    walls = [r["wall_s"] for r in records if isinstance(r.get("wall_s"), (int, float))]
    passes = sum(1 for r in records if _status(r) == "pass")

    def tokens(key: str) -> float | None:
        per = [sum((c.get(key) or 0) for c in r.get("model_calls") or []) for r in records]
        return statistics.mean(per) if per else None
    return {
        "runs": len(records), "passes": passes, "not_pass": len(records) - passes,
        "rate": Fraction(passes, len(records)) if records else None,
        "wall_median": statistics.median(walls) if walls else None,
        "wall_p90": _pct(walls, 0.9) if walls else None,
        "calls_mean": statistics.mean(len(r.get("model_calls") or []) for r in records) if records else None,
        "repairs": sum(r.get("repairs", 0) for r in records),
        "write_attempted": sum(1 for r in records if (r["score"].get("flags") or {}).get("write_attempted")),
        "prompt_mean": tokens("prompt"), "cached_mean": tokens("cached"), "output_mean": tokens("completion"),
    }


def _by_set(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {name: set_figures([r for r in records if record_set(r) == name]) for name in SETS}


def gate(a: dict[str, dict[str, Any]], b: dict[str, dict[str, Any]]) -> list[str]:
    """The gate's conditions B fails; none means `B wins`."""
    failed = []
    if b["board"]["rate"] - a["board"]["rate"] < GATE_BOARD_POINTS:
        failed.append("1: Board-set pass rate below A's + 10 points")
    if b["typed"]["not_pass"] > a["typed"]["not_pass"] + GATE_TYPED_SLACK:
        failed.append("2: typed-set runs that are not a pass above A's + 1")
    for name in SETS:
        ma, mb = a[name]["wall_median"], b[name]["wall_median"]
        if ma is None or mb is None or mb > GATE_TIME * ma + _EPS:
            failed.append(f"3: {name}-set median Turn wall time above 1.15 × A's")
    return failed


def _flipped(records: list[dict[str, Any]], i: int) -> list[dict[str, Any]]:
    r = records[i]
    status = "fail" if _status(r) == "pass" else "pass"
    return [*records[:i], {**r, "score": {**r["score"], "status": status}}, *records[i + 1:]]


def close_call(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> list[str]:
    """Why the verdict is close (spec, step 7.5): one flipped run changes it, or B's median time
    ratio lies in CLOSE_TIME in a set. Empty when it is clear."""
    wins = not gate(_by_set(a), _by_set(b))
    reasons = []
    flips = [("A", a, i) for i in range(len(a))] + [("B", b, i) for i in range(len(b))]
    for side, records, i in flips:
        fa = _flipped(a, i) if side == "A" else a
        fb = _flipped(b, i) if side == "B" else b
        if (not gate(_by_set(fa), _by_set(fb))) != wins:
            r = records[i]
            reasons.append(f"flipping {side}'s {r['scenario']} run {r.get('run')} changes the verdict")
            break
    fa, fb = _by_set(a), _by_set(b)
    for name in SETS:
        ma, mb = fa[name]["wall_median"], fb[name]["wall_median"]
        if ma and mb is not None and CLOSE_TIME[0] - _EPS <= mb / ma <= CLOSE_TIME[1] + _EPS:
            reasons.append(f"{name}-set median time ratio {mb / ma:.2f}× is within 1.10–1.20×")
    return reasons


def compare(a_records: list[dict[str, Any]], b_records: list[dict[str, Any]]) -> dict[str, Any]:
    """§21's gate on two runs files, A the incumbent and B the candidate. Calls nothing."""
    out: dict[str, Any] = {"invalid": [], "verdict": None, "failed": [], "close": [], "figures": None,
                           "models": (None, None)}
    pa, ia = _identity("A", a_records)
    pb, ib = _identity("B", b_records)
    out["invalid"] += pa + pb
    if ia and ib:
        out["models"] = (ia["model"], ib["model"])
        if ia["model"] == ib["model"]:
            out["invalid"].append(f"A and B have the same model ({ia['model']})")
        for key in ("level", "commit"):
            if ia[key] != ib[key]:
                out["invalid"].append(f"A and B differ in {key}")
    if out["invalid"]:
        return out
    a = [r for r in a_records if r["reviewed"] and not r.get("smoke")]
    b = [r for r in b_records if r["reviewed"] and not r.get("smoke")]

    def runs_per(records: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in records:
            counts[r["scenario"]] = counts.get(r["scenario"], 0) + 1
        return counts
    ca, cb = runs_per(a), runs_per(b)
    if set(ca) != set(cb):
        out["invalid"].append("A and B have different scenarios: " + ", ".join(sorted(set(ca) ^ set(cb))))
    elif ca != cb:
        out["invalid"].append("A and B have a different number of runs per scenario: "
                              + ", ".join(sorted(k for k in ca if ca[k] != cb[k])))
    for side, records in (("A", a), ("B", b)):
        bad = sorted({f"{r['scenario']} ({_status(r)})" for r in records if _status(r) in ("setup_failed", "blocked")})
        if bad:
            out["invalid"].append(f"{side} has setup_failed or blocked runs: {', '.join(bad)}")
        for name in SETS:
            if not any(record_set(r) == name for r in records):
                out["invalid"].append(f"{side} has no reviewed {name}-set runs")
    if out["invalid"]:
        return out
    out["figures"] = (_by_set(a), _by_set(b))
    out["failed"] = gate(*out["figures"])
    out["verdict"] = "A stays" if out["failed"] else "B wins"
    out["close"] = close_call(a, b)
    return out


def compare_path(a: Path, b: Path, out_dir: Path | None) -> Path:
    return (out_dir or a.parent) / f"compare-{a.stem}-vs-{b.stem}.md"


def compare_report(result: dict[str, Any], a_name: str, b_name: str) -> str:
    ma, mb = result["models"]
    lines = ["# Model A/B compare", "", f"- A (incumbent): `{a_name}`, model {ma or '—'}",
             f"- B (candidate): `{b_name}`, model {mb or '—'}", ""]
    if result["invalid"]:
        return "\n".join([*lines, "**Verdict: invalid**, no verdict:", "",
                          *(f"- {x}" for x in result["invalid"]), ""])
    verdict = f"**Verdict: {result['verdict']}** (A {ma}, B {mb})"
    verdict += "; close: rerun at 5" if result["close"] else "; clear"
    lines += [verdict, ""]
    lines += [f"- failed: {x}" for x in result["failed"]] + [f"- close: {x}" for x in result["close"]]
    lines += ["", "| Set | File | Passes | Runs | Pass rate | Median wall | p90 wall | Calls | Repairs | "
              "Write attempted | Prompt | Cached | Output |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name in SETS:
        for side, figs in zip(("A", "B"), result["figures"], strict=True):
            f = figs[name]
            lines.append(
                f"| {name} | {side} | {f['passes']} | {f['runs']} | {float(f['rate']):.0%} | {_fmt(f['wall_median'], ' s')} | "
                f"{_fmt(f['wall_p90'], ' s')} | {_fmt(f['calls_mean'])} | {f['repairs']} | {f['write_attempted']} | "
                f"{_fmt(f['prompt_mean'], '', 0)} | {_fmt(f['cached_mean'], '', 0)} | {_fmt(f['output_mean'], '', 0)} |")
    lines.append("")
    return "\n".join(lines)


def write_compare(a: Path, b: Path, out_dir: Path | None = None) -> Path:
    out = compare_path(a, b, out_dir)
    for src in (a, b):
        if out.resolve() == src.resolve():
            raise ScenarioError(f"the compare would overwrite {src.name}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(compare_report(compare(read_records(a), read_records(b)), a.name, b.name), encoding="utf-8")
    return out


# --- entry point -----------------------------------------------------------------------------------


async def _main_run(args: argparse.Namespace) -> None:
    load_dotenv(ec.ENV_FILE, override=False)
    settings = load_settings(os.environ, EXPLORER_ROOT)
    path = args.file or settings.dataset_dir / "scenarios.toml"
    keyless = {b.name for b in settings.dataset.boards.values() if not b.key}
    chosen = select(load(path, keyless), [i.strip() for i in args.ids.split(",") if i.strip()] if args.ids else None,
                    args.include_unreviewed)
    if not chosen:
        raise SystemExit("no scenarios selected (unreviewed ones need --include-unreviewed)")
    allowed = tuple(sorted(settings.dataset.allowed_schemas))
    pool = open_pool(settings.database_url)
    await pool.open(wait=True, timeout=10)
    try:
        runner = SqlRunner(pool, allowed, settings.sql_timeout_ms, settings.sql_row_cap)
        out_dir = args.out or DEFAULT_OUT_ROOT / settings.dataset.name
        commit = repo_commit([ec.AGENT_DIR, settings.dataset_dir])
        await run(chosen, args.runs, args.level, out_dir, runner, allowed, model=settings.model, commit=commit)
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
    c = sub.add_parser("compare", help="apply the model A/B gate to two runs files (A the incumbent)")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)
    c.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "run":
            asyncio.run(_main_run(args))
        elif args.cmd == "boards":
            asyncio.run(_main_boards(args))
        elif args.cmd == "compare":
            print(f"wrote {write_compare(args.a, args.b, args.out)}")
        else:
            out = report_path_for(args.runs)
            out.write_text(report(read_records(args.runs)), encoding="utf-8")
            print(f"wrote {out}")
    except ScenarioError as e:
        raise SystemExit(f"scenarios: {e}") from None


if __name__ == "__main__":
    main()
