"""Teacher baseline (ADR 0025): Muse Spark Open answers the dev prompts.

It writes predictions in the shape `checkpoints score` reads, so the teacher is scored by the
same scorer as Glimmer's checkpoints:

    uv run python -m a2ui_eval.teacher baseline --dry-run          # cost estimate, no call
    uv run python -m a2ui_eval.teacher baseline                    # paid: calls Baseten
    uv run python -m a2ui_eval.checkpoints score runs/teacher-spark-open/predictions.jsonl \\
        --full --out runs/teacher-spark-open/score.full.json

Only dev rows are sent (the test split stays frozen). The output is a baseline, never training
data: ADR 0025 does not allow Spark Open's answers into SFT targets. LiteLLM runs in llm_call.py's
throwaway environment, as for the vocabulary writer, so importing this module never imports it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .checkpoints import DATA, DEV_EXPORT, dev_cases
from .io import read_jsonl
from .seeds import ROOT

# LiteLLM model string. The dated snapshot (from Baseten's /v1/models, 2026-11-03), so runs repeat.
MODEL = "baseten/meta-models/muse-spark-open-1026"
CHECKPOINT = "spark-open"
CACHE_KEY = "glimmer-tune-teacher"
OUT = ROOT / "runs" / "teacher-spark-open" / "predictions.jsonl"
MAX_TOKENS = 3072  # the trainer's eval setting (trainer/glimmer_trainer/loop.py Config.max_tokens)
# Baseten list prices, US$ per 1M tokens, from third-party trackers on 2026-11-03. UNVERIFIED.
PRICE_IN = 1.25
PRICE_OUT = 4.25
CHARS_PER_TOKEN = 4.0  # rough; only for the dry-run estimate

LLM_ENV = ("litellm==1.106.6", "mcp>=2.3.0", "python-dotenv>=1.2.4")  # the explorer's pin

Complete = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class TeacherError(Exception):
    pass


def estimate(rows: list[dict[str, Any]], out_tokens: int = 400) -> dict[str, float]:
    chars = sum(len(m["content"]) for r in rows for m in r["messages"])
    in_tokens = chars / CHARS_PER_TOKEN
    total_out = len(rows) * out_tokens
    return {
        "rows": len(rows),
        "input_tokens": in_tokens,
        "output_tokens": total_out,
        "usd": in_tokens * PRICE_IN / 1e6 + total_out * PRICE_OUT / 1e6,
    }


def actual_usd(preds: list[dict[str, Any]]) -> float:
    """Upper bound: cached input is priced as uncached (Baseten's cache price is unknown)."""
    tin = sum(p.get("prompt_tokens") or 0 for p in preds)
    tout = sum(p.get("completion_tokens") or 0 for p in preds)
    return tin * PRICE_IN / 1e6 + tout * PRICE_OUT / 1e6


def _request(row: dict[str, Any], model: str, reasoning_effort: str | None) -> dict[str, Any]:
    return {
        "model": model,
        "messages": row["messages"],
        "reasoning_effort": reasoning_effort,
        "cache_key": CACHE_KEY,
        "temperature": 0.0,
        "max_tokens": MAX_TOKENS,
        # LiteLLM's baseten provider drops these as unsupported unless allowed explicitly
        "allowed_openai_params": ["reasoning_effort", "prompt_cache_key"],
        "env_file": str(ROOT.parent / ".env"),
    }


async def run_baseline(
    rows: list[dict[str, Any]],
    out: Path,
    complete: Complete,
    allowed_ids: set[str],
    model: str = MODEL,
    reasoning_effort: str | None = "low",
    workers: int = 8,
    limit: int | None = None,
) -> dict[str, Any]:
    """Answers every row not already answered in `out` (error rows are retried). Resumable."""
    if bad := sorted(r["id"] for r in rows if r["id"] not in allowed_ids):
        raise TeacherError(f"rows outside the dev cases: {bad[:5]}")
    rows = rows[:limit] if limit is not None else rows
    done: dict[str, dict[str, Any]] = {}
    if out.exists():
        for p in read_jsonl(out):
            if p.get("finish") != "error":
                done[p["id"]] = p
    todo = [r for r in rows if r["id"] not in done]
    sem = asyncio.Semaphore(workers)

    async def one(row: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            t0 = time.monotonic()
            try:
                reply = await complete(_request(row, model, reasoning_effort))
            except Exception as e:  # noqa: BLE001 - one failed row must not stop the run
                return {"id": row["id"], "checkpoint": CHECKPOINT, "text": None, "finish": "error",
                        "error": str(e)[-500:], "seconds": round(time.monotonic() - t0, 2)}
            return {
                "id": row["id"],
                "checkpoint": CHECKPOINT,
                "text": reply["content"],
                "finish": "length" if reply.get("finish_reason") == "length" else "stop",
                "prompt_tokens": reply.get("prompt_tokens"),
                "cached_tokens": reply.get("cached_tokens"),
                "completion_tokens": reply.get("completion_tokens"),
                "seconds": round(time.monotonic() - t0, 2),
            }

    new = await asyncio.gather(*(one(r) for r in todo))
    order = {r["id"]: i for i, r in enumerate(rows)}
    merged = {**done, **{p["id"]: p for p in new}}
    preds = sorted(merged.values(), key=lambda p: order.get(p["id"], len(order)))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(p) + "\n" for p in preds), encoding="utf-8")
    return {
        "rows": len(preds),
        "asked": len(todo),
        "errors": sum(p["finish"] == "error" for p in preds),
        "length": sum(p["finish"] == "length" for p in preds),
        "usd_upper_bound": actual_usd(new),
    }


async def litellm_complete(request: dict[str, Any]) -> dict[str, Any]:
    """Runs llm_call.py in a throwaway env with the explorer's LiteLLM pin. Keys stay in .env."""
    cmd = ["uv", "run", "--no-project", "--python", "3.13", *(a for p in LLM_ENV for a in ("--with", p)),
           "python", str(Path(__file__).parent / "llm_call.py")]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate(json.dumps(request).encode())
    if proc.returncode != 0:
        lines = err.decode().strip().splitlines()
        raise TeacherError(f"model call failed: {lines[-1] if lines else proc.returncode}")
    return json.loads(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m a2ui_eval.teacher")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baseline", help="Muse Spark Open answers the dev prompts (paid unless --dry-run)")
    b.add_argument("--dev", type=Path, default=DEV_EXPORT)
    b.add_argument("--out", type=Path, default=OUT)
    b.add_argument("--model", default=MODEL)
    b.add_argument("--reasoning-effort", default="low", help="'none' omits the parameter")
    b.add_argument("--workers", type=int, default=8)
    b.add_argument("--limit", type=int, default=None, help="only the first N rows (a smoke test)")
    b.add_argument("--dry-run", action="store_true", help="print the cost estimate and stop")
    args = p.parse_args(argv)

    rows = read_jsonl(args.dev)
    if args.limit is not None:
        rows = rows[: args.limit]
    print(json.dumps({"estimate": estimate(rows), "model": args.model}))
    if args.dry_run:
        return 0
    effort = None if args.reasoning_effort == "none" else args.reasoning_effort
    try:
        summary = asyncio.run(run_baseline(
            rows, args.out, litellm_complete, {c["id"] for c in dev_cases(DATA)},
            model=args.model, reasoning_effort=effort, workers=args.workers,
        ))
    except TeacherError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
