"""Teacher baseline (ADR 0025): Muse Spark Open answers the dev prompts.

It writes predictions in the shape `checkpoints score` reads, so the teacher is scored by the
same scorer as Glimmer's checkpoints:

    uv run python -m a2ui_eval.teacher baseline --dry-run          # cost estimate, no call
    uv run python -m a2ui_eval.teacher baseline                    # paid: calls Baseten
    uv run python -m a2ui_eval.checkpoints score runs/teacher-spark-open/predictions.jsonl \\
        --full --out runs/teacher-spark-open/score.full.json

Only dev rows are sent (the test split stays frozen). This `baseline` output is a
measurement, not training data. ADR 0034 (superseding 0025) allows Spark Open targets only through
the Approach v2 validator. LiteLLM runs in llm_call.py's throwaway environment, so importing this
module never imports it.
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


def allowed_row_ids(data: Path = DATA) -> set[str]:
    """Rows the teacher may answer: dev cases (Phase 1) and the intent set's train and tune rows
    (Phases 3 and 4). Never test."""
    ids = {c["id"] for c in dev_cases(data)}
    for name in ("train", "tune"):
        path = data / "intents" / f"{name}.jsonl"
        if path.exists():
            ids |= {r["id"] for r in read_jsonl(path)}
    return ids


def only_source(rows: list[dict[str, Any]], source: str | None) -> list[dict[str, Any]]:
    """The rows of one dev source (`gallery` for the Phase 1 teacher ceiling), or all of them."""
    return rows if source is None else [r for r in rows if r.get("source") == source]


def _request(row: dict[str, Any], model: str, reasoning_effort: str | None, temperature: float = 0.0, top_p: float | None = None, max_tokens: int = MAX_TOKENS) -> dict[str, Any]:
    return {
        "model": model,
        "messages": row["messages"],
        "reasoning_effort": reasoning_effort,
        "cache_key": CACHE_KEY,
        "temperature": temperature,
        **({"top_p": top_p} if top_p is not None else {}),
        "max_tokens": max_tokens,
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
    n: int = 1,
    temperature: float = 0.0,
    top_p: float | None = None,
    max_tokens: int = MAX_TOKENS,
) -> dict[str, Any]:
    """Answers every (row, sample) not already answered in `out` (error rows are retried).
    Resumable. With `n` > 1 each row is sampled `n` times and predictions carry a `sample` index."""
    if bad := sorted(r["id"] for r in rows if r["id"] not in allowed_ids):
        raise TeacherError(f"rows the teacher may not answer: {bad[:5]}")
    if n > 1 and not temperature > 0:
        raise TeacherError("n > 1 needs a temperature above 0, or every sample is the same answer")
    rows = rows[:limit] if limit is not None else rows
    key = (lambda p: (p["id"], p.get("sample", 0))) if n > 1 else (lambda p: (p["id"], 0))
    done: dict[tuple[str, int], dict[str, Any]] = {}
    if out.exists():
        for p in read_jsonl(out):
            if p.get("finish") != "error":
                done[key(p)] = p
    todo = [(r, k) for r in rows for k in range(n) if (r["id"], k) not in done]
    sem = asyncio.Semaphore(workers)

    async def one(row: dict[str, Any], k: int) -> dict[str, Any]:
        tag = {"sample": k} if n > 1 else {}
        async with sem:
            t0 = time.monotonic()
            try:
                reply = await complete(_request(row, model, reasoning_effort, temperature, top_p, max_tokens))
            except Exception as e:  # noqa: BLE001 - one failed row must not stop the run
                return {"id": row["id"], **tag, "checkpoint": CHECKPOINT, "text": None, "finish": "error",
                        "error": str(e)[-500:], "seconds": round(time.monotonic() - t0, 2)}
            return {
                "id": row["id"],
                **tag,
                "checkpoint": CHECKPOINT,
                "text": reply["content"],
                "finish": "length" if reply.get("finish_reason") == "length" else "stop",
                "prompt_tokens": reply.get("prompt_tokens"),
                "cached_tokens": reply.get("cached_tokens"),
                "completion_tokens": reply.get("completion_tokens"),
                "reasoning_tokens": reply.get("reasoning_tokens"),
                "seconds": round(time.monotonic() - t0, 2),
            }

    new = await asyncio.gather(*(one(r, k) for r, k in todo))
    order = {r["id"]: i for i, r in enumerate(rows)}
    merged = {**done, **{key(p): p for p in new}}
    preds = sorted(merged.values(), key=lambda p: (order.get(p["id"], len(order)), p.get("sample", 0)))
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
    b.add_argument("--source", choices=("gallery", "synthetic"), default=None, help="only this dev source")
    b.add_argument("--n", type=int, default=1, help="samples per row (best-of-n)")
    b.add_argument("--temperature", type=float, default=0.0)
    b.add_argument("--top-p", type=float, default=None)
    b.add_argument("--max-tokens", type=int, default=MAX_TOKENS, help="reasoning counts against it")
    args = p.parse_args(argv)

    rows = only_source(read_jsonl(args.dev), args.source)
    if args.limit is not None:
        rows = rows[: args.limit]
    est = estimate(rows)
    est = {**est, "samples": len(rows) * args.n, "usd": est["usd"] * args.n}
    print(json.dumps({"estimate": est, "model": args.model}))
    if args.dry_run:
        return 0
    effort = None if args.reasoning_effort == "none" else args.reasoning_effort
    try:
        summary = asyncio.run(run_baseline(
            rows, args.out, litellm_complete, allowed_row_ids(DATA),
            model=args.model, reasoning_effort=effort, workers=args.workers,
            n=args.n, temperature=args.temperature, top_p=args.top_p, max_tokens=args.max_tokens,
        ))
    except TeacherError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
