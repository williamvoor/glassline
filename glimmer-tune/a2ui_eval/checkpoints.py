"""Dev prompts for checkpoint scoring, and scoring of a checkpoint's predictions (ADR 0015).

`python -m a2ui_eval.checkpoints score <predictions.jsonl> --out <scores.json> [--full]`

The trainer (`trainer/`, its own environment) never imports this package: it writes
predictions and runs this command as a subprocess.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from .export import is_synthetic
from .generate import read_jsonl
from .refs import RefIndex
from .score import score_response
from .sdk import ExpressSDK
from .seeds import ROOT

DATA = ROOT / "data"
CATALOG = ROOT / "catalogs" / "basic_v0_9.json"  # ADR 0008
DEV_EXPORT = ROOT / "exports" / "sft_dev.jsonl"
SUBSET_PER_SOURCE = 32
SOURCES = ("gallery", "synthetic")
FINISH = ("stop", "length", "error")
BOOTSTRAP = {"samples": 2000, "level": 0.95, "seed": 0}


class CheckpointError(Exception):
    pass


def dev_cases(data: Path = DATA) -> list[dict[str, Any]]:
    return read_jsonl(data / "dev" / "cases.jsonl") + read_jsonl(data / "synth" / "dev" / "cases.jsonl")


def _source(case: dict[str, Any]) -> str:
    return "synthetic" if is_synthetic(case) else "gallery"


def export_dev(system_prompt: str, data: Path = DATA, seed: int = 0, per_source: int = SUBSET_PER_SOURCE) -> list[dict[str, Any]]:
    """One prompt row per dev case (gallery, then synthetic, each id-sorted). `subset` marks a
    fixed `per_source` rows of each source, drawn by `random.Random(seed)` from id-sorted ids."""
    cases = dev_cases(data)
    subset: set[str] = set()
    for source in SOURCES:
        ids = sorted(c["id"] for c in cases if _source(c) == source)
        if len(ids) < per_source:
            raise CheckpointError(f"{source} dev has {len(ids)} rows, fewer than the subset of {per_source}")
        subset |= set(random.Random(seed).sample(ids, per_source))
    ordered = sorted(cases, key=lambda c: (SOURCES.index(_source(c)), c["id"]))
    return [
        {
            "id": c["id"],
            "source": _source(c),
            "subset": c["id"] in subset,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": c["intent"]}],
        }
        for c in ordered
    ]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def selection_ci(
    rows: list[dict[str, Any]], samples: int = BOOTSTRAP["samples"], level: float = BOOTSTRAP["level"], seed: int = BOOTSTRAP["seed"]
) -> list[float]:
    """Percentile bootstrap interval for `selection`. Rows are resampled with replacement within
    each source, keeping its row count, so each resample is weighted like `selection` itself."""
    by_source = [v for v in ([r["slot_f1"] for r in rows if r["source"] == s] for s in SOURCES) if v]
    if not by_source:
        return [0.0, 0.0]
    rng = random.Random(seed)
    stats = sorted(_mean([_mean(rng.choices(v, k=len(v))) for v in by_source]) for _ in range(samples))
    tail = (1 - level) / 2
    return [stats[int(tail * samples)], stats[min(samples - 1, int((1 - tail) * samples))]]


def score_predictions(
    predictions: list[dict[str, Any]],
    dev_rows: list[dict[str, Any]],
    data: Path = DATA,
    expect: set[str] | None = None,
    sdk: ExpressSDK | None = None,
) -> dict[str, Any]:
    """Scores one checkpoint's predictions. `expect`: ids that must all be present.

    `selection` is the mean of the gallery and synthetic mean row slot F1, so the larger
    synthetic dev set doesn't outweigh the gallery one (ADR 0009)."""
    checkpoints = {p.get("checkpoint") for p in predictions}
    if len(checkpoints) != 1:
        raise CheckpointError(f"predictions must be for one checkpoint, got {sorted(map(str, checkpoints))}")
    known = {r["id"]: r for r in dev_rows}
    cases = {c["id"]: c for c in dev_cases(data)}
    ids = [p["id"] for p in predictions]
    if unknown := sorted(set(ids) - set(known)):
        raise CheckpointError(f"unknown ids (not in the dev export): {unknown[:5]}")
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        raise CheckpointError(f"duplicate ids: {dupes[:5]}")
    if expect is not None and (missing := sorted(expect - set(ids))):
        raise CheckpointError(f"missing predictions for {len(missing)} ids: {missing[:5]}")
    if bad := sorted({str(p.get("finish")) for p in predictions} - set(FINISH)):
        raise CheckpointError(f"finish must be one of {FINISH}, got {bad}")

    sdk = sdk or ExpressSDK(CATALOG)
    index = RefIndex(sdk.catalog_json)
    rows = []
    for p in predictions:
        text = None if p["finish"] == "error" else p.get("text")
        r = score_response(sdk, index, cases[p["id"]], text, p["finish"])
        rows.append(
            {
                "id": p["id"],
                "source": known[p["id"]]["source"],
                "finish": p["finish"],
                "compiles": r["compiles"],
                "exact_match": r["exact_match"],
                "truncated": r["truncated"],
                "slot_f1": r["slot"]["f1"],
            }
        )
    sources = {}
    for source in SOURCES:
        own = [r for r in rows if r["source"] == source]
        if own:
            sources[source] = {"rows": len(own), **{k: _mean([float(r[k]) for r in own]) for k in ("compiles", "exact_match", "truncated", "slot_f1")}}
    return {
        "checkpoint": checkpoints.pop(),
        "rows": rows,
        "sources": sources,
        "selection": _mean([s["slot_f1"] for s in sources.values()]),
        "selection_ci": selection_ci(rows),
        "bootstrap": {**BOOTSTRAP, "stratified_by": list(SOURCES)},
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m a2ui_eval.checkpoints")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score", help="score one checkpoint's predictions JSONL")
    s.add_argument("predictions", type=Path)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--dev", type=Path, default=DEV_EXPORT)
    s.add_argument("--full", action="store_true", help="every dev row must be present (default: the subset)")
    args = p.parse_args(argv)
    try:
        dev_rows = read_jsonl(args.dev)
        expect = {r["id"] for r in dev_rows if args.full or r["subset"]}
        out = score_predictions(read_jsonl(args.predictions), dev_rows, expect=expect)
    except CheckpointError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"checkpoint": out["checkpoint"], "selection": out["selection"], "selection_ci": out["selection_ci"], "sources": out["sources"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
