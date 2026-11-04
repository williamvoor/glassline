"""Reasoning effort check harness (explorer/specs/effort-check/SPEC.md).

    uv run python scripts/effort_check.py run [--levels minimal,low] [--runs 3] [--questions 1,2,3,4,5]
    uv run python scripts/effort_check.py sheet RUNS.jsonl [--out PATH]

`run` starts the real agent once per effort level, asks the fixed questions over A2A, and writes one
JSONL record per question with the prose, the Surface's numbers and SQL, and the token counts, SQL
attempts and failures from the agent's log (ADR 0012). `sheet` turns the JSONL into a Markdown sheet
for scoring by hand. The harness never scores. It calls the real model, so it is not a test.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import socket
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx
from dotenv import dotenv_values

from explorer_agent.catalog import EXPLORER_CATALOG_ID
from explorer_agent.executor import ACTION_FAILED_KEY, GENERIC_ERROR, MODEL_KEY, UI_ERROR_PREFIX
from explorer_agent.ui import BARE_NUMBERS_LOG
from explorer_agent.llm import (
    CONTENT_POLICY_PROSE,
    DRILLS_DROPPED_LOG,
    MODEL_FAILED_PROSE,
    NO_VIEW_PROSE,
    REPAIR_STATUS,
    STEP_LIMIT_PROSE,
    TIMEOUT_PROSE,
)

AGENT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AGENT_DIR.parents[1]
DEFAULT_OUT = REPO_ROOT / "docs" / "effort-check"
ENV_FILE = REPO_ROOT / ".env"

EXT_URI = "https://a2ui.org/a2a-extension/a2ui/v0.9"
A2UI_MIME_TYPES = {"application/a2ui+json", "application/json+a2ui"}
CAPS = {"a2uiClientCapabilities": {"v0.9": {"supportedCatalogIds": [EXPLORER_CATALOG_ID]}}}
READ_TIMEOUT_S = 180
STARTUP_TIMEOUT_S = 60
STOP_TIMEOUT_S = 10
LOG_SETTLE_S = 0.3
SHEET_ROW_CAP = 20

# explorer/SPEC.md §10, as amended on 2026-11-02.
QUESTIONS: dict[int, str] = {
    1: "How much contract value did the Department of Defence award in calendar year 2014, "
       "and across how many contracts?",
    2: "Which 5 suppliers received the most value from limited tenders in 2022-23?",
    3: "Which 10 contracts published in 2023-24 grew the most through amendments, from what to what?",
    4: "How has total contract value changed by financial year since the data starts?",
    5: "What share of 2024-25 contract value went to overseas suppliers?",
}
WARMUP_QUESTION = "How many contracts are in the dataset?"


# --- schedule --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Ask:
    question: int  # 0 = warm-up
    text: str
    run: int
    warmup: bool


def schedule(runs: int, questions: list[int]) -> list[Ask]:
    """Warm-up first, then rounds: round r asks each question once."""
    asks = [Ask(0, WARMUP_QUESTION, 0, True)]
    for r in range(1, runs + 1):
        asks += [Ask(q, QUESTIONS[q], r, False) for q in questions]
    return asks


# --- failure classification ------------------------------------------------------------------

_MODEL_FAILED_PREFIX = MODEL_FAILED_PROSE.split("{", 1)[0]


def classify_failure(prose: str) -> str:
    """The failure type the agent's final prose signals, or "none".

    UI, model and generic errors replace the whole prose. Timeout, step limit and failed Repairs
    keep any partial prose and put the failure sentence after it, as its own paragraph
    (`LlmLoop._give_up`), so those are matched at the start of any paragraph.
    """
    text = prose.strip()
    if text.startswith(UI_ERROR_PREFIX):
        return "ui_error"
    if text.startswith(_MODEL_FAILED_PREFIX):
        return "model_failed"
    if text == CONTENT_POLICY_PROSE:
        return "content_policy"
    if text == GENERIC_ERROR:
        return "error"
    paragraphs = [p.strip() for p in text.split("\n\n")]
    for marker, kind in ((TIMEOUT_PROSE, "timeout"), (STEP_LIMIT_PROSE, "step_limit"), (NO_VIEW_PROSE, "no_view")):
        if any(p.startswith(marker) for p in paragraphs):
            return kind
    return "none"


# --- one question over A2A -------------------------------------------------------------------


def _envelope(message: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/stream", "params": {"message": message}}


def message_body(text: str, context_id: str | None = None) -> dict[str, Any]:
    message = {
        "kind": "message", "role": "user", "messageId": str(uuid.uuid4()),
        "parts": [{"kind": "text", "text": text}], "metadata": CAPS,
    }
    if context_id:  # continue a Conversation (scenarios with several turns)
        message["contextId"] = context_id
    return _envelope(message)


def action_body(
    name: str, surface_id: str, component_id: str, context: dict[str, Any], context_id: str
) -> dict[str, Any]:
    """The A2UI action message the client sends for a click (client/src/a2a.ts `act`)."""
    action = {
        "name": name, "surfaceId": surface_id, "sourceComponentId": component_id,
        "timestamp": datetime.now(UTC).isoformat(), "context": context,
    }
    return _envelope({
        "kind": "message", "role": "user", "messageId": str(uuid.uuid4()), "contextId": context_id,
        "parts": [{"kind": "data", "data": {"version": "v0.9", "action": action},
                   "metadata": {"mimeType": "application/json+a2ui"}}],
        "metadata": CAPS,
    })


def _texts(parts: list[dict[str, Any]]) -> str:
    return "\n\n".join(p["text"] for p in parts if p.get("kind") == "text")


async def _exchange(client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    """Sends one message and reads the stream. Never raises for transport problems."""
    statuses: list[str] = []
    final: dict[str, Any] | None = None
    start = time.monotonic()
    error = ""
    try:
        async with client.stream("POST", "/", json=body, headers={"X-A2A-Extensions": EXT_URI}) as r:
            if r.status_code != 200:
                error = f"HTTP {r.status_code}"
            else:
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = json.loads(line[5:])
                    if "error" in payload:
                        error = f"JSON-RPC error: {payload['error']}"
                        break
                    event = payload.get("result", {})
                    if event.get("kind") != "status-update":
                        continue
                    parts = (event.get("status", {}).get("message") or {}).get("parts", [])
                    if event.get("final"):
                        final = event
                        break
                    if text := _texts(parts):
                        statuses.append(text)
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        error = f"{type(e).__name__}: {e}"
    wall_s = round(time.monotonic() - start, 3)

    if final is None:
        return {
            "wall_s": wall_s, "statuses": statuses, "repairs": statuses.count(REPAIR_STATUS),
            "prose": error or "stream ended without a final event", "failure": "transport",
            "surface": surface_summary([]), "metadata": {},
        }
    status = final.get("status") or {}
    message = status.get("message")
    if status.get("state") != "completed" or not isinstance(message, dict):
        return {
            "wall_s": wall_s, "statuses": statuses, "repairs": statuses.count(REPAIR_STATUS),
            "prose": f"final event not completed with a message (state {status.get('state')!r})",
            "failure": "transport", "surface": surface_summary([]), "metadata": {},
        }
    parts = message.get("parts") or []
    prose = _texts(parts)
    a2ui = [p["data"] for p in parts if p.get("kind") == "data" and (p.get("metadata") or {}).get("mimeType") in A2UI_MIME_TYPES]
    return {
        "wall_s": wall_s, "statuses": statuses, "repairs": statuses.count(REPAIR_STATUS),
        "prose": prose, "failure": classify_failure(prose), "surface": surface_summary(a2ui),
        "context_id": final.get("contextId"), "metadata": message.get("metadata") or {},
    }


async def ask(client: httpx.AsyncClient, text: str, context_id: str | None = None) -> dict[str, Any]:
    """Sends `text` in a fresh Conversation, or in `context_id`'s. Never raises for transport
    problems. The result's `context_id` is the reply's, for a following Turn."""
    return await _exchange(client, message_body(text, context_id))


async def act(
    client: httpx.AsyncClient, name: str, surface_id: str, component_id: str, context: dict[str, Any],
    context_id: str,
) -> dict[str, Any]:
    """Sends a click (SPEC §17). Like `ask`, plus the final message's `metadata`; an Action the
    agent rejects (`explorerActionFailed`) has `failure: "action_failed"`."""
    result = await _exchange(client, action_body(name, surface_id, component_id, context, context_id))
    if result["failure"] == "none" and result["metadata"].get(ACTION_FAILED_KEY):
        result["failure"] = "action_failed"
    return result


def chart_values(spec: dict[str, Any]) -> list[Any]:
    """A chart's data values: Vega-Lite's `data.values`, or each full-Vega `data[]` entry's
    `values` (a treemap, SPEC §18)."""
    data = spec.get("data")
    if isinstance(data, dict):
        return list(data.get("values") or [])
    if isinstance(data, list):
        return [v for d in data if isinstance(d, dict) for v in d.get("values") or []]
    return []


def surface_summary(a2ui: list[dict[str, Any]]) -> dict[str, Any]:
    """Metrics, tables, chart values, SQL and clickable Visuals from the Surface's components."""
    components: list[dict[str, Any]] = []
    surface_of: dict[str, str] = {}
    for m in a2ui:
        update = m.get("updateComponents") or {}
        for c in update.get("components", []):
            components.append(c)
            if "id" in c:
                surface_of[c["id"]] = update.get("surfaceId", "")
    by_id = {c["id"]: c for c in components if "id" in c}
    parent: dict[str, dict[str, Any]] = {}
    for c in components:
        for child in c.get("children", []) if isinstance(c.get("children"), list) else []:
            parent[child] = c

    def sibling_title(cid: str) -> str:
        p = parent.get(cid)
        for sid in (p or {}).get("children", []):
            s = by_id.get(sid, {})
            if s.get("component") == "Text" and s.get("variant") == "h4":
                return s.get("text", "")
        return ""

    metrics, tables, charts, sql, visuals = [], [], [], [], []
    for c in components:
        kind = c.get("component")
        if kind in ("DataGrid", "VegaChart"):  # SPEC §17: what a click on this Visual would do
            action = c.get("action") if isinstance(c.get("action"), dict) else None
            visuals.append({
                "kind": "table" if kind == "DataGrid" else "chart", "id": c.get("id"),
                "surface_id": surface_of.get(c.get("id", ""), ""), "title": sibling_title(c["id"]),
                "clickable": action is not None,
                # A Visual whose rows open an Entity Board counts as drillable (SPEC §18 slice 3).
                "drillable": bool(action and (action.get("drillable") or action.get("boards"))),
                "source": (action or {}).get("source"), "rows": list((action or {}).get("rows") or []),
            })
        if kind == "DataGrid":
            tables.append({
                "title": sibling_title(c["id"]), "columns": [col.get("label", col.get("key")) for col in c.get("columns", [])],
                "rows": c.get("rows", []), "caption": c.get("caption"),
            })
        elif kind == "VegaChart":
            charts.append({"title": sibling_title(c["id"]), "values": chart_values(c.get("spec") or {})})
        elif kind == "SqlDetails":
            sql.append({"summary": c.get("summary", ""), "sql": c.get("sql", "")})
        elif kind == "Card":  # what Metric expands to: Card > Column > [caption, h2, SqlDetails]
            kids = [by_id.get(k, {}) for k in by_id.get(c.get("child", ""), {}).get("children", [])]
            label = next((k.get("text") for k in kids if k.get("component") == "Text" and k.get("variant") == "caption"), None)
            value = next((k.get("text") for k in kids if k.get("component") == "Text" and k.get("variant") == "h2"), None)
            if label is not None and value is not None:
                metrics.append([label, value])
    return {"metrics": metrics, "tables": tables, "charts": charts, "sql": sql, "visuals": visuals, "a2ui": a2ui}


