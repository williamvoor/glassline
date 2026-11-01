"""A scripted fake of `litellm.acompletion` for tests: no network, no real model.

    model = ScriptedCompletion([
        tool_calls(("run_sql", {"sql": "SELECT 1 AS one", "purpose": "one"})),
        text("The answer is {r1[0].one}."),
    ])
    loop = LlmLoop(..., completion=model)

Each step is a ModelResponse, an exception to raise, or an (async) callable taking the call's
kwargs and returning either. Every call's kwargs are recorded (messages deep-copied).
"""

from __future__ import annotations

import copy
import inspect
import json
from collections.abc import Callable, Sequence
from typing import Any

from litellm import ModelResponse
from litellm.types.utils import (
    ChatCompletionMessageToolCall,
    Choices,
    CompletionTokensDetailsWrapper,
    Function,
    Message,
    PromptTokensDetailsWrapper,
    Usage,
)


def _usage(prompt: int, completion: int, cached: int | None, reasoning: int | None) -> Usage:
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        prompt_tokens_details=PromptTokensDetailsWrapper(cached_tokens=cached) if cached is not None else None,
        completion_tokens_details=(
            CompletionTokensDetailsWrapper(reasoning_tokens=reasoning) if reasoning is not None else None
        ),
    )


def text(content: str, *, prompt: int = 1000, completion: int = 100, cached: int | None = 0,
         reasoning: int | None = 10) -> ModelResponse:
    return ModelResponse(
        choices=[Choices(index=0, finish_reason="stop", message=Message(role="assistant", content=content))],
        usage=_usage(prompt, completion, cached, reasoning),
    )


def tool_calls(*calls: tuple[str, dict[str, Any] | str], prompt: int = 1000, completion: int = 50,
               cached: int | None = 0, reasoning: int | None = 10) -> ModelResponse:
    """Each call is (tool name, arguments dict, or a raw string for malformed JSON)."""
    tcs = [
        ChatCompletionMessageToolCall(
            id=f"call_{i}",
            type="function",
            function=Function(name=name, arguments=args if isinstance(args, str) else json.dumps(args)),
        )
        for i, (name, args) in enumerate(calls, start=1)
    ]
    return ModelResponse(
        choices=[Choices(index=0, finish_reason="tool_calls",
                         message=Message(role="assistant", content=None, tool_calls=tcs))],
        usage=_usage(prompt, completion, cached, reasoning),
    )


class ScriptedCompletion:
    def __init__(self, steps: Sequence[Any], *, repeat_last: bool = False):
        self.steps = list(steps)
        self.repeat_last = repeat_last
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> ModelResponse:
        recorded = dict(kwargs)
        recorded["messages"] = copy.deepcopy(kwargs.get("messages"))
        self.calls.append(recorded)
        index = len(self.calls) - 1
        if index >= len(self.steps):
            if not (self.repeat_last and self.steps):
                raise AssertionError(f"stub model called {index + 1} times; only {len(self.steps)} steps scripted")
            index = len(self.steps) - 1
        step = self.steps[index]
        if isinstance(step, Callable) and not isinstance(step, ModelResponse):
            step = step(kwargs)
            if inspect.isawaitable(step):
                step = await step
        if isinstance(step, BaseException):
            raise step
        return step
