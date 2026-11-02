"""Train cases -> Fireworks chat-format SFT rows, guarded against eval overlap and over-length rows.

With `share`, synthetic train rows are mixed with gallery rows (SPEC 2c, S12): the guards run
first on every case, then the gallery rows are repeated or cut so that the synthetic rows are
`share` of the output.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .generate import wrap

# Chat-template tokens around three messages (headers, end-of-turn markers). An
# estimate; the cookbook's `prepare` gives the exact count in the smoke run.
TEMPLATE_OVERHEAD = 64
NEAR_LIMIT = 0.9  # rows above this share of max_seq_len are reported as near the limit


class ExportError(Exception):
    pass


def approx_tokens(text: str) -> int:
    """Offline stand-in for the tokenizer: one token per 3 characters. Over-counts typical
    Express (about 3.5 characters a token), so it errs toward flagging long rows."""
    return math.ceil(len(text) / 3)


@dataclass
class ExportReport:
    rows: int = 0
    dropped: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))  # family -> reason -> n
    tokens: dict[str, int] = field(default_factory=dict)  # max, p50, p95
    by_source: dict[str, dict[str, int]] = field(default_factory=dict)  # source -> max, p50, p95, near_limit, rows
    mix: dict[str, Any] = field(default_factory=dict)  # share, synthetic, gallery_rows, gallery_unique

    def to_dict(self) -> dict[str, Any]:
        out = {"rows": self.rows, "dropped": {f: dict(c) for f, c in self.dropped.items()}, "tokens": self.tokens, "by_source": self.by_source}
        if self.mix:
            out["mix"] = self.mix
        return out


def sft_row(system_prompt: str, case: dict[str, Any]) -> dict[str, Any]:
    """Answer-only target, no reasoning (ADR 0003)."""
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": case["intent"]},
            {"role": "assistant", "content": wrap(case["reference_express"])},
        ]
    }


def _percentile(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def is_synthetic(case: dict[str, Any]) -> bool:
    return case.get("source") == "synthetic"


def mix_cases(gallery: list[dict[str, Any]], synthetic: list[dict[str, Any]], share: float, seed: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """All `synthetic` rows plus enough gallery rows that synthetic is `share` of the total.

    Gallery rows are shuffled with `seed`, then repeated in that order (oversampling) or cut.
    The result is shuffled again with the same seed. Same inputs and seed, same output.
    """
    if not 0 < share <= 1:
        raise ExportError(f"--mix must be in (0, 1], got {share}")
    if not synthetic:
        raise ExportError("--mix needs synthetic train rows, and none survived the guards")
    total = round(len(synthetic) / share)
    n_gallery = total - len(synthetic)
    if n_gallery > 0 and not gallery:
        raise ExportError(f"--mix {share} needs {n_gallery} gallery rows and there are none")
    rng = random.Random(seed)
    pool = sorted(gallery, key=lambda c: c["id"])
    rng.shuffle(pool)
    picked = [pool[i % len(pool)] for i in range(n_gallery)] if pool else []
    rows = sorted(synthetic, key=lambda c: c["id"]) + picked
    rng.shuffle(rows)
    return rows, {
        "share": share,
        "synthetic": len(synthetic),
        "gallery_rows": n_gallery,
        "gallery_unique": len({c["id"] for c in picked}),
    }


def export_sft(
    train_cases: Iterable[dict[str, Any]],
    eval_cases: Iterable[dict[str, Any]],
    splits: dict[str, str],
    system_prompt: str,
    count_tokens: Callable[[str], int],
    max_seq_len: int,
    extra_fingerprints: Iterable[str] = (),
    skeleton_splits: dict[str, str] | None = None,
    share: float | None = None,
    seed: int = 0,
) -> tuple[list[dict[str, Any]], ExportReport]:
    """`extra_fingerprints`: eval designs with no cases yet (the dev/test seeds themselves).

    `eval_cases` should include synthetic dev rows: a synthetic train row with the same
    fingerprint (or intent) as a synthetic dev row is dropped like any other overlap.
    A synthetic train case is checked against `skeleton_splits[case["skeleton"]]`, a gallery
    case against `splits[case["family"]]`. `share` mixes the two sources (see `mix_cases`).
    """
    train_cases = list(train_cases)
    skeleton_splits = skeleton_splits or {}
    leaked = sorted(
        {
            c["family"]
            for c in train_cases
            if (skeleton_splits.get(c.get("skeleton")) if is_synthetic(c) else splits.get(c["family"])) != "train"
        }
    )
    if leaked:
        raise ExportError(f"train cases from families not in the train split: {leaked}")

    eval_cases = list(eval_cases)
    eval_prints = {c["fingerprint"] for c in eval_cases} | set(extra_fingerprints)
    eval_intents = {c["intent"].strip() for c in eval_cases}

    report = ExportReport()
    kept = []
    for case in train_cases:
        if case["fingerprint"] in eval_prints:
            report.dropped[case["family"]]["fingerprint"] += 1
        elif case["intent"].strip() in eval_intents:
            report.dropped[case["family"]]["intent"] += 1
        else:
            kept.append(case)

    if share is not None:
        kept, report.mix = mix_cases([c for c in kept if not is_synthetic(c)], [c for c in kept if is_synthetic(c)], share, seed)

    system_tokens = count_tokens(system_prompt)
    rows, lengths, sources, too_long = [], [], [], []
    measured: dict[str, int] = {}
    for case in kept:
        row = sft_row(system_prompt, case)
        if case["id"] not in measured:
            measured[case["id"]] = system_tokens + sum(count_tokens(m["content"]) for m in row["messages"][1:]) + TEMPLATE_OVERHEAD
            if measured[case["id"]] > max_seq_len:
                too_long.append(f"{case['id']} ({measured[case['id']]})")
        rows.append(row)
        lengths.append(measured[case["id"]])
        sources.append("synthetic" if is_synthetic(case) else "gallery")
    if too_long:
        raise ExportError(f"{len(too_long)} rows exceed max_seq_len {max_seq_len}: {too_long}")

    report.rows = len(rows)
    if lengths:
        report.tokens = _stats(lengths)
        for source in ("gallery", "synthetic"):
            own = [n for n, s in zip(lengths, sources) if s == source]
            if own:
                report.by_source[source] = {"rows": len(own), **_stats(own), "near_limit": sum(n > NEAR_LIMIT * max_seq_len for n in own)}
    return rows, report


def _stats(lengths: list[int]) -> dict[str, int]:
    return {"max": max(lengths), "p50": _percentile(lengths, 0.5), "p95": _percentile(lengths, 0.95)}
