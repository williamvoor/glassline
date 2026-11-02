"""BOOTSTRAP_* settings for the draft step (SPEC.md phase 2b). Pure: reads only the given mapping."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# ADR 0020 and CLAUDE.md: Wonder (default) or Spark, nothing else.
ALLOWED_MODELS = ("meta/muse-wonder-1.0", "meta/muse-spark-1.3")
PRICES_PATH = Path(__file__).resolve().parent / "prices.toml"

_INTS = {
    "BOOTSTRAP_MAX_TOOL_ITERATIONS": 12,
    "BOOTSTRAP_MAX_TOKENS": 32000,
    "BOOTSTRAP_CALL_TIMEOUT_S": 300,
    "BOOTSTRAP_SQL_ROW_CAP": 200,
    "BOOTSTRAP_ROWS_TO_MODEL": 20,
}


class SettingsError(ValueError):
    """Names the variable, never a value from the environment."""


@dataclass(frozen=True)
class Prices:
    input: float  # USD per 1M tokens
    cached_input: float
    output: float


@dataclass(frozen=True)
class DraftSettings:
    model: str
    effort: str
    max_tool_iterations: int
    max_tokens: int
    call_timeout_s: int
    sql_row_cap: int
    rows_to_model: int
    prices: Prices


def load_prices(model: str, path: Path = PRICES_PATH) -> Prices:
    table = tomllib.loads(path.read_text(encoding="utf-8"))
    if model not in table:
        raise SettingsError(f"no prices for {model} in {path.name}")
    p = table[model]
    return Prices(input=float(p["input"]), cached_input=float(p["cached_input"]), output=float(p["output"]))


def _positive_int(env: Mapping[str, str], key: str) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return _INTS[key]
    try:
        value = int(raw)
    except ValueError:
        raise SettingsError(f"{key} must be a positive integer") from None
    if value <= 0:
        raise SettingsError(f"{key} must be a positive integer")
    return value


def load_draft_settings(
    env: Mapping[str, str], *, model: str | None = None, effort: str | None = None
) -> DraftSettings:
    model = (model or env.get("BOOTSTRAP_MODEL", "").strip() or ALLOWED_MODELS[0]).strip()
    if model not in ALLOWED_MODELS:
        raise SettingsError(f"model must be one of {', '.join(ALLOWED_MODELS)}")
    effort = (effort or env.get("BOOTSTRAP_REASONING_EFFORT", "").strip() or "low").strip()
    if effort not in ("minimal", "low", "medium", "high", "xhigh", "max"):
        raise SettingsError("reasoning effort must be one of minimal, low, medium, high, xhigh, max")
    return DraftSettings(
        model=model,
        effort=effort,
        max_tool_iterations=_positive_int(env, "BOOTSTRAP_MAX_TOOL_ITERATIONS"),
        max_tokens=_positive_int(env, "BOOTSTRAP_MAX_TOKENS"),
        call_timeout_s=_positive_int(env, "BOOTSTRAP_CALL_TIMEOUT_S"),
        sql_row_cap=_positive_int(env, "BOOTSTRAP_SQL_ROW_CAP"),
        rows_to_model=_positive_int(env, "BOOTSTRAP_ROWS_TO_MODEL"),
        prices=load_prices(model),
    )
