"""Train cases -> Fireworks chat-format SFT rows, guarded against eval overlap and over-length rows.

The run 2 synthetic mix (`mix_cases`, skeleton splits) was retired with the synthetic
generator (docs/cleanup-plan.md). Each train case must name a Family in the train split; the
distilled rows of Approach v2 get their own split rule when Phase 4 defines their format.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .io import wrap
from .refs import RefIndex
from .structure import check_structure, required_on_boolean_input

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

    def to_dict(self) -> dict[str, Any]:
        return {"rows": self.rows, "dropped": {f: dict(c) for f, c in self.dropped.items()}, "tokens": self.tokens, "by_source": self.by_source}


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


def _unreachable(messages: list[dict[str, Any]], index: RefIndex) -> list[str]:
    """Ids of components that no surface attaches under its root (`check_structure` orphans)."""
    surfaces: dict[str, dict[str, dict[str, Any]]] = {}
    for m in messages:
        if "updateComponents" in m:
            u = m["updateComponents"]
            surfaces.setdefault(u["surfaceId"], {}).update({c["id"]: c for c in u["components"]})
    reports = [check_structure(comps, index) for comps in surfaces.values()]
    lost = sorted({o for r in reports for o in r.orphans})
    return lost + (["(no root)"] if any(r.missing_root for r in reports) else [])


def export_sft(
    train_cases: Iterable[dict[str, Any]],
    eval_cases: Iterable[dict[str, Any]],
    splits: dict[str, str],
    system_prompt: str,
    count_tokens: Callable[[str], int],
    max_seq_len: int,
    extra_fingerprints: Iterable[str] = (),
    catalog_check: Any = None,
) -> tuple[list[dict[str, Any]], ExportReport]:
    """`extra_fingerprints`: eval designs with no cases yet (the dev/test seeds themselves).

    `eval_cases` should include the synthetic dev rows, so a train row with the same
    fingerprint (or intent) as one is dropped like any other overlap. Every train case is
    checked against `splits[case["family"]]`.
    `catalog_check` (a `CatalogCheck`) refuses any target with an operator or a name outside
    the catalog: training must never show the model invented Express. It also refuses any
    `required` check on a boolean input, which never blocks, and any component that nothing
    attaches to the tree.
    """
    train_cases = list(train_cases)
    leaked = sorted({c["family"] for c in train_cases if splits.get(c["family"]) != "train"})
    if leaked:
        raise ExportError(f"train cases from families not in the train split: {leaked}")
    if catalog_check is not None:
        outside = [f"{c['id']} ({catalog_check.findings(c['reference_express'])[0].detail})" for c in train_cases if catalog_check.findings(c["reference_express"])]
        if outside:
            raise ExportError(f"{len(outside)} train targets reach outside the catalog: {outside[:10]}")
        dead_checks = [
            f"{c['id']} ({', '.join(ids)})"
            for c in train_cases
            if (ids := required_on_boolean_input(
                (x for m in c["reference"] for x in m.get("updateComponents", {}).get("components", [])),
                catalog_check.boolean_inputs,
            ))
        ]
        if dead_checks:
            raise ExportError(f"{len(dead_checks)} train targets use required on a boolean input, which never blocks: {dead_checks[:10]}")
        index = RefIndex(catalog_check.sdk.catalog_json)
        lost = [f"{c['id']} ({', '.join(ids)})" for c in train_cases if (ids := _unreachable(c["reference"], index))]
        if lost:
            raise ExportError(f"{len(lost)} train targets define unreachable components, which nothing displays: {lost[:10]}")

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
