"""`python -m glimmer_trainer {prepare,train,sample}`. Only `train --launch` and `sample` touch Fireworks."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from .loop import Config, Run, RunError, redact, sample_rows
from .prepare import PrepareError, check

GLIMMER_TUNE = Path(__file__).resolve().parents[2]
RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")  # model id glimmer-a2ui-<run>-sNNN stays within 63


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m glimmer_trainer")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "train"):
        s = sub.add_parser(name)
        s.add_argument("--train", type=Path, default=GLIMMER_TUNE / "exports" / "sft_mix.jsonl")
        s.add_argument("--dev", type=Path, default=GLIMMER_TUNE / "exports" / "sft_dev.jsonl")
        s.add_argument("--prompt", type=Path, default=GLIMMER_TUNE / "data" / "system_prompt.txt")
        s.add_argument("--max-seq-len", type=int, default=8192)
        s.add_argument("--rank", type=int, default=16)
        s.add_argument("--lr", type=float, default=5e-5)
        s.add_argument("--batch-size", type=int, default=16)
        s.add_argument("--every", type=int, default=25)
        s.add_argument("--max-tokens", type=int, default=3072)
        s.add_argument("--seed", type=int, default=0)
        s.add_argument("--workers", type=int, default=8)
        s.add_argument("--run-id", default=None, help="required with --launch; names the run directory and the promoted model")
        s.add_argument("--runs", type=Path, default=GLIMMER_TUNE / "runs")
        if name == "train":
            s.add_argument("--launch", action="store_true", help="open a Fireworks training session (remote; eval sampling is billed)")
    sm = sub.add_parser("sample", help="sample one saved checkpoint of a finished run on a prompt export (billed)")
    sm.add_argument("--run", type=Path, required=True, help="run directory holding manifest.json")
    sm.add_argument("--ckpt", required=True, help="checkpoint name in the manifest, e.g. s250")
    sm.add_argument("--cases", type=Path, required=True, help="prompt export, e.g. exports/sft_test.jsonl")
    sm.add_argument("--out", type=Path, required=True, help="predictions JSONL; never overwritten")
    sm.add_argument("--limit", type=int, default=None, help="only the first N rows (a probe)")
    sm.add_argument("--max-tokens", type=int, default=None, help="default: the run's config.max_tokens")
    sm.add_argument("--workers", type=int, default=8)
    return p


def sample(args: argparse.Namespace, remote) -> int:
    """Checks the output and checkpoint before any client exists; exits 1 when a row errored."""
    if args.out.exists():
        raise PrepareError(f"{args.out} exists; predictions are never overwritten")
    if args.limit is not None and args.limit < 1:
        raise PrepareError("--limit must be at least 1")
    manifest = json.loads((args.run / "manifest.json").read_text(encoding="utf-8"))
    paths = manifest.get("checkpoints", {})
    if args.ckpt not in paths:
        raise PrepareError(f"checkpoint {args.ckpt} is not in {args.run / 'manifest.json'} (have {sorted(paths)})")
    rows = read_jsonl(args.cases)[: args.limit]
    if not rows:
        raise PrepareError(f"{args.cases} has no rows")
    max_tokens = args.max_tokens or manifest.get("config", {}).get("max_tokens") or Config.max_tokens
    if remote is None:
        from .real import deps as remote
    preds = sample_rows(remote(args.cases), paths[args.ckpt], rows, args.ckpt, args.workers, max_tokens)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.out.open("x", encoding="utf-8") as f:  # exclusive create: never overwrite
            f.write("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in preds))
    except FileExistsError:
        raise PrepareError(f"{args.out} exists; predictions are never overwritten") from None
    errors = sum(p["finish"] == "error" for p in preds)
    for p in preds:
        if p["finish"] == "error":
            print(f"{p['id']}: {p.get('error')}", file=sys.stderr)
    print(json.dumps({"checkpoint": args.ckpt, "rows": len(preds), "errors": errors, "length": sum(p["finish"] == "length" for p in preds), "out": str(args.out)}))
    return 1 if errors else 0


def config(args: argparse.Namespace) -> Config:
    if getattr(args, "launch", False) and not args.run_id:
        raise PrepareError("--launch needs an explicit --run-id")
    args.run_id = args.run_id or "dry-run"
    if not RUN_ID.match(args.run_id):
        raise PrepareError(f"run id must match {RUN_ID.pattern}")
    return Config(
        run_id=args.run_id, rank=args.rank, learning_rate=args.lr, batch_size=args.batch_size, every=args.every,
        max_tokens=args.max_tokens, max_seq_len=args.max_seq_len, seed=args.seed, workers=args.workers,
    )


def main(argv: list[str] | None = None, local=None, remote=None) -> int:
    """`local()` -> (api, renderer, tokenizer); `remote(dev_path)` -> Deps. Injected by tests."""
    args = build_parser().parse_args(argv)
    if args.cmd == "sample":
        try:
            return sample(args, remote)
        except (PrepareError, RunError, FileNotFoundError) as e:
            print(f"error: {redact(str(e))}", file=sys.stderr)
            return 1
        except Exception as e:  # noqa: BLE001 - SDK errors may quote request headers; never print the key
            print(f"error: {type(e).__name__}: {redact(str(e))}", file=sys.stderr)
            return 1
    if local is None:
        from .real import renderer_and_tokenizer as local
    try:
        cfg = config(args)
        train, dev, frozen = read_jsonl(args.train), read_jsonl(args.dev), args.prompt.read_text(encoding="utf-8")
        api, renderer, tokenizer = local()
        report = check(cfg, train, dev, frozen, renderer, lambda ids: str(tokenizer.decode(ids)), api.LAST_ASSISTANT_TURN)
        print(json.dumps(report, indent=1))
        if args.cmd == "prepare" or not args.launch:
            if args.cmd == "train":
                print("not launched: pass --launch to open a Fireworks training session")
            return 0
        if remote is None:
            from .real import deps as remote
        Run(cfg, remote(args.dev), train, dev, args.runs / args.run_id).run()
    except (PrepareError, RunError, FileNotFoundError) as e:
        print(f"error: {redact(str(e))}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - SDK errors may quote request headers; never print the key
        print(f"error: {type(e).__name__}: {redact(str(e))}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
