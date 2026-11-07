"""Traces (specs/trace/SPEC.md, ADR 0053): a debugging record of the model calls and SQL queries
behind one request, recorded only while the user has the Trace switched on.

A `Recorder` is installed for a request with `recording(recorder)`; code anywhere below it calls
`await emit(event)` or `sql_event(...)`. With no Recorder installed both do nothing, so the cost
of an untraced request is one context-variable lookup per model call and query.

A Trace never holds data rows: SQL events carry counts. Model events carry the request messages
as sent (with the run_sql row previews the model saw); `saved_form` replaces those previews with
`rows_omitted` for storage.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable, Iterator, Sequence
from datetime import UTC, datetime
from typing import Any

from explorer_agent.results import to_json_safe

log = logging.getLogger(__name__)

Event = dict[str, Any]
LiveFn = Callable[[Event], Awaitable[None]]

_recorder: contextvars.ContextVar[Recorder | None] = contextvars.ContextVar("explorer_trace", default=None)
_source: contextvars.ContextVar[str] = contextvars.ContextVar("explorer_trace_source", default="query")


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Recorder:
    """The Trace events of one request, in order. `live` (optional) is awaited with each event
    as it happens; a failing `live` never fails the request. Once `close`d, events are dropped:
    a background task that outlives its request (a Drill prefetch) records nothing."""

    def __init__(self, live: LiveFn | None = None, known_prompts: set[str] | None = None):
        self.events: list[Event] = []
        self.live = live
        self.closed = False
        self.known_prompts = known_prompts if known_prompts is not None else set()  # hashes already sent
        self.sent_messages = 0  # how many request messages the previous model event covered

    async def add(self, event: Event) -> None:
        if self.closed:
            return
        event = {"seq": len(self.events), "at": _now(), **event}
        self.events.append(event)
        if self.live is not None:
            try:
                await self.live(event)
            except Exception as e:  # the Trace is a debugging aid: never break the answer
                log.warning("trace event not delivered: %s", type(e).__name__)

    def close(self) -> None:
        self.closed = True


def active() -> Recorder | None:
    r = _recorder.get()
    return r if r is not None and not r.closed else None


@contextlib.contextmanager
def recording(recorder: Recorder | None) -> Iterator[Recorder | None]:
    """Installs `recorder` (None: tracing off) for the code inside, and closes it on exit."""
    token = _recorder.set(recorder)
    try:
        yield recorder
    finally:
        _recorder.reset(token)
        if recorder is not None:
            recorder.close()


def context_for(recorder: Recorder | None) -> contextvars.Context:
    """A copy of the current context with `recorder` installed, for a background task
    (`asyncio.create_task(..., context=...)`) that outlives the request starting it."""
    ctx = contextvars.copy_context()
    ctx.run(_recorder.set, recorder)
    return ctx


@contextlib.contextmanager
def source(name: str) -> Iterator[None]:
    """Names what runs the queries inside (`run_sql`, `drill`, `board`, …) for their SQL events."""
    token = _source.set(name)
    try:
        yield
    finally:
        _source.reset(token)


async def emit(event: Event) -> None:
    r = active()
    if r is not None:
        await r.add(event)


async def sql_event(sql: str, params: Sequence[Any] | None, *, ok: bool, row_count: int | None = None,
                    truncated: bool = False, elapsed_ms: int | None = None, sqlstate: str | None = None,
                    error: str | None = None) -> None:
    r = active()
    if r is None:
        return
    await r.add({
        "kind": "sql", "source": _source.get(), "sql": sql,
        "params": [to_json_safe(p) for p in params] if params is not None else None,
        "ok": ok, "row_count": row_count, "truncated": truncated, "elapsed_ms": elapsed_ms,
        "sqlstate": sqlstate, "error": error,
    })


async def model_event(model: str, messages: list[dict[str, Any]], *, reply: dict[str, Any] | None,
                      tokens: dict[str, Any] | None, latency_ms: int, error: str | None = None,
                      attempt: int = 1) -> None:
    """One model call. `messages` is the full request list; the system prompt goes out once per
    Recorder's `known_prompts` (as a `prompt` event, by hash) and the event lists only the messages
    added since the Recorder's previous model event."""
    r = active()
    if r is None:
        return
    system = next((m.get("content") for m in messages[:1] if m.get("role") == "system"), None)
    digest = None
    if isinstance(system, str):
        digest = prompt_hash(system)
        if digest not in r.known_prompts:
            r.known_prompts.add(digest)
            await r.add({"kind": "prompt", "hash": digest, "text": system})
    rest = messages[1:] if digest is not None else messages
    start = r.sent_messages if r.sent_messages <= len(rest) else 0  # a compacted history starts again
    delta = rest[start:]
    if error is None:  # a failed call is sent again: its retry lists the same messages
        r.sent_messages = len(rest)
    await r.add({
        "kind": "model", "model": model, "prompt_hash": digest, "messages_total": len(rest),
        "first_message": start, "messages": delta, "reply": reply, "tokens": tokens,
        "latency_ms": latency_ms, "error": error, "attempt": attempt,
    })


def timer() -> Callable[[], int]:
    start = time.perf_counter()
    return lambda: round((time.perf_counter() - start) * 1000)


def saved_form(events: list[Event], omit_rows: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]) -> list[Event]:
    """Events for storage: `prompt` events dropped (the prompt is kept once, by hash, elsewhere)
    and each model event's messages passed through `omit_rows`."""
    out = []
    for e in events:
        if e.get("kind") == "prompt":
            continue
        if e.get("kind") == "model":
            e = {**e, "messages": omit_rows(e.get("messages") or [])}
        out.append(e)
    return out
