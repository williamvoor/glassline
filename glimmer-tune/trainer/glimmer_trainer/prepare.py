"""Local pre-launch checks: no Fireworks call. The tokenizer may be fetched from Hugging Face."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .loop import Config, batches, ckpt_name, schedule

DATE_MARK = "Current date:"


class PrepareError(Exception):
    pass


def _pct(values: list[int], q: float) -> int:
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))] if s else 0


def check(cfg: Config, train: list[dict[str, Any]], dev: list[dict[str, Any]], frozen: str, renderer: Any, decode: Callable[[list[int]], str], train_on: Any) -> dict[str, Any]:
    """Renders every row and raises PrepareError listing every problem found."""
    problems: list[str] = []
    for kind, rows in (("train", train), ("dev", dev)):
        bad = [r.get("id", i) for i, r in enumerate(rows) if not r["messages"] or r["messages"][0] != {"role": "system", "content": frozen}]
        if bad:
            problems.append(f"{len(bad)} {kind} rows lack the frozen system prompt: {bad[:5]}")
    lengths: list[int] = []
    for i, row in enumerate(train):
        model_input, weights = renderer.build_supervised_example(row["messages"], train_on_what=train_on)
        tokens = list(model_input.to_ints())
        w = weights.tolist() if hasattr(weights, "tolist") else list(weights)
        lengths.append(len(tokens))
        if len(tokens) > cfg.max_seq_len:
            problems.append(f"train row {i} has {len(tokens)} tokens > max_seq_len {cfg.max_seq_len}")
        if not any(x > 0 for x in w):
            problems.append(f"train row {i} has no trained tokens")
        if i == 0 and DATE_MARK in decode(tokens):
            problems.append(f"rendered train prompt contains {DATE_MARK!r}")
    for row in dev:
        if DATE_MARK in decode(list(renderer.build_generation_prompt(row["messages"]).to_ints())):
            problems.append(f"rendered dev prompt {row['id']} contains {DATE_MARK!r}")
            break
    if problems:
        raise PrepareError("; ".join(problems[:20]) + (f" (+{len(problems) - 20} more)" if len(problems) > 20 else ""))

    n_steps = len(batches(train, cfg.batch_size, cfg.seed))
    plan = schedule(n_steps, cfg.every)
    subset = sum(r["subset"] for r in dev)
    return {
        "train_rows": len(train),
        "dev_rows": len(dev),
        "dev_subset": subset,
        "steps": n_steps,
        "checkpoints": [ckpt_name(s) for s in plan],
        "tokens": {"max": max(lengths), "p50": _pct(lengths, 0.5), "p95": _pct(lengths, 0.95)},
        "samples_estimate": subset * len(plan) + (len(dev) - subset) * (1 + cfg.top_k),
    }