# --- agent log ---------------------------------------------------------------------------------

_LOG_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} (DEBUG|INFO|WARNING|ERROR|CRITICAL) (\S+): (.*)$")
_MODEL_CALL = re.compile(r"model call: .*?prompt=(\S+) cached=(\S+) completion=(\S+) reasoning=(\S+) latency_ms=(\d+)")
_RUN_SQL = re.compile(r"run_sql \((.*?)\): (.*)", re.DOTALL)
_SQL_FAILED = re.compile(r"run_sql failed: (\S+) ?(.*)", re.DOTALL)
_BARE = BARE_NUMBERS_LOG
_LISTENING = "explorer: listening on "  # the agent's startup line on stdout


def _int(text: str) -> int | None:
    try:
        return int(text.replace(",", ""))
    except ValueError:
        return None


def log_records(text: str) -> list[tuple[str, str, str]]:
    """(level, logger, message) per record; lines without a log prefix continue the previous one."""
    records: list[list[str]] = []
    for line in text.splitlines():
        m = _LOG_LINE.match(line)
        if m:
            records.append([m[1], m[2], m[3]])
        elif records:
            records[-1][2] += "\n" + line
        else:
            records.append(["", "", line])
    return [(lvl, name, msg) for lvl, name, msg in records]


def parse_log_slice(text: str) -> dict[str, Any]:
    """Model calls, SQL attempts, bare numbers and other warnings from one run's log lines.

    Parallel tool calls (`asyncio.gather`) interleave their lines, and the outcome lines don't
    name their query, so an attempt's `ok` is set only when it was the one pending attempt.
    Overlapping attempts are marked `parallel` with `ok: None`; `sql_failures` still counts all.
    """
    model_calls: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    failures = 0
    bare: list[str] = []
    others: list[str] = []
    dropped = 0

    def settle(**outcome: Any) -> None:
        if len(pending) == 1 and not pending[0].get("parallel"):
            pending[0].update(outcome)
        if pending:
            pending.pop(0)

    for level, name, msg in log_records(text):
        if m := _MODEL_CALL.match(msg):
            model_calls.append({
                "prompt": _int(m[1]), "cached": _int(m[2]), "completion": _int(m[3]),
                "reasoning": _int(m[4]), "latency_ms": int(m[5]),
            })
        elif msg.startswith("run_sql failed: "):
            failures += 1
            fm = _SQL_FAILED.match(msg)
            settle(ok=False, sqlstate=fm[1] if fm else "", message=fm[2].strip() if fm else msg)
        elif m := _RUN_SQL.match(msg):
            attempt = {"purpose": m[1], "sql": m[2].strip(), "ok": None}
            if pending:
                for a in (*pending, attempt):
                    a["parallel"] = True
            attempts.append(attempt)
            pending.append(attempt)
        elif msg.startswith("sql ok: "):
            settle(ok=True)
        elif msg.startswith(DRILLS_DROPPED_LOG):  # SPEC §17: Drill queries the checks rejected
            dropped += len([v for v in msg[len(DRILLS_DROPPED_LOG):].split(",") if v.strip()])
        elif msg.startswith(_BARE):
            bare += [n.strip() for n in msg[len(_BARE):].split(",") if n.strip()]
        elif level in ("WARNING", "ERROR", "CRITICAL") or (
            not level and msg.strip() and not msg.startswith(_LISTENING)
        ):
            others.append(f"{level} {name}: {msg}".strip())
    return {
        "model_calls": model_calls, "sql_attempts": attempts, "sql_failures": failures,
        "bare_numbers": bare, "log_errors": others, "drills_dropped": dropped,
    }


