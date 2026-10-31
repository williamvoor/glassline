"""The one system prompt, shared by training export and inference (ADR 0003)."""

from __future__ import annotations

from .sdk import ExpressSDK

REASONING = "low"


def build_system_prompt(sdk: ExpressSDK, reasoning: str = REASONING) -> str:
    """Rules and catalog, no gallery examples, then the Muse reasoning-strength line.

    The Muse renderer adds its own default system prompt and `Reasoning strength: high`
    unless the system text already names a strength, so the line must be here.
    """
    return f"{sdk.prompt_snippet}\n\nReasoning strength: {reasoning}."
