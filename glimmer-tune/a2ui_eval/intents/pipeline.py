"""The Phase 2 pipeline: draft the bank, build content, word it, check it, split it.

Model calls go through an injected `Complete` (tests stub it; the CLI uses
`teacher.litellm_complete`). Every step writes a file and is resumable or deterministic.
"""

from __future__ import annotations

import asyncio
import json
import random
import subprocess
from pathlib import Path
from typing import Any

from ..io import read_jsonl, write_jsonl
from ..seeds import ROOT
from ..teacher import CACHE_KEY, MODEL, PRICE_IN, PRICE_OUT, Complete
from . import checks, content as content_mod, wording
from .bank import (BANK_PATH, DOMAIN_PROMPT, DOMAINS_PROMPT, COLUMN_TYPES, BankError, Domain,
                   domain_to_json, extract_json, load_bank, parse_domain)

DIR = ROOT / "data" / "intents"
SLICE_DOMAINS = DIR / "slice_domains.json"
CONTENT = DIR / "content.jsonl"
WORDED = DIR / "worded.jsonl"
NEAR_DUP_T = 0.35  # gallery calibration: cross-family max 0.167, same-family p50 0.23 (SPEC)
TUNE_SIZE = 150
MODEL_TAG = f"model:{MODEL.rsplit('/', 1)[-1]}"


def script_tag(module: str) -> str:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    return f"script:{module}@{commit}"


def _request(msgs: list[dict[str, str]], max_tokens: int, temperature: float = 1.0) -> dict[str, Any]:
    return {
        "model": MODEL, "messages": msgs, "reasoning_effort": "low", "cache_key": CACHE_KEY + "-intents",
        "temperature": temperature, "max_tokens": max_tokens,
        "allowed_openai_params": ["reasoning_effort", "prompt_cache_key"],
        "env_file": str(ROOT.parent / ".env"),
    }


def usd(replies: list[dict[str, Any]]) -> float:
    tin = sum(r.get("prompt_tokens") or 0 for r in replies)
    tout = sum(r.get("completion_tokens") or 0 for r in replies)
    return tin * PRICE_IN / 1e6 + tout * PRICE_OUT / 1e6


# ---------------------------------------------------------------- bank

async def draft_bank(complete: Complete, n: int, workers: int = 8, avoid: list[str] | None = None,
                     ) -> tuple[list[Domain], list[dict[str, Any]], float]:
    """(valid domains, rejects with reasons, usd). One call lists domains, one call per domain
    describes it. A domain that fails validation is rejected and logged, not re-asked."""
    avoid_text = ", ".join(avoid or []) or "nothing"
    first = await complete(_request([{"role": "user", "content": DOMAINS_PROMPT.format(n=n, avoid=avoid_text)}], 2048))
    replies = [first]
    names = extract_json(first["content"])
    if not isinstance(names, list) or not all(isinstance(x, str) for x in names):
        raise BankError("domain list is not a JSON array of strings")
    names = list(dict.fromkeys(x.strip() for x in names if x.strip()))
    sem = asyncio.Semaphore(workers)

    async def one(name: str) -> tuple[Domain | None, dict[str, Any] | None, dict[str, Any] | None]:
        prompt = DOMAIN_PROMPT.format(name=name, types=", ".join(COLUMN_TYPES))
        async with sem:
            try:
                r = await complete(_request([{"role": "user", "content": prompt}], 4096))
            except Exception as e:  # noqa: BLE001 - one failed call must not stop the draft
                return None, None, {"name": name, "reason": f"call failed: {str(e)[-300:]}"}
        try:
            raw = extract_json(r["content"])
            if isinstance(raw, dict):
                raw["name"] = name  # the listed name wins over a reworded one
            return parse_domain(raw, MODEL_TAG), r, None
        except (BankError, ValueError) as e:
            return None, r, {"name": name, "reason": str(e), "raw": r["content"]}

    results = await asyncio.gather(*(one(x) for x in names))
    domains, rejects = [], []
    seen: set[str] = set()
    for d, r, rej in results:
        if r is not None:
            replies.append(r)
        if rej is not None:
            rejects.append(rej)
        elif d is not None and d.slug not in seen:
            seen.add(d.slug)
            domains.append(d)
    return domains, rejects, usd(replies)