def read_slice(path: Path, start: int, end: int) -> str:
    with path.open("rb") as f:
        f.seek(start)
        return f.read(end - start).decode("utf-8", errors="replace")


# --- agent process -------------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def agent_env(base: dict[str, str], level: str, port: int) -> dict[str, str]:
    env = dict(base)
    env.update(REASONING_EFFORT=level, AGENT_PORT=str(port), AGENT_HOST="127.0.0.1", LOG_LEVEL="INFO")
    return env


@dataclass
class Agent:
    level: str
    log_path: Path
    port: int = field(default_factory=free_port)
    proc: subprocess.Popen[bytes] | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        log = self.log_path.open("ab")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "explorer_agent"], cwd=AGENT_DIR,
            env=agent_env(dict(os.environ), self.level, self.port), stdout=log, stderr=subprocess.STDOUT,
        )
        log.close()
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        async with httpx.AsyncClient(timeout=2) as c:
            while time.monotonic() < deadline:
                if self.proc.poll() is not None:
                    break
                try:
                    if (await c.get(f"{self.url}/.well-known/agent-card.json")).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
        tail = self.log_path.read_text(errors="replace").splitlines()[-15:]
        self.stop()
        raise SystemExit(f"agent ({self.level}) did not start; last log lines:\n" + "\n".join(tail))

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


