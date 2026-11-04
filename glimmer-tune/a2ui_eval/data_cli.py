"""Data commands: `python -m a2ui_eval.data_cli {export,freeze-prompt,export-dev,export-test,grammar,lint}`.

The template and synthetic generators (`generate`, `draft`, `build-eval`, `vocab`, `synth`,
`coverage`) were retired to `archive/` for the distill restart (docs/cleanup-plan.md). The
frozen `data/dev` and `data/test` case files are now the source of truth.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .catalog_check import CatalogCheck
from .export import ExportError, approx_tokens, export_sft
from .fingerprint import fingerprint
from .io import SplitError, load_splits, read_jsonl, write_jsonl
from .prompt import PromptError, build_system_prompt, checked_system_prompt, freeze_prompt
from .refs import RefIndex
from .sdk import ExpressSDK
from .seeds import ROOT, load_seeds

CATALOG = ROOT / "catalogs" / "basic_v0_9.json"  # ADR 0008
DATA = ROOT / "data"
DRAFT = DATA / "intents_draft.jsonl"
EXPORT = ROOT / "exports" / "sft_train.jsonl"
TOKENIZER = "meta-models/Muse-Glimmer-30B"


class DataError(Exception):
    pass


def _setup(data: Path) -> tuple[ExpressSDK, RefIndex, dict[str, str]]:
    sdk = ExpressSDK(CATALOG)
    splits = load_splits(load_seeds(), data / "splits.yaml")
    return sdk, RefIndex(sdk.catalog_json), splits


def load_tokenizer_counter(name: str = TOKENIZER):
    from tokenizers import Tokenizer  # network on first use; never called by tests

    tok = Tokenizer.from_pretrained(name)
    return lambda text: len(tok.encode(text, add_special_tokens=False).ids)


def eval_seed_fingerprints(index: RefIndex, data: Path = DATA) -> set[str]:
    seeds = load_seeds()
    splits = load_splits(seeds, data / "splits.yaml")
    return {fingerprint(s.messages, index) for f, s in seeds.items() if splits[f] in ("dev", "test")}


def cmd_export(args) -> None:
    sdk, index, splits = _setup(args.data)
    synth = args.data / "synth"
    eval_cases = (
        read_jsonl(args.data / DRAFT.name)
        + read_jsonl(args.data / "dev" / "cases.jsonl")
        + read_jsonl(args.data / "test" / "cases.jsonl")
        + (read_jsonl(synth / "dev" / "cases.jsonl") if (synth / "dev" / "cases.jsonl").exists() else [])
    )
    if not args.train.exists():
        raise DataError(f"no train cases at {args.train}")
    rows, report = export_sft(
        read_jsonl(args.train),
        eval_cases,
        splits,
        checked_system_prompt(sdk),
        approx_tokens if args.approx_tokens else load_tokenizer_counter(),
        args.max_seq_len,
        eval_seed_fingerprints(index, args.data),
        catalog_check=CatalogCheck(sdk),
    )
    write_jsonl(args.out, rows)
    print(json.dumps(report.to_dict(), indent=2))


def cmd_freeze_prompt(args) -> None:
    print(f"froze the system prompt to {freeze_prompt(ExpressSDK(CATALOG), force=args.force)}")


def cmd_export_dev(args) -> None:
    from .checkpoints import DEV_EXPORT, export_dev, fewshot_examples, with_fewshot

    rows = export_dev(checked_system_prompt(ExpressSDK(CATALOG)), args.data, seed=args.seed)
    extra = {}
    if args.fewshot:
        if args.out is None:
            raise SystemExit("--fewshot needs --out (the plain dev export stays at its usual path)")
        ex = fewshot_examples(args.data)
        rows = with_fewshot(rows, ex)
        extra = {"fewshot": [c["id"] for c in ex]}
    write_jsonl(args.out or DEV_EXPORT, rows)
    subset = [r for r in rows if r["subset"]]
    print(json.dumps({"rows": len(rows), "subset": len(subset), "out": str(args.out or DEV_EXPORT), **extra}))


def cmd_export_test(args) -> None:
    from .checkpoints import TEST_EXPORT, export_test

    rows = export_test(checked_system_prompt(ExpressSDK(CATALOG)), args.data)
    write_jsonl(args.out or TEST_EXPORT, rows)
    print(json.dumps({"rows": len(rows), "families": len({r["family"] for r in rows}), "out": str(args.out or TEST_EXPORT)}))


def cmd_grammar(args) -> None:
    from .grammar import chat_gbnf

    out = args.out or ROOT / "exports" / "express_chat.gbnf"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = chat_gbnf(CatalogCheck(ExpressSDK(CATALOG)))
    out.write_text(text, encoding="utf-8")
    print(json.dumps({"out": str(out), "rules": len(text.splitlines())}))


def cmd_lint(args) -> None:
    from .catalog_model import CatalogModel
    from .lint import lint_gallery, lint_rows, load_gallery_allowlist, stale_allowlist

    model = CatalogModel(ExpressSDK(CATALOG))
    problems: list[str] = []
    if args.gallery:
        seeds, allow = load_seeds(), load_gallery_allowlist()
        problems += [f"gallery {stem}: {', '.join(rules)}" for stem, rules in lint_gallery(seeds, model, allow).items()]
        problems += [f"stale allowlist entry {entry}" for entry in stale_allowlist(seeds, model, allow)]
    if args.synthetic:
        rows = read_jsonl(args.synthetic)
        problems += [f"{rid}: {', '.join(rules)}" for rid, rules in lint_rows(rows, model, {}).items()]
    if not (args.gallery or args.synthetic):
        raise DataError("lint needs --gallery and/or --synthetic <cases.jsonl>")
    for line in problems:
        print(line, file=sys.stderr)
    if problems:
        raise DataError(f"{len(problems)} lint problems")
    print("lint passed")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m a2ui_eval.data_cli")
    p.add_argument("--data", type=Path, default=DATA)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--max-seq-len", type=int, default=8192)
    e.add_argument("--train", type=Path, required=True, help="train cases.jsonl to export")
    e.add_argument("--out", type=Path, default=EXPORT)
    e.add_argument("--approx-tokens", action="store_true", help="count tokens as ceil(chars/3) instead of loading the tokenizer (offline)")
    e.set_defaults(fn=cmd_export)
    fz = sub.add_parser("freeze-prompt", help="write data/system_prompt.txt (ADR 0015)")
    fz.add_argument("--force", action="store_true", help="overwrite a different frozen prompt; invalidates trained adapters")
    fz.set_defaults(fn=cmd_freeze_prompt)
    ed = sub.add_parser("export-dev", help="dev prompts for checkpoint scoring (gallery + synthetic; the subset is 6 gallery rows per family)")
    ed.add_argument("--out", type=Path, default=None)
    ed.add_argument("--seed", type=int, default=0)
    ed.add_argument("--fewshot", action="store_true", help="put the 3 frozen examples (data/fewshot3.jsonl) before each question (needs --out)")
    ed.set_defaults(fn=cmd_export_dev)
    et = sub.add_parser("export-test", help="test prompts for the one-time final evaluation (gallery only, with families)")
    et.add_argument("--out", type=Path, default=None)
    et.set_defaults(fn=cmd_export_test)
    gr = sub.add_parser("grammar", help="write the Express GBNF (with Glimmer's channel framing) for constrained sampling")
    gr.add_argument("--out", type=Path, default=None, help="default: exports/express_chat.gbnf")
    gr.set_defaults(fn=cmd_grammar)
    li = sub.add_parser("lint", help="plausibility linter (S8)")
    li.add_argument("--gallery", action="store_true", help="every single-turn converted seed, after the allowlist")
    li.add_argument("--synthetic", type=Path, help="a cases.jsonl to lint (synthetic dev, or distilled rows)")
    li.set_defaults(fn=cmd_lint)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except (DataError, SplitError, ExportError, PromptError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
