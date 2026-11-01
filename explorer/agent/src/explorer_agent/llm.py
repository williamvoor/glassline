"""The tool loop (parent spec §2, specs/llm-loop/SPEC.md). The only module that imports litellm.

One Turn: the user's text goes into the Conversation's history; the model is called with the
system prompt, the history and the tools; tool calls run (in parallel) and their results go
back; a reply without tool calls is the final answer and goes through the UI pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import litellm

from explorer_agent.executor import Conversation, StatusFn
from explorer_agent.schema import SchemaCatalog
from explorer_agent.sql import SqlRunner
from explorer_agent.tools import TOOL_SCHEMAS, ToolBox
from explorer_agent.ui import UiError, UiPipeline, UiResult

log = logging.getLogger(__name__)

STEP_LIMIT_PROSE = "I couldn't finish this answer within the step limit."
NO_VIEW_PROSE = "I couldn't build a valid view for this answer."
MODEL_FAILED_PROSE = "The model service failed ({name}). Please try again."
REPAIR_STATUS = "Fixing the view"
REPAIR_MESSAGE = (
    "Your reply could not be shown: {errors}. Reply again with the corrected UI in the same output "
    "format, reusing the same result IDs. SQL is not available while fixing the view."
)
TOOLS_DURING_REPAIR = "the reply called tools while fixing the view"
TIMEOUT_PROSE = "I couldn't finish this answer in time."
RETRY_WAIT_S = 1.0
DEFAULT_CALL_LATENCY_S = 2.9  # spike median with reasoning_effort=minimal, before any call is timed
UNKNOWN_CONTEXT_NOTE = "(Note: earlier results from this conversation are no longer available.)"

Completion = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class LoopConfig:
    model: str = "meta/muse-spark-1.3"
    reasoning_effort: str | None = "minimal"
    prompt_cache_key: str = "explorer"
    max_tool_iterations: int = 8
    max_ui_retries: int = 2
    rows_to_model: int = 20
    history_mode: str = "compact"
    turn_timeout_s: float = 60
    slow_turn_s: float = 30

    @classmethod
    def from_settings(cls, settings: Any) -> LoopConfig:
        return cls(
            model=settings.model,
            reasoning_effort=settings.reasoning_effort,
            prompt_cache_key=settings.prompt_cache_key,
            max_tool_iterations=settings.max_tool_iterations,
            max_ui_retries=settings.max_ui_retries,
            rows_to_model=settings.rows_to_model,
            history_mode=settings.history_mode,
            turn_timeout_s=settings.turn_timeout_s,
            slow_turn_s=settings.slow_turn_s,
        )


@dataclass
class _Turn:
    """State of one Turn that outlives a single model call."""

    last_prose: str | None = None  # filled prose of the last reply whose prose was fine
    repairs: int = 0  # Repairs asked for so far
    repairing: bool = False  # the next model call is a Repair: no SQL
    deadline: float = float("inf")  # clock time the Turn must end by
    calls_left: int = 0  # model calls still allowed after the current one
    model_calls: int = 0
    queries: int = 0


class _ModelFailed(Exception):
    def __init__(self, cause: BaseException):
        super().__init__(type(cause).__name__)
        self.cause = cause


def _tokens(usage: Any) -> dict[str, int | None]:
    def get(obj: Any, name: str) -> int | None:
        value = getattr(obj, name, None) if obj is not None else None
        return value if isinstance(value, int) else None

    return {
        "prompt": get(usage, "prompt_tokens"),
        "completion": get(usage, "completion_tokens"),
        "cached": get(getattr(usage, "prompt_tokens_details", None), "cached_tokens"),
        "reasoning": get(getattr(usage, "completion_tokens_details", None), "reasoning_tokens"),
    }


def _fmt(value: int | None) -> str:
    return "-" if value is None else str(value)


def _summary(result: dict[str, Any], call: dict[str, Any] | None) -> str:
    """One line for an earlier Turn's tool result, built from the history alone (never the
    ResultStore, which evicts), so a finished Turn's summary never changes."""
    name = call["function"]["name"] if call else ""
    if "error" in result:
        return f"failed: {result['error'].get('message')}"
    if name != "run_sql" or "result_id" not in result:
        return "(schema lookup; omitted)"
    try:
        args = json.loads(call["function"]["arguments"] or "{}")
    except (json.JSONDecodeError, TypeError):
        args = {}
    truncated = "yes" if result.get("truncated") else "no"
    return (
        f'{result["result_id"]} "{args.get("purpose", "")}": columns {", ".join(result.get("columns", []))}; '
        f'{result.get("row_count")} rows (truncated: {truncated}). SQL: {args.get("sql", result.get("sql", ""))}'
    )