# --- secrets -------------------------------------------------------------------------------------


def secret_values(environ: dict[str, str], env_file: Path) -> list[str]:
    """META_API_KEY and the DATABASE_URL password (raw and decoded) from the environment and the
    agent's env file (`EXPLORER_ENV_FILE`, else the repo root `.env`). Values shorter than 6
    characters are skipped, with a warning that never shows the value."""
    sources = [environ]
    path = Path(environ.get("EXPLORER_ENV_FILE") or env_file)
    if path.exists():
        sources.append({k: v or "" for k, v in dotenv_values(path).items()})
    values: set[str] = set()
    for src in sources:
        if key := src.get("META_API_KEY"):
            values.add(key)
        if dsn := src.get("DATABASE_URL"):
            if password := urlsplit(dsn).password:
                values |= {password, unquote(password)}
    short = [v for v in values if len(v) < 6]
    if short:
        print(f"warning: {len(short)} secret value(s) too short to scan for", file=sys.stderr)
    return [v for v in values if len(v) >= 6]


def secret_hits(paths: list[Path], secrets: list[str]) -> list[Path]:
    """Files containing any secret value. Never returns or prints the value."""
    return [p for p in paths if p.exists() and any(s in p.read_text(errors="replace") for s in secrets)]


# --- run -----------------------------------------------------------------------------------------


