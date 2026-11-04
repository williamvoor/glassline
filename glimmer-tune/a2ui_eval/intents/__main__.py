"""`python -m a2ui_eval.intents <cmd>`. Paid commands (`bank`, `word`) print an estimate and
stop with --dry-run. Run them with `uv run --env-file ../.env`."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from collections import Counter
from pathlib import Path

from ..io import read_jsonl, write_jsonl
from ..seeds import ROOT
from ..teacher import PRICE_IN, PRICE_OUT, litellm_complete
from . import pipeline as P
from .bank import BANK_PATH, load_bank

CATALOG = ROOT / "catalogs" / "basic_v0_9.json"  # ADR 0008
# Rough per-call token guesses for the dry-run estimates (reasoning counts as output).
EST = {"bank": (400, 2500), "word": (700, 450)}


def _estimate(kind: str, calls: int) -> dict:
    tin, tout = EST[kind]
    return {"calls": calls, "usd": round(calls * (tin * PRICE_IN + tout * PRICE_OUT) / 1e6, 2),
            "note": "Baseten list price, UNVERIFIED; token counts are guesses"}


def gallery_references() -> list[str]:
    """Gallery dev and test intents. Test intents are read only for this leakage guard, as the
    export guard (`data_cli.export_eval_cases`) already does."""
    data = ROOT / "data"
    rows = read_jsonl(data / "dev" / "cases.jsonl") + read_jsonl(data / "test" / "cases.jsonl")
    return [r["intent"] for r in rows if r.get("source") != "synthetic"]


def references() -> list[str]:
    """Texts no train intent may sit close to: gallery dev and test, and the built slice cases."""
    return gallery_references() + [c["intent"] for c in read_jsonl(ROOT / "data" / "explorer_dev" / "cases.jsonl")]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m a2ui_eval.intents")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bank", help="Spark Open drafts the schema bank (paid)")
    b.add_argument("--domains", type=int, default=48)
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--add", action="store_true", help="keep the existing bank and add new domains")
    s = sub.add_parser("slice", help="pick the held-out domains for the explorer slice")
    s.add_argument("--n", type=int, default=6)
    s.add_argument("--seed", type=int, default=0)
    c = sub.add_parser("content", help="build content specs over the train domains")
    c.add_argument("--total", type=int, required=True)
    c.add_argument("--seed", type=int, default=0)
    w = sub.add_parser("word", help="Spark Open words content rows (paid)")
    w.add_argument("--limit", type=int, default=None, help="only the first N content rows")
    w.add_argument("--seed", type=int, default=0)
    w.add_argument("--workers", type=int, default=8)
    w.add_argument("--dry-run", action="store_true")
    a = sub.add_parser("assemble", help="check worded rows and write train/tune")
    a.add_argument("--seed", type=int, default=0)
    sc = sub.add_parser("slice-content", help="slice specs from the held-out domains")
    sc.add_argument("--seed", type=int, default=0)
    sw = sub.add_parser("slice-word", help="Spark Open words the slice specs (paid)")
    sw.add_argument("--seed", type=int, default=0)
    sw.add_argument("--dry-run", action="store_true")
    sub.add_parser("slice-select", help="check, select 5 per domain, write the prompt export")
    sp = sub.add_parser("slice-page", help="validate Glimmer candidates and write the picking page")
    sp.add_argument("--preds", type=Path, required=True)
    sp.add_argument("--no-render", action="store_true")
    sb = sub.add_parser("slice-build", help="turn picks.yaml into data/explorer_dev/cases.jsonl")
    sb.add_argument("--preds", type=Path, required=True)
    sb.add_argument("--person", required=True, help="who edited references (provenance)")
    m = sub.add_parser("sample", help="print N random assembled intents for review")
    m.add_argument("--n", type=int, default=30)
    m.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)

    if args.cmd == "bank":
        print(json.dumps({"estimate": _estimate("bank", args.domains + 1)}))
        if args.dry_run:
            return 0
        old = load_bank() if args.add and BANK_PATH.exists() else []
        domains, rejects, cost = asyncio.run(P.draft_bank(litellm_complete, args.domains,
                                                          avoid=[d.name for d in old]))
        P.write_bank(old + [d for d in domains if d.slug not in {o.slug for o in old}])
        write_jsonl(P.DIR / "bank_rejects.jsonl", rejects)
        print(json.dumps({"domains": len(domains), "rejected": len(rejects), "usd_upper_bound": round(cost, 4)}))
        return 0
    if args.cmd == "slice":
        slugs = P.pick_slice_domains(load_bank(), args.n, args.seed)
        P.SLICE_DOMAINS.write_text(json.dumps({"domains": slugs, "seed": args.seed,
                                               "provenance": P.script_tag("a2ui_eval.intents.slice")}, indent=1) + "\n")
        print(json.dumps(slugs))
        return 0
    if args.cmd == "content":
        held = json.loads(P.SLICE_DOMAINS.read_text())["domains"]
        rows = P.build_content(args.seed, args.total, P.train_domains(load_bank(), held))
        write_jsonl(P.CONTENT, rows)
        print(json.dumps(dict(Counter(r["content"]["kind"] for r in rows))))
        return 0
    if args.cmd == "word":
        rows = read_jsonl(P.CONTENT)[: args.limit]
        done = {(r["id"], r.get("content_hash")) for r in read_jsonl(P.WORDED) + P.read_lenient(P._partial(P.WORDED))
                if r.get("finish") != "error"}
        todo = sum((r["id"], P.content_hash(r["content"])) not in done for r in rows)
        print(json.dumps({"estimate": _estimate("word", todo)}))
        if args.dry_run:
            return 0
        print(json.dumps(asyncio.run(P.word_rows(rows, P.WORDED, litellm_complete, args.seed, args.workers))))
        return 0
    if args.cmd == "assemble":
        content_rows = read_jsonl(P.CONTENT)
        worded = read_jsonl(P.WORDED)
        verdicts = P.check_rows(content_rows, worded, references(), json.loads(CATALOG.read_text()))
        write_jsonl(P.DIR / "verdicts.jsonl", verdicts)
        rows = P.assemble(content_rows, worded, verdicts, args.seed, P.script_tag("a2ui_eval.intents.content"))
        write_jsonl(P.DIR / "train.jsonl", [r for r in rows if r["split"] == "train"])
        write_jsonl(P.DIR / "tune.jsonl", [r for r in rows if r["split"] == "tune"])
        print(json.dumps({"verdicts": dict(Counter(v["verdict"] for v in verdicts)),
                          "train": sum(r["split"] == "train" for r in rows),
                          "tune": sum(r["split"] == "tune" for r in rows)}))
        return 0
    if args.cmd.startswith("slice-"):
        return _slice(args)
    if args.cmd == "sample":
        rows = read_jsonl(P.DIR / "train.jsonl") + read_jsonl(P.DIR / "tune.jsonl")
        for r in random.Random(args.seed).sample(rows, min(args.n, len(rows))):
            print(f"--- {r['id']} [{r['domain']} · {r['style'][:30]}]\n{r['intent']}\n")
        return 0
    return 1


def _slice(args) -> int:
    from ..checkpoints import fewshot_examples
    from ..prompt import checked_system_prompt
    from ..refs import RefIndex
    from ..sdk import ExpressSDK
    from ..validate import Validator
    from . import slice as S

    held = json.loads(P.SLICE_DOMAINS.read_text())["domains"]
    if args.cmd == "slice-content":
        rows = S.slice_content(load_bank(), held, args.seed)
        write_jsonl(S.CONTENT, rows)
        print(json.dumps(dict(Counter(r["content"]["domain"] for r in rows))))
        return 0
    if args.cmd == "slice-word":
        rows = read_jsonl(S.CONTENT)
        print(json.dumps({"estimate": _estimate("word", len(rows))}))
        if args.dry_run:
            return 0
        print(json.dumps(asyncio.run(P.word_rows(rows, S.WORDED, litellm_complete, args.seed, workers=4))))
        return 0
    sdk = ExpressSDK(CATALOG)
    worded = read_jsonl(S.WORDED)
    intents = {w["id"]: w["intent"] for w in worded if w.get("intent")}
    if args.cmd == "slice-select":
        content_rows = read_jsonl(S.CONTENT)
        # Slice intents must not sit close to gallery eval intents or to any train intent. The
        # check is symmetric, so this one guard covers both directions once train is fully worded.
        train_worded = read_jsonl(P.WORDED)
        S.require_complete(read_jsonl(P.CONTENT), train_worded, P._partial(P.WORDED).exists())
        refs = S.references_for_slice(gallery_references(), read_jsonl(S.CASES),
                                      [w["intent"] for w in train_worded if w.get("intent")])
        verdicts = P.check_rows(content_rows, worded, refs, sdk.catalog_json)
        dropped = {k for k, v in S.load_picks().items() if isinstance(v, dict) and "drop" in v}
        selected = S.select(content_rows, verdicts, dropped=dropped)
        write_jsonl(S.DIR / "verdicts.jsonl", verdicts)
        write_jsonl(S.SELECTED, selected)
        write_jsonl(S.EXPORT, S.export_rows(selected, intents, checked_system_prompt(sdk), fewshot_examples()))
        print(json.dumps({"verdicts": dict(Counter(v["verdict"] for v in verdicts)), "selected": len(selected),
                          "export": str(S.EXPORT)}))
        return 0
    selected = read_jsonl(S.SELECTED)
    preds = read_jsonl(args.preds)
    if args.cmd == "slice-page":
        shots_dir = S.DIR / "shots"
        shots_dir.mkdir(parents=True, exist_ok=True)

        def run(validator):
            return S.candidates(preds, lambda p: validator.check(
                p.get("text"), p.get("finish"), screenshot=shots_dir / f"{p['id'].replace('/', '_')}_{p.get('sample', 0)}.png"))

        if args.no_render:
            cands = run(Validator(sdk))
        else:
            from ..render import Renderer
            with Renderer() as r:
                cands = run(Validator(sdk, r))
        (S.DIR / "candidates.json").write_text(json.dumps(cands, ensure_ascii=False))

        def shots(cid, k):
            base = f"shots/{cid.replace('/', '_')}_{k}"
            return [f"{base}.{w}.png" for w in (375, 1280) if (S.DIR / f"{base}.{w}.png").exists()]

        (S.DIR / "picks.html").write_text(S.page(selected, intents, cands, shots))
        print(json.dumps({"cases": len(cands), "passing": sum(any(c["pass"] for c in v) for v in cands.values()),
                          "page": str(S.DIR / "picks.html")}))
        return 0
    if args.cmd == "slice-build":
        from ..data_cli import eval_seed_fingerprints

        index = RefIndex(sdk.catalog_json)
        cands = json.loads((S.DIR / "candidates.json").read_text())
        gallery = {c["fingerprint"] for c in read_jsonl(ROOT / "data" / "dev" / "cases.jsonl")
                   + read_jsonl(ROOT / "data" / "test" / "cases.jsonl")} | eval_seed_fingerprints(index)
        v = Validator(sdk)
        cases = S.build_cases(selected, intents, cands, S.load_picks(), sdk, index, gallery,
                              lambda text: v.mechanical(text, "stop"), args.person, P.MODEL_TAG)
        write_jsonl(S.CASES, cases)
        print(json.dumps({"cases": len(cases), "families": dict(Counter(c["family"] for c in cases))}))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
