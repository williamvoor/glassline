"""Data commands: `python -m a2ui_eval.data_cli {generate,draft,build-eval,export}`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .export import ExportError, export_sft
from .fingerprint import fingerprint
from .generate import GenerationError, generate_family, load_splits, load_templates, read_jsonl, write_jsonl
from .prompt import build_system_prompt
from .refs import RefIndex
from .sdk import ExpressSDK
from .seeds import ROOT, load_seeds

CATALOG = ROOT / "catalogs" / "basic_v0_9.json"  # ADR 0008
DATA = ROOT / "data"
DRAFT = DATA / "intents_draft.jsonl"
EXPORT = ROOT / "exports" / "sft_train.jsonl"
TOKENIZER = "meta-models/Muse-Glimmer-30B"


def _setup(data: Path) -> tuple[ExpressSDK, RefIndex, dict, dict[str, str]]:
    sdk = ExpressSDK(CATALOG)
    templates = load_templates()
    splits = load_splits(load_seeds(), data / "splits.yaml")
    return sdk, RefIndex(sdk.catalog_json), templates, splits


def _generate(sdk, index, templates, splits, wanted: dict[str, int], seed: int) -> list[dict]:
    cases = []
    for family, template in sorted(templates.items()):
        split = splits[family]
        if split not in wanted:
            continue
        cases.extend(generate_family(sdk, index, template, split, wanted[split], seed))
    skipped = sorted(f for f, s in splits.items() if s in wanted and f not in templates)
    if skipped:
        print(f"no template yet, skipped {len(skipped)} families: {', '.join(skipped)}", file=sys.stderr)
    return cases


def cmd_generate(args) -> None:
    sdk, index, templates, splits = _setup(args.data)
    n = write_jsonl(args.data / "train" / "cases.jsonl", _generate(sdk, index, templates, splits, {"train": args.count}, args.seed))
    print(f"wrote {n} train cases")


def merge_reviews(old: list[dict], new: list[dict]) -> tuple[list[dict], list[str]]:
    """New draft rows, with `intent` and `reviewed` carried over from `old` by id.

    Fails if a reviewed id is gone, or its UI changed: the approved intent described
    the old one. The UI is compared by `reference_express` (text and values) and by
    fingerprint (structure), because the fingerprint alone ignores text. Returns the
    rows and the unreviewed ids that are gone.
    """
    old_by_id = {r["id"]: r for r in old}
    new_by_id = {c["id"]: c for c in new}

    def ui_changed(i: str) -> bool:
        before, after = old_by_id[i], new_by_id[i]
        return before["reference_express"] != after["reference_express"] or before["fingerprint"] != after["fingerprint"]

    missing = [i for i, r in old_by_id.items() if r.get("reviewed") and i not in new_by_id]
    changed = [i for i, r in old_by_id.items() if r.get("reviewed") and i in new_by_id and ui_changed(i)]
    if missing or changed:
        raise GenerationError(f"reviewed rows would be lost: missing {missing}, UI changed {changed}")
    rows = []
    for c in new:
        prev = old_by_id.get(c["id"])
        rows.append({**c, "intent": prev["intent"], "reviewed": bool(prev.get("reviewed"))} if prev else {**c, "reviewed": False})
    return rows, [i for i in old_by_id if i not in new_by_id]


def cmd_draft(args) -> None:
    path = args.data / DRAFT.name
    if args.keep_reviews and not path.exists():
        raise GenerationError(f"--keep-reviews needs an existing {path}")
    if path.exists() and not (args.force or args.keep_reviews):
        raise GenerationError(f"{path} exists and may hold your edits; pass --keep-reviews to keep them or --force to overwrite it")
    sdk, index, templates, splits = _setup(args.data)
    cases = _generate(sdk, index, templates, splits, {"dev": args.dev_count, "test": args.test_count}, args.seed)
    if args.keep_reviews:
        rows, dropped = merge_reviews(read_jsonl(path), cases)
        if dropped:
            print(f"dropped {len(dropped)} unreviewed rows no longer generated: {', '.join(dropped)}", file=sys.stderr)
    else:
        rows = [{**c, "reviewed": False} for c in cases]
    n = write_jsonl(path, rows)
    print(f"wrote {n} draft cases to {path} ({sum(r['reviewed'] for r in rows)} reviewed); edit intents, then set reviewed: true")


def build_eval(data: Path, splits: tuple[str, ...] = ("dev", "test")) -> dict[str, int]:
    """Writes each of `splits` that has reviewed rows; a split with none is left alone, so test is never frozen empty.

    Pass `("dev",)` to build dev while leaving an approved test split unfrozen.
    """
    rows = read_jsonl(data / DRAFT.name)
    reviewed = {
        split: [{k: v for k, v in r.items() if k != "reviewed"} for r in rows if r["split"] == split and r.get("reviewed")]
        for split in splits
    }
    reviewed = {split: cases for split, cases in reviewed.items() if cases}
    if not reviewed:
        raise GenerationError(f"no reviewed rows in {data / DRAFT.name}")
    test_path = data / "test" / "cases.jsonl"
    if "test" in reviewed and test_path.exists():
        raise GenerationError(f"{test_path} exists; the test split is frozen")
    return {split: write_jsonl(data / split / "cases.jsonl", cases) for split, cases in reviewed.items()}


def cmd_build_eval(args) -> None:
    print(build_eval(args.data, (args.split,) if args.split else ("dev", "test")))


def load_tokenizer_counter(name: str = TOKENIZER):
    from tokenizers import Tokenizer  # network on first use; never called by tests

    tok = Tokenizer.from_pretrained(name)
    return lambda text: len(tok.encode(text, add_special_tokens=False).ids)


def eval_seed_fingerprints(index: RefIndex, data: Path = DATA) -> set[str]:
    seeds = load_seeds()
    splits = load_splits(seeds, data / "splits.yaml")
    return {fingerprint(s.messages, index) for f, s in seeds.items() if splits[f] in ("dev", "test")}


def cmd_export(args) -> None:
    sdk, index, _, splits = _setup(args.data)
    eval_cases = read_jsonl(args.data / DRAFT.name) + read_jsonl(args.data / "dev" / "cases.jsonl") + read_jsonl(args.data / "test" / "cases.jsonl")
    rows, report = export_sft(
        read_jsonl(args.data / "train" / "cases.jsonl"),
        eval_cases,
        splits,
        build_system_prompt(sdk),
        load_tokenizer_counter(),
        args.max_seq_len,
        eval_seed_fingerprints(index, args.data),
    )
    write_jsonl(args.out, rows)
    print(json.dumps(report.to_dict(), indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m a2ui_eval.data_cli")
    p.add_argument("--data", type=Path, default=DATA)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--count", type=int, default=60)
    g.add_argument("--seed", type=int, default=0)
    g.set_defaults(fn=cmd_generate)
    d = sub.add_parser("draft")
    d.add_argument("--dev-count", type=int, default=12)
    d.add_argument("--test-count", type=int, default=17)
    d.add_argument("--seed", type=int, default=0)
    d.add_argument("--force", action="store_true", help="overwrite the draft, discarding edits and reviews")
    d.add_argument("--keep-reviews", action="store_true", help="regenerate, keeping intents and reviews by id")
    d.set_defaults(fn=cmd_draft)
    b = sub.add_parser("build-eval")
    b.add_argument("--split", choices=("dev", "test"), help="build only this split (default: both)")
    b.set_defaults(fn=cmd_build_eval)
    e = sub.add_parser("export")
    e.add_argument("--max-seq-len", type=int, default=8192)
    e.add_argument("--out", type=Path, default=EXPORT)
    e.set_defaults(fn=cmd_export)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except (GenerationError, ExportError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
