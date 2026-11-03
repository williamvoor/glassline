"""The one system prompt, shared by training export and inference (ADR 0003)."""

from __future__ import annotations

from pathlib import Path

from .sdk import ExpressSDK
from .seeds import ROOT

REASONING = "low"


def build_system_prompt(sdk: ExpressSDK, reasoning: str = REASONING) -> str:
    """Rules and catalog, no gallery examples, then the Muse reasoning-strength line.

    The Muse renderer adds its own default system prompt and `Reasoning strength: high`
    unless the system text already names a strength, so the line must be here.
    """
    return f"{sdk.prompt_snippet}\n\n{RUN_2_RULE}\n\nReasoning strength: {reasoning}."


# Run 2 (SPEC "Run 2 decisions", item 8; ADR 0015): s250 invented conditionals and operators.
# Appended after the upstream snippet, which stays byte-identical.
RUN_2_RULE = "Express has no conditionals or operators. Values that vary per item belong in the data model."


FROZEN_PROMPT = ROOT / "data" / "system_prompt.txt"


class PromptError(Exception):
    pass


def checked_system_prompt(sdk: ExpressSDK, frozen: Path = FROZEN_PROMPT) -> str:
    """The system prompt, refused if it differs from the frozen copy (ADR 0015).

    A trained adapter is only valid with the exact prompt it was trained on. Before the
    first freeze (no file) any prompt passes; `data_cli freeze-prompt` writes the file."""
    prompt = build_system_prompt(sdk)
    if frozen.exists() and frozen.read_text(encoding="utf-8") != prompt:
        raise PromptError(
            f"build_system_prompt differs from the frozen {frozen}; a changed prompt invalidates "
            "trained adapters. Re-freeze only on purpose: `data_cli freeze-prompt --force`"
        )
    return prompt


def freeze_prompt(sdk: ExpressSDK, frozen: Path = FROZEN_PROMPT, force: bool = False) -> Path:
    if frozen.exists() and not force and frozen.read_text(encoding="utf-8") != build_system_prompt(sdk):
        raise PromptError(f"{frozen} exists and differs; pass force to overwrite")
    frozen.write_text(build_system_prompt(sdk), encoding="utf-8")
    return frozen
