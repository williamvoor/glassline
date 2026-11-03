"""Dev prompts for checkpoint scoring, and scoring of a checkpoint's predictions (ADR 0015).

`python -m a2ui_eval.checkpoints score <predictions.jsonl> --out <scores.json> [--full] [--split test]`
`python -m a2ui_eval.checkpoints compare <a-scores.json> <b-scores.json>`

The test split is spent once, on the final candidate and the base (SPEC "Test evaluation").

The trainer (`trainer/`, its own environment) never imports this package: it writes
predictions and runs this command as a subprocess.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

from .catalog_check import CatalogCheck, kinds
from .export import is_synthetic
from .generate import read_jsonl
from .refs import RefIndex
from .score import score_response
from .sdk import ExpressSDK
from .seeds import ROOT

DATA = ROOT / "data"
CATALOG = ROOT / "catalogs" / "basic_v0_9.json"  # ADR 0008
DEV_EXPORT = ROOT / "exports" / "sft_dev.jsonl"
TEST_EXPORT = ROOT / "exports" / "sft_test.jsonl"
SUBSET_PER_SOURCE = 32
SOURCES = ("gallery", "synthetic")
FINISH = ("stop", "length", "error")
BOOTSTRAP = {"samples": 2000, "level": 0.95, "seed": 0}


class CheckpointError(Exception):
    pass


def dev_cases(data: Path = DATA) -> list[dict[str, Any]]:
    return read_jsonl(data / "dev" / "cases.jsonl") + read_jsonl(data / "synth" / "dev" / "cases.jsonl")


def test_cases(data: Path = DATA) -> list[dict[str, Any]]:
    return read_jsonl(data / "test" / "cases.jsonl")


SPLIT_CASES = {"dev": dev_cases, "test": test_cases}


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


def export_test(system_prompt: str, data: Path = DATA) -> list[dict[str, Any]]:
    """One prompt row per frozen test case, id-sorted. Test is gallery only; each row keeps its
    family for the family-level bootstrap. No subset: every row is sampled."""
    return [
        {
            "id": c["id"],
            "source": _source(c),
            "family": c["family"],
            "subset": False,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": c["intent"]}],
        }
        for c in sorted(test_cases(data), key=lambda c: c["id"])
    ]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


NO_ROOT = "(no root)"


def _git_version(*paths: str) -> dict[str, Any]:
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain", "--", *paths], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}
    return {"commit": head, "dirty": bool(status.strip())}


def scorer_version() -> dict[str, Any]:
    """The git commit the scorer ran from, and whether the checkout had uncommitted changes.
    Saved with every result so a later scorer fix can rescore saved predictions traceably."""
    return _git_version("a2ui_eval")


def references_version() -> dict[str, Any]:
    """The same for the eval references (`data/dev`, `data/synth/dev`, `data/test`): a fixed dev Reference changes
    scores, so old and new numbers must not be mixed (SPEC, item 7 amendment, decision 9)."""
    return _git_version("data/dev", "data/synth/dev", "data/test")


def selection_ci(
    rows: list[dict[str, Any]],
    samples: int = BOOTSTRAP["samples"],
    level: float = BOOTSTRAP["level"],
    seed: int = BOOTSTRAP["seed"],
    key: str = "slot_f1",
) -> list[float]:
    """Percentile bootstrap interval for `selection` (or, with `key`, another row score). Rows are
    resampled with replacement within each source, keeping its row count, so each resample is
    weighted like `selection` itself."""
    by_source = [v for v in ([r[key] for r in rows if r["source"] == s] for s in SOURCES) if v]
    if not by_source:
        return [0.0, 0.0]
    rng = random.Random(seed)
    return _percentiles([_mean([_mean(rng.choices(v, k=len(v))) for v in by_source]) for _ in range(samples)], samples, level)


def _percentiles(stats: list[float], samples: int, level: float) -> list[float]:
    stats = sorted(stats)
    tail = (1 - level) / 2
    return [stats[int(tail * samples)], stats[min(samples - 1, int((1 - tail) * samples))]]


def _by_family(rows: list[dict[str, Any]], key: str = "slot_f1") -> list[list[float]]:
    fams: dict[str, list[float]] = {}
    for r in rows:
        fams.setdefault(r["family"], []).append(r[key])
    return [fams[f] for f in sorted(fams)]


def _cluster_ci(clusters: list[list[float]], samples: int, level: float, seed: int) -> list[float]:
    """Percentile bootstrap that resamples whole clusters and takes the row mean of the draw."""
    if not clusters:
        return [0.0, 0.0]
    rng = random.Random(seed)
    stats = []
    for _ in range(samples):
        draw = [v for c in rng.choices(clusters, k=len(clusters)) for v in c]
        stats.append(_mean(draw))
    return _percentiles(stats, samples, level)


def family_ci(
    rows: list[dict[str, Any]],
    samples: int = BOOTSTRAP["samples"],
    level: float = BOOTSTRAP["level"],
    seed: int = BOOTSTRAP["seed"],
    key: str = "slot_f1",
) -> list[float]:
    """Interval for the row mean slot F1 (or `key`) when rows come from few families: rows of one
    family are correlated, so whole families are resampled with replacement."""
    return _cluster_ci(_by_family(rows, key), samples, level, seed)


def compare_ci(
    a: list[dict[str, Any]], b: list[dict[str, Any]], samples: int = BOOTSTRAP["samples"], level: float = BOOTSTRAP["level"], seed: int = BOOTSTRAP["seed"]
) -> dict[str, Any]:
    """Paired comparison: per-row difference b - a, families resampled. Both must score the same rows."""
    fa, fb = {r["id"]: r for r in a}, {r["id"]: r for r in b}
    if set(fa) != set(fb):
        raise CheckpointError("the two score files must cover the same rows")
    diffs = [{"id": i, "family": fa[i]["family"], "slot_f1": fb[i]["slot_f1"] - fa[i]["slot_f1"]} for i in sorted(fa)]
    return {"diff": _mean([d["slot_f1"] for d in diffs]), "diff_ci": family_ci(diffs, samples, level, seed)}


def score_predictions(
    predictions: list[dict[str, Any]],
    dev_rows: list[dict[str, Any]],
    data: Path = DATA,
    expect: set[str] | None = None,
    sdk: ExpressSDK | None = None,
    split: str = "dev",
) -> dict[str, Any]:
    """Scores one checkpoint's predictions. `expect`: ids that must all be present.
    `split="test"` scores against test references, with a family-level interval.

    `selection` is the mean of the gallery and synthetic mean row slot F1, so the larger
    synthetic dev set doesn't outweigh the gallery one (ADR 0009)."""
    checkpoints = {p.get("checkpoint") for p in predictions}
    if len(checkpoints) != 1:
        raise CheckpointError(f"predictions must be for one checkpoint, got {sorted(map(str, checkpoints))}")
    known = {r["id"]: r for r in dev_rows}
    cases = {c["id"]: c for c in SPLIT_CASES[split](data)}
    ids = [p["id"] for p in predictions]
    if unknown := sorted(set(ids) - (set(known) & set(cases))):
        raise CheckpointError(f"unknown ids (not in the {split} export): {unknown[:5]}")
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        raise CheckpointError(f"duplicate ids: {dupes[:5]}")
    if expect is not None and (missing := sorted(expect - set(ids))):
        raise CheckpointError(f"missing predictions for {len(missing)} ids: {missing[:5]}")
    if bad := sorted({str(p.get("finish")) for p in predictions} - set(FINISH)):
        raise CheckpointError(f"finish must be one of {FINISH}, got {bad}")

    sdk = sdk or ExpressSDK(CATALOG)
    index = RefIndex(sdk.catalog_json)
    check = CatalogCheck(sdk)
    rows = []
    for p in predictions:
        text = None if p["finish"] == "error" else p.get("text")
        r = score_response(sdk, index, cases[p["id"]], text, p["finish"])
        outside = kinds(check.response(text))
        unreachable = sorted({c for s in r["structure"].values() for c in s.get("orphans", [])})
        if any(s.get("missing_root") for s in r["structure"].values()):
            unreachable.append(NO_ROOT)  # nothing is attached when there is no root
        rows.append(
            {
                "id": p["id"],
                "source": known[p["id"]]["source"],
                "finish": p["finish"],
                "compiles": r["compiles"],
                "exact_match": r["exact_match"],
                "truncated": r["truncated"],
                "slot_f1": r["slot"]["f1"],
                "slot_f1_blind": r["slot_blind"]["f1"],
                "outside_catalog": bool(outside),
                "outside_catalog_kinds": outside,
                "unreachable": unreachable,
                **({"family": cases[p["id"]]["family"]} if split == "test" else {}),
            }
        )
    sources = {}
    for source in SOURCES:
        own = [r for r in rows if r["source"] == source]
        if own:
            sources[source] = {
                "rows": len(own),
                **{k: _mean([float(r[k]) for r in own]) for k in ("compiles", "exact_match", "truncated", "slot_f1", "slot_f1_blind", "outside_catalog")},
                "unreachable": _mean([float(bool(r["unreachable"])) for r in own]),
            }
    if split == "test":
        ci, ci_blind, how = family_ci(rows), family_ci(rows, key="slot_f1_blind"), {"clustered_by": "family"}
    else:
        ci, ci_blind, how = selection_ci(rows), selection_ci(rows, key="slot_f1_blind"), {"stratified_by": list(SOURCES)}
    return {
        "checkpoint": checkpoints.pop(),
        "split": split,
        "rows": rows,
        "sources": sources,
        "selection": _mean([s["slot_f1"] for s in sources.values()]),
        "selection_blind": _mean([s["slot_f1_blind"] for s in sources.values()]),
        "selection_ci": ci,
        "selection_blind_ci": ci_blind,
        "bootstrap": {**BOOTSTRAP, **how},
        "scorer": scorer_version(),
        "references": references_version(),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m a2ui_eval.checkpoints")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score", help="score one checkpoint's predictions JSONL")
    s.add_argument("predictions", type=Path)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--dev", type=Path, default=None, help="the prompt export (default: the split's export)")
    s.add_argument("--full", action="store_true", help="every dev row must be present (default: the subset; test always needs every row)")
    s.add_argument("--split", choices=tuple(SPLIT_CASES), default="dev")
    c = sub.add_parser("compare", help="paired, family-level comparison of two test score files (b - a)")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)
    args = p.parse_args(argv)
    if args.cmd == "compare":
        try:
            a, b = (json.loads(f.read_text(encoding="utf-8")) for f in (args.a, args.b))
            if a.get("split") != "test" or b.get("split") != "test":
                raise CheckpointError("compare needs test score files (split: test)")
            frozen = {c["id"] for c in test_cases()}
            for name, f in (("a", a), ("b", b)):
                if {r["id"] for r in f["rows"]} != frozen:
                    raise CheckpointError(f"{name} must score every test row ({len(frozen)}), got {len(f['rows'])}")
            out = compare_ci(a["rows"], b["rows"])
        except CheckpointError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(json.dumps({"a": a["checkpoint"], "b": b["checkpoint"], "a_selection": a["selection"], "b_selection": b["selection"], **out}))
        return 0
    try:
        dev_rows = read_jsonl(args.dev or (TEST_EXPORT if args.split == "test" else DEV_EXPORT))
        if args.split == "test":  # the guard is the frozen cases, not whatever export was passed in
            expect = {c["id"] for c in test_cases()}
        else:
            expect = {r["id"] for r in dev_rows if args.full or r["subset"]}
        out = score_predictions(read_jsonl(args.predictions), dev_rows, expect=expect, split=args.split)
    except CheckpointError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"checkpoint": out["checkpoint"], "split": out["split"], "selection": out["selection"], "selection_ci": out["selection_ci"], "selection_blind": out["selection_blind"], "sources": out["sources"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