def write_bank(domains: list[Domain], path: Path = BANK_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"domains": [domain_to_json(d) for d in domains]}, ensure_ascii=False, indent=1) + "\n")


def pick_slice_domains(domains: list[Domain], n: int, seed: int) -> list[str]:
    return sorted(random.Random(seed).sample([d.slug for d in domains], n))


def train_domains(domains: list[Domain], slice_slugs: list[str]) -> list[Domain]:
    held = set(slice_slugs)
    return [d for d in domains if d.slug not in held]


# ---------------------------------------------------------------- content

def build_content(seed: int, total: int, domains: list[Domain]) -> list[dict[str, Any]]:
    """Content rows with ids; `parent` is the parent's id for update rows."""
    specs = content_mod.generate(seed, total, domains)
    width = max(5, len(str(len(specs))))
    ids = [f"intent/{s['kind']}/{i:0{width}d}" for i, s in enumerate(specs)]
    out = []
    for i, s in enumerate(specs):
        s = dict(s)
        if "parent_index" in s:
            s["parent"] = ids[s.pop("parent_index")]
        out.append({"id": ids[i], "content": s, "seed": seed})
    return out


# ---------------------------------------------------------------- wording and checks

def _partial(out: Path) -> Path:
    return out.with_name(out.name + ".partial")


# Content keys that don't reach the wording prompt: changing them must not force a reword.
NOT_WORDED = ("bank_provenance", "parent", "parent_index")