async def run_level(level: str, asks: list[Ask], stamp: str, out_dir: Path, jsonl: Path, written: list[Path]) -> None:
    log_path = out_dir / f"agent-{level}-{stamp}.log"
    written.append(log_path)
    agent = Agent(level, log_path)
    await agent.start()
    try:
        timeout = httpx.Timeout(connect=10, read=READ_TIMEOUT_S, write=30, pool=10)
        async with httpx.AsyncClient(base_url=agent.url, timeout=timeout) as client:
            for a in asks:
                label = "warm-up" if a.warmup else f"Q{a.question} run {a.run}"
                print(f"[{level}] {label} …", flush=True)
                record = await run_one(client, a, level, log_path)
                with jsonl.open("a") as f:
                    f.write(json.dumps(record) + "\n")
                print(f"[{level}] {label}: {record['failure']}, {record['wall_s']:.1f} s", flush=True)
    finally:
        agent.stop()


async def run_one(client: httpx.AsyncClient, a: Ask, level: str, log_path: Path) -> dict[str, Any]:
    started = datetime.now(UTC).isoformat(timespec="seconds")
    before = log_path.stat().st_size
    result = await ask(client, a.text)
    await asyncio.sleep(LOG_SETTLE_S)
    after = log_path.stat().st_size
    return {
        "level": level, "question": a.question, "question_text": a.text, "run": a.run, "warmup": a.warmup,
        "started_at": started, **result, **parse_log_slice(read_slice(log_path, before, after)),
    }


