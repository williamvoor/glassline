"""The draft step: a short LiteLLM tool loop that ends with a `submit_draft` call (ADR 0023).

Muse Wonder is allowed here and nowhere in the explorer's request path (ADR 0020).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import explorer_agent  # noqa: F401  (sets LiteLLM's env defaults before litellm is imported)
import litellm
from explorer_agent.results import to_json_safe
from explorer_agent.sql import SqlFail, SqlRunner

from explorer_bootstrap.draft_schema import TOOLS, validate_draft_args
from explorer_bootstrap.settings import DraftSettings

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "draft.md"
USER_MESSAGE = "Draft the dataset files, the missing comments and the claims for this database."
NUDGE = "Call submit_draft with the draft."
_KEYLIKE = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")

Completion = Callable[..., Awaitable[Any]]
EventFn = Callable[[dict[str, Any]], None]


class DraftError(Exception):
    """The draft could not be produced. The message never holds a key or the DSN."""


def build_draft_prompt(report: dict[str, Any]) -> str:
    """Instructions, then the report. Stable for the same report: no dates, IDs or paths."""
    instructions = PROMPT_PATH.read_text(encoding="utf-8").strip()
    data = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return f"{instructions}\n\n## Report\n{data}\n"


def cache_key(report: dict[str, Any]) -> str:
    return "bootstrap-" + (report.get("dataset") or "-".join(report["schemas"]))


@dataclass
class _Usage:
    calls: list[dict[str, Any]]

    def add(self, response: Any, latency_ms: int, settings: DraftSettings) -> dict[str, Any]:
        u = getattr(response, "usage", None)
        prompt = getattr(u, "prompt_tokens", None) or 0
        completion = getattr(u, "completion_tokens", None) or 0
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", None) or 0
        reasoning = getattr(getattr(u, "completion_tokens_details", None), "reasoning_tokens", None) or 0
        p = settings.prices
        cost = ((prompt - cached) * p.input + cached * p.cached_input + completion * p.output) / 1_000_000
        call = {"prompt_tokens": prompt, "cached_tokens": cached, "completion_tokens": completion,
                "reasoning_tokens": reasoning, "latency_ms": latency_ms, "cost_usd": round(cost, 6)}
        self.calls.append(call)
        return call

    def totals(self) -> dict[str, Any]:
        keys = ("prompt_tokens", "cached_tokens", "completion_tokens", "reasoning_tokens", "latency_ms")
        t: dict[str, Any] = {k: sum(c[k] for c in self.calls) for k in keys}
        t["cost_usd"] = round(sum(c["cost_usd"] for c in self.calls), 6)
        t["calls"] = len(self.calls)
        return t

    def to_dict(self) -> dict[str, Any]:
        return {"calls": list(self.calls), "totals": self.totals()}


def _scrub(text: str) -> str:
    key = os.environ.get("META_API_KEY", "").strip()
    if key:
        text = text.replace(key, "[redacted]")
    return _KEYLIKE.sub("[redacted]", text)[:300]


def _assistant_message(message: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"role": "assistant", "content": message.content}
    if message.tool_calls:
        out["tool_calls"] = [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in message.tool_calls
        ]
    return out


async def _run_sql(runner: SqlRunner, args: Any, rows_to_model: int) -> dict[str, Any]:
    if not isinstance(args, dict) or not isinstance(args.get("sql"), str) or not args["sql"].strip():
        return {"error": {"message": "run_sql needs a 'sql' string"}}
    outcome = await runner.run(args["sql"])
    if isinstance(outcome, SqlFail):
        return {"error": {"sqlstate": outcome.sqlstate, "message": outcome.message}}
    rows = [{k: to_json_safe(v) for k, v in r.items()} for r in outcome.rows[:rows_to_model]]
    return {"columns": outcome.columns, "rows": rows, "row_count": len(outcome.rows), "truncated": outcome.truncated}


def _tool_message(call_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload, ensure_ascii=False, default=str)}


SubmitCheck = Callable[[str], Awaitable[tuple[Any, list[str]]]]


async def tool_loop(
    *,
    system: str,
    user: str,
    tools: list[dict[str, Any]],
    prompt_cache_key: str,
    submit: str,
    check_submit: SubmitCheck,
    nudge: str,
    what: str,
    runner: SqlRunner,
    settings: DraftSettings,
    completion: Completion | None = None,
    on_event: EventFn | None = None,
) -> tuple[Any, dict[str, Any]]:
    """The bootstrap's tool loop (ADR 0023), shared by the dataset draft and the Boards draft:
    `run_sql` calls run, and the loop ends at a `submit` call that `check_submit` (raw arguments ->
    (arguments, problems)) passes. Problems go back as the tool error. Raises DraftError."""
    completion = completion or litellm.acompletion
    emit = on_event or (lambda _e: None)
    usage = _Usage([])
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    request = {
        "model": settings.model,
        "tools": tools,
        "prompt_cache_key": prompt_cache_key,
        "reasoning_effort": settings.effort,
        "max_tokens": settings.max_tokens,
        "num_retries": 0,
    }
    nudged = False
    for _ in range(settings.max_tool_iterations):
        started = time.monotonic()
        try:
            async with asyncio.timeout(settings.call_timeout_s):
                response = await completion(messages=messages, **request)
        except TimeoutError:
            raise DraftError(f"the model call took longer than {settings.call_timeout_s}s") from None
        except Exception as e:
            raise DraftError(f"model call failed: {type(e).__name__}: {_scrub(str(e))}") from None
        call = usage.add(response, int((time.monotonic() - started) * 1000), settings)
        log.info(
            "model call: model=%s prompt=%d cached=%d completion=%d reasoning=%d latency_ms=%d cost_usd=%.4f",
            settings.model, call["prompt_tokens"], call["cached_tokens"], call["completion_tokens"],
            call["reasoning_tokens"], call["latency_ms"], call["cost_usd"],
        )
        emit({"type": "call", "usage": call})
        message = response.choices[0].message
        tool_calls = list(message.tool_calls or [])
        if not tool_calls:
            messages.append({"role": "assistant", "content": message.content or ""})
            if nudged:
                raise DraftError(f"the model stopped without calling {submit}")
            messages.append({"role": "user", "content": nudge})
            nudged = True
            continue
        messages.append(_assistant_message(message))
        submits = [tc for tc in tool_calls if tc.function.name == submit]
        if submits:
            tc = submits[0]
            args, problems = await check_submit(tc.function.arguments or "{}")
            if not problems:
                return args, usage.to_dict()
            emit({"type": f"invalid_{what.lower()}", "problems": problems})
            replies = {t.id: {"error": {"message": f"ignored: {submit} was called"}} for t in tool_calls}
            replies[tc.id] = {"error": {"message": f"the {what} is invalid; fix these and call {submit} again",
                                        "problems": problems}}
            messages.extend(_tool_message(t.id, replies[t.id]) for t in tool_calls)
            continue

        async def one(tc: Any) -> dict[str, Any]:
            if tc.function.name != "run_sql":
                return {"error": {"message": f"unknown tool {tc.function.name!r}; use run_sql or {submit}"}}
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as e:
                return {"error": {"message": f"arguments are not valid JSON: {e.msg}"}}
            if isinstance(args, dict):
                emit({"type": "sql", "purpose": str(args.get("purpose") or ""), "sql": str(args.get("sql") or "")})
            try:
                return await _run_sql(runner, args, settings.rows_to_model)
            except Exception as e:  # e.g. PoolTimeout, which isn't a psycopg.Error: never end the draft
                log.warning("run_sql failed unexpectedly: %s", type(e).__name__)
                return {"error": {"message": f"run_sql failed unexpectedly ({type(e).__name__})"}}

        results = await asyncio.gather(*(one(tc) for tc in tool_calls))
        messages.extend(_tool_message(tc.id, r) for tc, r in zip(tool_calls, results, strict=True))
    raise DraftError(f"no valid {what} after {settings.max_tool_iterations} model calls")


async def run_draft(
    report: dict[str, Any],
    runner: SqlRunner,
    settings: DraftSettings,
    *,
    completion: Completion | None = None,
    on_event: EventFn | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the loop. Returns (valid submit_draft arguments, usage). Raises DraftError."""

    async def check(raw: str) -> tuple[Any, list[str]]:
        try:
            args = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, [f"arguments are not valid JSON: {e.msg}"]
        return args, validate_draft_args(args, report)

    return await tool_loop(
        system=build_draft_prompt(report), user=USER_MESSAGE, tools=TOOLS, prompt_cache_key=cache_key(report),
        submit="submit_draft", check_submit=check, nudge=NUDGE, what="draft", runner=runner, settings=settings,
        completion=completion, on_event=on_event,
    )


def estimate_cost(report: dict[str, Any], settings: DraftSettings) -> dict[str, Any]:
    """A rough ceiling before running: ~1 token per 3 characters (high for English and JSON), the full
    output allowance on every call, no cache discount. Tool results add input it can't foresee."""
    return estimate_for(len(build_draft_prompt(report)) + len(USER_MESSAGE), settings)


def estimate_for(prompt_chars: int, settings: DraftSettings) -> dict[str, Any]:
    """`estimate_cost`'s arithmetic for any prompt size (the Boards loop's too, phase 3)."""
    tokens = -(-prompt_chars // 3)
    p = settings.prices
    first = (tokens * p.input + settings.max_tokens * p.output) / 1_000_000
    return {
        "model": settings.model,
        "prompt_chars": prompt_chars,
        "input_tokens_est": tokens,
        "max_calls": settings.max_tool_iterations,
        "first_call_usd": round(first, 4),
        "run_ceiling_usd": round(first * settings.max_tool_iterations, 4),
    }