def compact_history(history: list[dict[str, Any]], current_turn_start: int) -> list[dict[str, Any]]:
    """Earlier Turns' tool results become one-line summaries; the current Turn stays in full.
    Message order and the pairing of tool calls with results are unchanged."""
    calls: dict[str, dict[str, Any]] = {}
    out = []
    for i, m in enumerate(history):
        if m["role"] == "assistant":
            for tc in m.get("tool_calls") or ():
                calls[tc["id"]] = tc
        if i < current_turn_start and m["role"] == "tool":
            try:
                result = json.loads(m["content"])
            except json.JSONDecodeError:
                result = {}
            m = {**m, "content": _summary(result if isinstance(result, dict) else {}, calls.get(m["tool_call_id"]))}
        out.append(m)
    return out


def _retryable(e: BaseException) -> bool:
    """A rate limit, a 5xx or a timeout: worth one more try. A 4xx (bad request, auth) is not."""
    if isinstance(e, (litellm.exceptions.RateLimitError, litellm.exceptions.Timeout)):
        return True
    # LiteLLM's 5xx classes (InternalServerError, BadGatewayError, ServiceUnavailableError, ...)
    # derive from openai's error classes, not litellm.exceptions.APIError, so go by status code.
    status = getattr(e, "status_code", None)
    return type(e).__module__.startswith("litellm") and isinstance(status, int) and status >= 500


def _assistant_message(message: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"role": "assistant", "content": message.content}
    if message.tool_calls:
        out["tool_calls"] = [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in message.tool_calls
        ]
    return out


