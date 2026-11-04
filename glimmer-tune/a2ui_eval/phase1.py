"""Distill Phase 1: the teacher ceiling on gallery dev (`docs/distill_restart_prompt.md`).

`python -m a2ui_eval.phase1 report --run LABEL=predictions.jsonl[@cost] ... --out runs/phase1/report.json`

For each teacher's best-of-n predictions (rows carry a `sample` index), it reports:
- single: the mean over samples of the gallery name-blind slot F1, which is the expected score
  of one sample;
- validator best-of-n: per row, the candidate `validate.pick` keeps (passes layers 1-3, fewest
  tokens). A row with no passing candidate scores its sample 0 and counts as uncovered;
- oracle best-of-n: per row, the best candidate by F1 against the reference. It is an upper
  bound no pipeline can reach, because the reference isn't available at selection time;
- compile and outside-catalog rates, per-layer pass rates, answer and reasoning tokens, and cost.
Intervals are family-level over gallery rows, as everywhere on dev.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .checkpoints import DATA, CheckpointError, _mean, family_ci, score_predictions
from .io import read_jsonl
from .sdk import ExpressSDK
from .validate import Validator, pick

S225_GALLERY_BLIND = 0.523  # the bar to beat clearly (brief, Phase 1)


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def answer_text(p: dict[str, Any]) -> str:
    return p.get("text") or ""


def token_counts(p: dict[str, Any], count: Callable[[str], int]) -> tuple[int, int]:
    """(answer, reasoning) output tokens. Glimmer rows have `tokens` (everything sampled); Spark
    Open rows have `completion_tokens` and, when the host reports it, `reasoning_tokens`."""
    answer = count(answer_text(p))
    total = p.get("tokens") if p.get("tokens") is not None else p.get("completion_tokens")
    if p.get("reasoning_tokens") is not None:
        return answer, int(p["reasoning_tokens"])
    return answer, max(0, int(total or 0) - answer)


def analyse(
    preds: list[dict[str, Any]],
    dev_rows: list[dict[str, Any]],
    validator: Validator,
    count: Callable[[str], int],
    data: Path = DATA,
    cost_usd: float | None = None,
) -> dict[str, Any]:
    by_sample: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for p in preds:
        by_sample[int(p.get("sample", 0))].append(p)
    samples = sorted(by_sample)
    ids = sorted({p["id"] for p in preds})
    if any(sorted(q["id"] for q in by_sample[k]) != ids for k in samples):
        raise CheckpointError("every sample must cover the same rows")

    scored: dict[int, dict[str, Any]] = {}
    for k in samples:
        one = [{**p, "checkpoint": "teacher"} for p in by_sample[k]]
        scored[k] = score_predictions(one, dev_rows, data)
    row_score = {(r["id"], k): r for k in samples for r in scored[k]["rows"]}

    cands: dict[str, list[dict[str, Any]]] = defaultdict(list)
    layer_pass: dict[str, list[float]] = defaultdict(list)
    answer_toks, reasoning_toks = [], []
    for p in preds:
        v = validator.check(p.get("text"), p.get("finish"))
        a, r = token_counts(p, count)
        answer_toks.append(a)
        reasoning_toks.append(r)
        for name, layer in v["layers"].items():
            if layer["pass"] is not None:
                layer_pass[name].append(float(layer["pass"]))
        cands[p["id"]].append({"sample": int(p.get("sample", 0)), "pass": v["pass"], "tokens": a + r, "reasons": v["layers"]})

    picked_rows, oracle_rows, covered = [], [], 0
    for i in ids:
        chosen = pick(cands[i])
        covered += chosen is not None
        k = chosen["sample"] if chosen else samples[0]
        picked_rows.append({**row_score[(i, k)], "picked_sample": k, "covered": chosen is not None})
        best = max(samples, key=lambda s: (row_score[(i, s)]["slot_f1_blind"], -s))
        oracle_rows.append(row_score[(i, best)])

    def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "gallery_f1_blind": _mean([r["slot_f1_blind"] for r in rows]),
            "ci": family_ci(rows, key="slot_f1_blind"),
            "compiles": _mean([float(r["compiles"]) for r in rows]),
            "outside_catalog": _mean([float(r["outside_catalog"]) for r in rows]),
        }

    single = [scored[k]["sources"]["gallery"] for k in samples]
    return {
        "rows": len(ids),
        "samples": len(samples),
        "single": {
            "gallery_f1_blind": _mean([s["slot_f1_blind"] for s in single]),
            "per_sample": [s["slot_f1_blind"] for s in single],
            "compiles": _mean([s["compiles"] for s in single]),
            "outside_catalog": _mean([s["outside_catalog"] for s in single]),
        },
        "validator_best_of_n": {**summary(picked_rows), "covered": covered / len(ids)},
        "oracle_best_of_n": summary(oracle_rows),
        "layer_pass": {k: _mean(v) for k, v in layer_pass.items()},
        "candidate_pass": _mean([float(c["pass"]) for cs in cands.values() for c in cs]),
        "tokens": {
            "answer_p50": _pct(answer_toks, 0.5), "answer_p95": _pct(answer_toks, 0.95),
            "reasoning_p50": _pct(reasoning_toks, 0.5), "reasoning_p95": _pct(reasoning_toks, 0.95),
        },
        "cost_usd": cost_usd,
        "beats_s225": None,  # the decision is the user's; the table shows the interval
    }


def markdown(results: dict[str, dict[str, Any]]) -> str:
    lines = [
        f"Gallery dev, name-blind slot F1, family-level 95% intervals. Bar: s225 = {S225_GALLERY_BLIND}.",
        "",
        "| Teacher | n | Single | Validator best-of-n (CI) | Covered | Oracle best-of-n | Compiles | Outside catalog | Candidates passing | Answer tok p50/p95 | Reasoning tok p50/p95 | Cost |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for label, r in results.items():
        v, o, t = r["validator_best_of_n"], r["oracle_best_of_n"], r["tokens"]
        cost = f"${r['cost_usd']:.2f}" if r["cost_usd"] is not None else "?"
        lines.append(
            f"| {label} | {r['samples']} | {r['single']['gallery_f1_blind']:.3f} | {v['gallery_f1_blind']:.3f} "
            f"({v['ci'][0]:.3f}–{v['ci'][1]:.3f}) | {v['covered']:.0%} | {o['gallery_f1_blind']:.3f} | "
            f"{r['single']['compiles']:.2f} | {r['single']['outside_catalog']:.2f} | {r['candidate_pass']:.0%} | "
            f"{t['answer_p50']:.0f}/{t['answer_p95']:.0f} | {t['reasoning_p50']:.0f}/{t['reasoning_p95']:.0f} | {cost} |"
        )
    return "\n".join(lines) + "\n"


def _parse_run(spec: str) -> tuple[str, Path, float | None]:
    label, _, rest = spec.partition("=")
    path, _, cost = rest.partition("@")
    if not label or not path:
        raise SystemExit(f"--run wants LABEL=predictions.jsonl[@cost], got {spec!r}")
    return label, Path(path), float(cost) if cost else None


def main(argv: list[str] | None = None) -> int:
    from .data_cli import CATALOG, load_tokenizer_counter
    from .export import approx_tokens
    from .render import Renderer

    p = argparse.ArgumentParser(prog="python -m a2ui_eval.phase1")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report", help="score and validate best-of-n teacher predictions on gallery dev (no model calls)")
    r.add_argument("--run", action="append", required=True, help="LABEL=predictions.jsonl[@cost_usd]; repeat per teacher")
    r.add_argument("--dev", type=Path, required=True, help="the prompt export the predictions answered")
    r.add_argument("--out", type=Path, required=True, help="report JSON; a .md table is written beside it")
    r.add_argument("--no-render", action="store_true", help="skip layer 2 (needs review/dist)")
    r.add_argument("--approx-tokens", action="store_true", help="count answer tokens as chars/3 instead of the Glimmer tokenizer")
    args = p.parse_args(argv)

    dev_rows = read_jsonl(args.dev)
    count = approx_tokens if args.approx_tokens else load_tokenizer_counter()
    sdk = ExpressSDK(CATALOG)
    results = {}
    try:
        if args.no_render:
            v = Validator(sdk, renderer=None)
            for spec in args.run:
                label, path, cost = _parse_run(spec)
                results[label] = analyse(read_jsonl(path), dev_rows, v, count, cost_usd=cost)
        else:
            with Renderer() as renderer:
                v = Validator(sdk, renderer=renderer)
                for spec in args.run:
                    label, path, cost = _parse_run(spec)
                    results[label] = analyse(read_jsonl(path), dev_rows, v, count, cost_usd=cost)
    except CheckpointError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    table = markdown(results)
    args.out.with_suffix(".md").write_text(table, encoding="utf-8")
    print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
