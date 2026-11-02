"""Data commands: `python -m a2ui_eval.data_cli {generate,draft,build-eval,export,export-dev,freeze-prompt,...}`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .export import ExportError, approx_tokens, export_sft
from .fingerprint import fingerprint
from .generate import GenerationError, generate_family, load_splits, load_templates, read_jsonl, write_jsonl
from .prompt import PromptError, build_system_prompt, checked_system_prompt, freeze_prompt
from .refs import RefIndex
from .sdk import ExpressSDK
from .seeds import ROOT, load_seeds
from .synth.pipeline import SynthError
from .synth.skeleton import SkeletonError
from .synth.vocab_writer import VocabError

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
    An unreviewed row's intent is carried over only if its UI is unchanged.

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
        # An unreviewed edit is kept only while it still describes the same UI.
        keep = prev is not None and (prev.get("reviewed") or not ui_changed(c["id"]))
        rows.append({**c, "intent": prev["intent"], "reviewed": bool(prev.get("reviewed"))} if keep else {**c, "reviewed": False})
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
    synth = args.data / "synth"
    eval_cases = (
        read_jsonl(args.data / DRAFT.name)
        + read_jsonl(args.data / "dev" / "cases.jsonl")
        + read_jsonl(args.data / "test" / "cases.jsonl")
        + (read_jsonl(synth / "dev" / "cases.jsonl") if (synth / "dev" / "cases.jsonl").exists() else [])
    )
    train = read_jsonl(args.data / "train" / "cases.jsonl")
    skeleton_splits = None
    if args.mix is not None:
        from .synth.pipeline import load_skeleton_splits
        from .synth.skeleton import load_skeletons
        from .synth.catalog_model import CatalogModel
        import yaml

        doc = yaml.safe_load((args.data / "splits.yaml").read_text()) or {}
        skeleton_splits = load_skeleton_splits(doc, load_skeletons(CatalogModel(sdk)))
        if not (synth / "train" / "cases.jsonl").exists():
            raise ExportError(f"--mix needs {synth / 'train' / 'cases.jsonl'}; run `synth --split train` first")
        train += read_jsonl(synth / "train" / "cases.jsonl")
    rows, report = export_sft(
        train,
        eval_cases,
        splits,
        checked_system_prompt(sdk),
        approx_tokens if args.approx_tokens else load_tokenizer_counter(),
        args.max_seq_len,
        eval_seed_fingerprints(index, args.data),
        skeleton_splits,
        args.mix,
        args.seed,
    )
    write_jsonl(args.out, rows)
    print(json.dumps(report.to_dict(), indent=2))


def cmd_freeze_prompt(args) -> None:
    print(f"froze the system prompt to {freeze_prompt(ExpressSDK(CATALOG), force=args.force)}")


def cmd_export_dev(args) -> None:
    from .checkpoints import DEV_EXPORT, export_dev

    rows = export_dev(checked_system_prompt(ExpressSDK(CATALOG)), args.data, seed=args.seed)
    write_jsonl(args.out or DEV_EXPORT, rows)
    subset = [r for r in rows if r["subset"]]
    print(json.dumps({"rows": len(rows), "subset": len(subset), "out": str(args.out or DEV_EXPORT)}))


def cmd_lint(args) -> None:
    from .synth.catalog_model import CatalogModel
    from .synth.lint import lint_gallery, lint_rows, load_gallery_allowlist, stale_allowlist
    from .synth.skeleton import load_skeletons

    model = CatalogModel(ExpressSDK(CATALOG))
    problems: list[str] = []
    if args.gallery:
        seeds, allow = load_seeds(), load_gallery_allowlist()
        problems += [f"gallery {stem}: {', '.join(rules)}" for stem, rules in lint_gallery(seeds, model, allow).items()]
        problems += [f"stale allowlist entry {entry}" for entry in stale_allowlist(seeds, model, allow)]
    if args.synthetic:
        tags = {k: s.tags for k, s in load_skeletons(model).items()}
        rows = read_jsonl(args.synthetic)
        problems += [f"{rid}: {', '.join(rules)}" for rid, rules in lint_rows(rows, model, tags).items()]
    if not (args.gallery or args.synthetic):
        raise GenerationError("lint needs --gallery and/or --synthetic <cases.jsonl>")
    for line in problems:
        print(line, file=sys.stderr)
    if problems:
        raise GenerationError(f"{len(problems)} lint problems")
    print("lint passed")


def cmd_vocab(args) -> None:
    from .synth.catalog_model import CatalogModel
    import asyncio

    from .synth import vocab_writer
    from .synth.vocab_writer import DOMAINS, check_vocab_dir, repo_eval_texts

    model = CatalogModel(ExpressSDK(CATALOG))
    texts = repo_eval_texts(model, args.data)
    if args.check:
        problems = check_vocab_dir(model, texts)
        for name, errs in problems.items():
            print(f"{name}:\n  " + "\n  ".join(errs), file=sys.stderr)
        if problems:
            raise VocabError(f"{len(problems)} invalid vocabulary files")
        print("all vocabulary files valid")
        return
    domains = list(DOMAINS) if args.all else [args.domain]
    failed = asyncio.run(vocab_writer.write_domains(domains, model, texts))
    if failed:
        raise VocabError(f"failed domains: {failed}")


SYNTH = DATA / "synth"


def cmd_synth(args) -> None:
    from .synth.coverage import SynthConfig
    from .synth.pipeline import load_env, synthesize

    env = load_env(args.data)
    config = SynthConfig(target=args.target)
    rows, report = synthesize(env, args.count, args.seed, args.split, config)
    n = write_jsonl(args.data / "synth" / args.split / "cases.jsonl", rows)
    print(json.dumps(report.to_dict(), indent=2))
    print(f"wrote {n} synthetic {args.split} rows")
    if report.rows < args.count:
        raise GenerationError(f"shortfall: {report.rows} of {args.count} rows")


def cmd_coverage(args) -> None:
    from .synth.catalog_model import CatalogModel
    from .synth.coverage import SynthConfig, report, write_report

    model = CatalogModel(ExpressSDK(CATALOG))
    rows = read_jsonl(args.data / "synth" / "train" / "cases.jsonl")
    rep = report(rows, model, SynthConfig(target=args.target))
    write_report(rep, args.data / "synth" / "coverage.json", args.data / "synth" / "README.md")
    print(f"{rep['rows']} rows, {len(rep['gaps'])} keys below {rep['target']}")


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
    e.add_argument("--mix", type=float, nargs="?", const=0.7, default=None, help="synthetic share of the output (default 0.7); adds data/synth/train rows")
    e.add_argument("--seed", type=int, default=0, help="seed for the gallery oversampling and the final shuffle")
    e.add_argument("--approx-tokens", action="store_true", help="count tokens as ceil(chars/3) instead of loading the tokenizer (offline)")
    e.set_defaults(fn=cmd_export)
    fz = sub.add_parser("freeze-prompt", help="write data/system_prompt.txt (ADR 0015)")
    fz.add_argument("--force", action="store_true", help="overwrite a different frozen prompt; invalidates trained adapters")
    fz.set_defaults(fn=cmd_freeze_prompt)
    ed = sub.add_parser("export-dev", help="dev prompts for checkpoint scoring (gallery + synthetic, with a fixed subset)")
    ed.add_argument("--out", type=Path, default=None)
    ed.add_argument("--seed", type=int, default=0)
    ed.set_defaults(fn=cmd_export_dev)
    v = sub.add_parser("vocab", help="write domain vocabularies with meta/muse-spark-1.3")
    which = v.add_mutually_exclusive_group(required=True)
    which.add_argument("--domain")
    which.add_argument("--all", action="store_true")
    which.add_argument("--check", action="store_true", help="re-validate the committed files; no model call")
    v.set_defaults(fn=cmd_vocab)
    sy = sub.add_parser("synth", help="generate synthetic single-turn surfaces (no model calls)")
    sy.add_argument("--split", choices=("train", "dev"), required=True)
    sy.add_argument("--count", type=int, required=True)
    sy.add_argument("--seed", type=int, default=0)
    sy.add_argument("--target", type=int, default=30, help="coverage target N per key")
    sy.set_defaults(fn=cmd_synth)
    co = sub.add_parser("coverage", help="coverage report for data/synth/train")
    co.add_argument("--target", type=int, default=30)
    co.set_defaults(fn=cmd_coverage)
    li = sub.add_parser("lint", help="plausibility linter (S8)")
    li.add_argument("--gallery", action="store_true", help="every single-turn converted seed, after the allowlist")
    li.add_argument("--synthetic", type=Path, help="a synthetic cases.jsonl")
    li.set_defaults(fn=cmd_lint)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except (GenerationError, ExportError, PromptError, VocabError, SynthError, SkeletonError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