class LlmLoop:
    """An `Answerer`: `await loop(conv, text, status) -> UiResult`."""

    def __init__(
        self,
        config: LoopConfig,
        system_prompt: str,
        pipeline: UiPipeline,
        runner: SqlRunner,
        catalog: SchemaCatalog,
        completion: Completion | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self.system_prompt = system_prompt
        self.pipeline = pipeline
        self.runner = runner
        self.catalog = catalog
        self.completion = completion or litellm.acompletion
        self.sleep = sleep
        self.clock = clock
        self._latencies: deque[float] = deque(maxlen=50)  # seconds per recent model call, agent-wide

    async def __call__(self, conv: Conversation, text: str, status: StatusFn) -> UiResult:
        conv.turn_starts.append(len(conv.history))
        if conv.unknown_context:
            text = f"{UNKNOWN_CONTEXT_NOTE}\n\n{text}"
        conv.history.append({"role": "user", "content": text})
        start = self.clock()
        turn = _Turn(deadline=start + self.config.turn_timeout_s)
        try:
            async with asyncio.timeout(self.config.turn_timeout_s):
                return await self._turn(conv, status, turn)
        except TimeoutError:
            # The model call or the queries in flight were cancelled (psycopg cancels the
            # statement server-side; the pool resets the connection).
            log.warning("turn timed out after %.0f s", self.config.turn_timeout_s)
            return self._give_up(TIMEOUT_PROSE, turn.last_prose, [])
        except _ModelFailed as e:
            cause = e.cause
            # Class and status only: a provider's error body can echo request content.
            log.error("model call failed: %s status=%s", type(cause).__name__, getattr(cause, "status_code", "-"))
            log.debug("model call failure detail", exc_info=cause)
            return UiResult(prose=MODEL_FAILED_PROSE.format(name=type(cause).__name__), messages=[], bare_numbers=[])
        finally:
            elapsed = self.clock() - start
            if elapsed > self.config.slow_turn_s:
                log.warning(
                    "slow turn: %.1f s (target %.0f s), model_calls=%d repairs=%d queries=%d",
                    elapsed, self.config.slow_turn_s, turn.model_calls, turn.repairs, turn.queries,
                )

    async def _turn(self, conv: Conversation, status: StatusFn, turn: _Turn) -> UiResult:
        tools = ToolBox(self.runner, self.catalog, conv.store, self.config.rows_to_model)
        for i in range(self.config.max_tool_iterations):
            turn.calls_left = self.config.max_tool_iterations - i - 1
            message = await self._call_model(conv, turn)
            if message.tool_calls and turn.repairing:
                # tool_choice="none" should prevent this; if the provider ignores it, the calls
                # are not run (a Repair never queries) and the attempt counts as a failed Repair.
                conv.history.append({"role": "assistant", "content": message.content or ""})
                errors = [TOOLS_DURING_REPAIR]
                if not await self._ask_for_repair(conv, turn, errors, status):
                    return self._give_up(NO_VIEW_PROSE, turn.last_prose, errors)
                continue
            if message.tool_calls:
                # The assistant message and its results go into the history together, after every
                # tool finished: a failure or cancellation in between leaves no unanswered calls.
                results = await self._run_tools(tools, message.tool_calls, status, turn)
                conv.history.append(_assistant_message(message))
                conv.history.extend(results)
                continue
            reply = message.content or ""
            conv.history.append({"role": "assistant", "content": reply})
            try:
                return self.pipeline.build(reply, conv.store, conv.surface_id)
            except UiError as e:
                if e.prose:
                    turn.last_prose = e.prose
                if not await self._ask_for_repair(conv, turn, e.messages, status):
                    return self._give_up(NO_VIEW_PROSE, turn.last_prose, e.messages)
        return self._give_up(STEP_LIMIT_PROSE, turn.last_prose, [])

    async def _ask_for_repair(self, conv: Conversation, turn: _Turn, errors: list[str], status: StatusFn) -> bool:
        """Queue a Repair, unless the Repairs are used up. The next model call runs no SQL."""
        if turn.repairs >= self.config.max_ui_retries or turn.calls_left == 0:
            return False  # no Repair left, or no model call left to make it with
        turn.repairs += 1
        turn.repairing = True
        await status(REPAIR_STATUS)
        conv.history.append({"role": "user", "content": REPAIR_MESSAGE.format(errors="; ".join(errors))})
        return True

    @staticmethod
    def _give_up(explanation: str, prose: str | None, errors: list[str]) -> UiResult:
        parts = [prose] if prose else []
        parts.append(explanation)
        if errors:
            parts.append("\n".join(f"- {m}" for m in errors))
        return UiResult(prose="\n\n".join(parts), messages=[], bare_numbers=[])

    def _history(self, conv: Conversation) -> list[dict[str, Any]]:
        if self.config.history_mode == "full" or not conv.turn_starts:
            return conv.history
        return compact_history(conv.history, conv.turn_starts[-1])

    def _request(self, conv: Conversation, turn: _Turn) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": [{"role": "system", "content": self.system_prompt}, *self._history(conv)],
            "tools": TOOL_SCHEMAS,
            "prompt_cache_key": self.config.prompt_cache_key,
            "num_retries": 0,  # LiteLLM would otherwise retry twice by itself
        }
        if self.config.reasoning_effort is not None:
            kwargs["reasoning_effort"] = self.config.reasoning_effort
        if turn.repairing:
            # Same tools list (the cached prefix stays identical), but no tool may be called.
            kwargs["tool_choice"] = "none"
        return kwargs

    def _retry_fits(self, turn: _Turn) -> bool:
        latencies = sorted(self._latencies)
        median = latencies[len(latencies) // 2] if latencies else DEFAULT_CALL_LATENCY_S
        return self.clock() + RETRY_WAIT_S + median <= turn.deadline

    async def _call_model(self, conv: Conversation, turn: _Turn) -> Any:
        for attempt in (1, 2):
            start = self.clock()
            try:
                response = await self.completion(**self._request(conv, turn))
                message = response.choices[0].message
                break
            except Exception as e:  # API errors and malformed responses alike
                if attempt == 1 and _retryable(e) and self._retry_fits(turn):
                    log.warning("model call retry: %s status=%s", type(e).__name__, getattr(e, "status_code", "-"))
                    await self.sleep(RETRY_WAIT_S)
                    continue
                raise _ModelFailed(e) from e
        turn.model_calls += 1  # the retry is the same call, repeated: it doesn't count
        elapsed = self.clock() - start
        self._latencies.append(elapsed)
        latency_ms = round(elapsed * 1000)
        t = _tokens(getattr(response, "usage", None))
        conv.prompt_tokens += t["prompt"] or 0
        conv.cached_tokens += t["cached"] or 0
        rate = 100 * conv.cached_tokens / conv.prompt_tokens if conv.prompt_tokens else 0.0
        log.info(
            "model call: model=%s prompt=%s cached=%s completion=%s reasoning=%s latency_ms=%d cache_rate=%.0f%%",
            self.config.model, _fmt(t["prompt"]), _fmt(t["cached"]), _fmt(t["completion"]),
            _fmt(t["reasoning"]), latency_ms, rate,
        )
        return message

    async def _run_tools(self, tools: ToolBox, calls: list[Any], status: StatusFn, turn: _Turn) -> list[dict[str, Any]]:
        queries: list[str] = []

        async def run(call: Any) -> dict[str, Any]:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                result: dict[str, Any] = {"error": {"message": "the tool arguments are not valid JSON"}}
            else:
                if name == "run_sql" and isinstance(args, dict):
                    queries.append(name)
                    await status(f"Running SQL: {args.get('purpose') or 'query'}")
                elif name in ("list_tables", "describe_table"):
                    await status("Looking at the schema")
                result = await tools.call(name, args)
                if name == "run_sql" and "error" in result:
                    await status(f"SQL failed: {result['error'].get('message')}")
            return {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)}

        try:
            return list(await asyncio.gather(*(run(c) for c in calls)))
        finally:
            turn.queries += len(queries)