async def run(levels: list[str], runs: int, questions: list[int], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    jsonl = out_dir / f"runs-{stamp}.jsonl"
    written = [jsonl]
    asks = schedule(runs, questions)
    try:
        for level in levels:
            await run_level(level, asks, stamp, out_dir, jsonl, written)
    finally:  # scan on every exit, so files left by a failed run are checked too
        hits = secret_hits(written, secret_values(dict(os.environ), ENV_FILE))
        if hits:
            raise SystemExit("secret found in: " + ", ".join(str(p) for p in hits) + " (do not commit these files)")
    print(f"wrote {jsonl}")
    return jsonl


# --- sheet ---------------------------------------------------------------------------------------


def summary(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per level, over scored runs only."""
    out: dict[str, dict[str, Any]] = {}
    for level in dict.fromkeys(r["level"] for r in records):
        scored = [r for r in records if r["level"] == level and not r["warmup"]]
        calls = [c for r in scored for c in r.get("model_calls", [])]
        prompt = sum(c["prompt"] or 0 for c in calls)
        cached = sum(c["cached"] or 0 for c in calls)
        # Cache rate only over calls that report both counts.
        known = [c for c in calls if c["prompt"] is not None and c["cached"] is not None]
        known_prompt = sum(c["prompt"] for c in known)
        walls = [r["wall_s"] for r in scored]
        out[level] = {
            "runs": len(scored),
            "median_wall_s": round(statistics.median(walls), 1) if walls else None,
            "max_wall_s": round(max(walls), 1) if walls else None,
            "model_calls": len(calls),
            "prompt_tokens": prompt,
            "cached_tokens": cached,
            "cache_rate": round(100 * sum(c["cached"] for c in known) / known_prompt, 1) if known_prompt else None,
            "failures": sum(r["failure"] != "none" for r in scored),
        }
    return out


def _cell(v: Any) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def _md_table(header: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(_cell(h) for h in header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return lines


def render_sheet(records: list[dict[str, Any]], source: str) -> str:
    lines = ["# Reasoning effort check: scoring sheet", "", f"Runs: `{source}`. Reference: "
             "[`reference-values.md`](reference-values.md). Score each run by hand; the harness never scores.", "",
             "## Summary (scored runs; warm-ups left out)", ""]
    s = summary(records)
    lines += _md_table(
        ["Level", "Runs", "Median s", "Max s", "Model calls", "Prompt tokens", "Cached tokens", "Cache %", "Failures"],
        [[lvl, v["runs"], v["median_wall_s"], v["max_wall_s"], v["model_calls"], v["prompt_tokens"],
          v["cached_tokens"], v["cache_rate"], v["failures"]] for lvl, v in s.items()],
    )
    for q in sorted({r["question"] for r in records if not r["warmup"]}):
        runs = [r for r in records if r["question"] == q and not r["warmup"]]
        lines += ["", f"## Q{q}. {runs[0]['question_text']}", "", f"Reference: [Q{q}](reference-values.md#q{q})"]
        for r in sorted(runs, key=lambda r: (r["level"], r["run"])):
            lines += ["", f"### Q{q} · {r['level']} · run {r['run']}", "",
                      f"Failure: `{r['failure']}` · {r['wall_s']:.1f} s · {len(r.get('model_calls', []))} model calls · "
                      f"{r['repairs']} repairs · bare numbers: {', '.join(r.get('bare_numbers', [])) or 'none'}", "",
                      "**Prose:**", "", *[f"> {line}" for line in r["prose"].splitlines() or [""]]]
            surf = r["surface"]
            if surf["metrics"]:
                lines += ["", "**Metrics:** " + "; ".join(f"{label}: {value}" for label, value in surf["metrics"])]
            for t in surf["tables"]:
                lines += ["", f"**Table: {t['title']}**" + (f" ({t['caption']})" if t.get("caption") else ""), ""]
                lines += _md_table(t["columns"], t["rows"][:SHEET_ROW_CAP])
                if len(t["rows"]) > SHEET_ROW_CAP:
                    lines.append(f"\n… {len(t['rows']) - SHEET_ROW_CAP} more rows")
            for c in surf["charts"]:
                vals = c["values"][:SHEET_ROW_CAP]
                lines += ["", f"**Chart: {c['title']}**", ""]
                if vals:
                    keys = list(dict.fromkeys(k for v in vals for k in v))
                    lines += _md_table(keys, [[v.get(k, "") for k in keys] for v in vals])
                if len(c["values"]) > SHEET_ROW_CAP:
                    lines.append(f"\n… {len(c['values']) - SHEET_ROW_CAP} more values")
            for sq in surf["sql"]:
                lines += ["", f"<details><summary>SQL: {sq['summary']}</summary>", "", "```sql", sq["sql"], "```", "</details>"]
            lines += ["", "Score: "]
    return "\n".join(lines) + "\n"


def sheet(jsonl: Path, out: Path) -> Path:
    records = [json.loads(line) for line in jsonl.read_text().splitlines() if line.strip()]
    out.write_text(render_sheet(records, jsonl.name))
    print(f"wrote {out}")
    return out


# --- CLI -----------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="ask the questions against the real agent")
    r.add_argument("--levels", default="minimal,low")
    r.add_argument("--runs", type=int, default=3)
    r.add_argument("--questions", default=",".join(map(str, QUESTIONS)))
    r.add_argument("--out", type=Path, default=DEFAULT_OUT)
    s = sub.add_parser("sheet", help="write the scoring sheet from a runs file")
    s.add_argument("runs", type=Path)
    s.add_argument("--out", type=Path, default=DEFAULT_OUT / "scoring-sheet.md")
    args = parser.parse_args(argv)
    if args.cmd == "run":
        questions = [int(q) for q in args.questions.split(",") if q.strip()]
        unknown = [q for q in questions if q not in QUESTIONS]
        if unknown:
            parser.error(f"unknown questions: {unknown}")
        asyncio.run(run([lvl.strip() for lvl in args.levels.split(",") if lvl.strip()], args.runs, questions, args.out))
    else:
        sheet(args.runs, args.out)


if __name__ == "__main__":
    main()