def content_hash(content: dict[str, Any]) -> str:
    """Identifies what was worded. A worded record whose hash differs from its row's current
    content is stale (the bank or content was regenerated) and is worded again."""
    import hashlib

    keep = {k: v for k, v in content.items() if k not in NOT_WORDED}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def read_lenient(path: Path) -> list[dict[str, Any]]:
    """JSONL rows, skipping a line that doesn't parse (a write cut off by a hard kill)."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


async def word_rows(rows: list[dict[str, Any]], out: Path, complete: Complete, seed: int,
                    workers: int = 8, max_tokens: int = 2048) -> dict[str, Any]:
    """Words every content row not already worded in `out` (error rows retried). Resumable, and
    crash-safe: each reply is appended to `<out>.partial` as it arrives, and the next run picks
    those up. Checks run later (`check_rows`), so a check change never needs new model calls."""
    part = _partial(out)
    hashes = {r["id"]: content_hash(r["content"]) for r in rows}
    done = {r["id"]: r for r in read_jsonl(out) + read_lenient(part)
            if r.get("finish") != "error" and r.get("content_hash") == hashes.get(r["id"])}
    todo = [r for r in rows if r["id"] not in done]
    sem = asyncio.Semaphore(workers)
    part.parent.mkdir(parents=True, exist_ok=True)
    log = part.open("a", encoding="utf-8")

    async def one(row: dict[str, Any]) -> dict[str, Any]:
        rng = random.Random(f"{seed}:{row['id']}")
        style = wording.pick_style(rng)
        opener = wording.pick_opener(rng)  # drawn after the style, so earlier rows keep their style
        async with sem:
            try:
                r = await complete(_request(wording.messages(row["content"], style, opener), max_tokens))
            except Exception as e:  # noqa: BLE001
                return {"id": row["id"], "finish": "error", "error": str(e)[-300:], "style": style, "opener": opener}
        rec = {
            "id": row["id"], "content_hash": hashes[row["id"]], "model": MODEL_TAG,
            "style": style, "opener": opener, "reply": r["content"],
            "intent": wording.parse(r["content"]),
            "finish": "length" if r.get("finish_reason") == "length" else "stop",
            "prompt_tokens": r.get("prompt_tokens"), "completion_tokens": r.get("completion_tokens"),
            "reasoning_tokens": r.get("reasoning_tokens"),
        }
        log.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log.flush()
        return rec

    try:
        new = await asyncio.gather(*(one(r) for r in todo))
    finally:
        log.close()
    order = {r["id"]: i for i, r in enumerate(rows)}
    merged = {**done, **{p["id"]: p for p in new}}
    write_jsonl(out, sorted(merged.values(), key=lambda p: order.get(p["id"], len(order))))
    part.unlink(missing_ok=True)
    return {"asked": len(todo), "errors": sum(p["finish"] == "error" for p in new),
            "unparsed": sum(p.get("intent") is None for p in new if p["finish"] != "error"),
            "usd_upper_bound": round(usd(new), 4)}


def check_rows(content_rows: list[dict[str, Any]], worded: list[dict[str, Any]], references: list[str],
               catalog_json: dict[str, Any], t: float = NEAR_DUP_T) -> list[dict[str, Any]]:
    """One verdict per worded row: `ok` or the first failing check with its details. Rows are
    checked in content order, so the near-duplicate check keeps the earlier of two."""
    comps, jargon = checks.catalog_jargon(catalog_json)
    by_id = {r["id"]: r for r in worded}
    dup = checks.NearDup(references, t)
    out = []
    for c in content_rows:
        w = by_id.get(c["id"])
        if w is None or w.get("finish") == "error":
            continue
        text = w.get("intent")
        req = content_mod.required_strings(c["content"])
        if text is None:
            v = {"verdict": "unparsed"}
        elif w.get("finish") == "length":
            v = {"verdict": "length"}
        elif miss := checks.missing(text, req):
            v = {"verdict": "r2_missing", "detail": miss}
        elif hits := checks.register(text, req, comps, jargon):
            v = {"verdict": "register", "detail": hits}
        elif c["content"].get("layout") and (run := checks.copied(text, c["content"]["layout"])):
            v = {"verdict": "layout_copied", "detail": run}
        elif (d := dup.check(text)) is not None:
            v = {"verdict": f"near_dup_{d[0]}", "detail": round(d[1], 3)}
        else:
            v = {"verdict": "ok"}
        out.append({"id": c["id"], **v})
    return out


def assemble(content_rows: list[dict[str, Any]], worded: list[dict[str, Any]], verdicts: list[dict[str, Any]],
             seed: int, content_tag: str, tune_size: int = TUNE_SIZE) -> list[dict[str, Any]]:
    """Intent rows for the ok verdicts. An update row whose parent was dropped is dropped too.
    `tune` takes about `tune_size` rows stratified by kind (no update rows, whose parent may sit
    in train); the rest are `train`."""
    ok = {v["id"] for v in verdicts if v["verdict"] == "ok"}
    by_id = {r["id"]: r for r in worded}
    rows = []
    for c in content_rows:
        cont = c["content"]
        if c["id"] not in ok or (cont.get("parent") and cont["parent"] not in ok):
            continue
        rows.append({
            "id": c["id"], "intent": by_id[c["id"]]["intent"], "expect_ui": cont["kind"] != "text_only",
            "kind": cont["kind"], "domain": cont["domain"], "entity": cont["entity"], "content": cont,
            "parent": cont.get("parent"), "style": by_id[c["id"]]["style"],
            "provenance": {"intent": by_id[c["id"]].get("model", MODEL_TAG), "content": content_tag,
                           "schema": cont.get("bank_provenance", MODEL_TAG)},
        })
    rng = random.Random(seed)
    # A tune row must not be the parent of a train update row (Phase 4 answers parents as train),
    # so update rows and parents are excluded before sampling, and tune keeps its size.
    parents = {r["parent"] for r in rows if r["parent"]}
    by_kind: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        if r["kind"] != "update" and r["id"] not in parents:
            by_kind.setdefault(r["kind"], []).append(i)
    pool = sum(len(v) for v in by_kind.values())
    tune: set[int] = set()
    for kind, idx in sorted(by_kind.items()):
        k = min(len(idx), round(tune_size * len(idx) / pool)) if pool else 0
        tune |= set(rng.sample(idx, k))
    for i, r in enumerate(rows):
        r["split"] = "tune" if i in tune else "train"
    return rows
