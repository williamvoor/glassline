"""Train cases -> Fireworks chat-format SFT rows, guarded against eval overlap and over-length rows."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .generate import wrap

# Chat-template tokens around three messages (headers, end-of-turn markers). An
# estimate; the cookbook's `prepare` gives the exact count in the smoke run.
TEMPLATE_OVERHEAD = 64


class ExportError(Exception):
    pass


@dataclass
class ExportReport:
    rows: int = 0
    dropped: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))  # family -> reason -> n
    tokens: dict[str, int] = field(default_factory=dict)  # max, p50, p95

    def to_dict(self) -> dict[str, Any]:
        return {"rows": self.rows, "dropped": {f: dict(c) for f, c in self.dropped.items()}, "tokens": self.tokens}


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


def export_sft(
    train_cases: Iterable[dict[str, Any]],
    eval_cases: Iterable[dict[str, Any]],
    splits: dict[str, str],
    system_prompt: str,
    count_tokens: Callable[[str], int],
    max_seq_len: int,
    extra_fingerprints: Iterable[str] = (),
) -> tuple[list[dict[str, Any]], ExportReport]:
    """`extra_fingerprints`: eval designs with no cases yet (the dev/test seeds themselves)."""
    train_cases = list(train_cases)
    leaked = sorted({c["family"] for c in train_cases if splits.get(c["family"]) != "train"})
    if leaked:
        raise ExportError(f"train cases from families not in the train split: {leaked}")

    eval_cases = list(eval_cases)
    eval_prints = {c["fingerprint"] for c in eval_cases} | set(extra_fingerprints)
    eval_intents = {c["intent"].strip() for c in eval_cases}

    report = ExportReport()
    system_tokens = count_tokens(system_prompt)
    rows, lengths, too_long = [], [], []
    for case in train_cases:
        if case["fingerprint"] in eval_prints:
            report.dropped[case["family"]]["fingerprint"] += 1
            continue
        if case["intent"].strip() in eval_intents:
            report.dropped[case["family"]]["intent"] += 1
            continue
        row = sft_row(system_prompt, case)
        n = system_tokens + sum(count_tokens(m["content"]) for m in row["messages"][1:]) + TEMPLATE_OVERHEAD
        if n > max_seq_len:
            too_long.append(f"{case['id']} ({n})")
        rows.append(row)
        lengths.append(n)
    if too_long:
        raise ExportError(f"{len(too_long)} rows exceed max_seq_len {max_seq_len}: {too_long}")

    report.rows = len(rows)
    if lengths:
        report.tokens = {"max": max(lengths), "p50": _percentile(lengths, 0.5), "p95": _percentile(lengths, 0.95)}
    return rows, report
