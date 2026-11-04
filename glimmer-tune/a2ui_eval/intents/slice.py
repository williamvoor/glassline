"""The explorer-shaped dev slice (ADR 0038; SPEC "Approach v2 Phase 2 completion").

Intents come from the held-out schema-bank domains through the same wording and checks as train
rows. Glimmer base proposes 8 candidates per intent; a person picks one (and may edit it) in a
hand-written `picks.yaml`; `build_cases` turns the picks into frozen eval cases.
"""

from __future__ import annotations

import html
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from ..fingerprint import fingerprint
from ..refs import RefIndex
from ..sdk import ExpressSDK
from ..seeds import ROOT
from . import content as content_mod
from .bank import Domain

DIR = ROOT / "data" / "explorer_dev"
CONTENT = DIR / "content.jsonl"
WORDED = DIR / "worded.jsonl"
SELECTED = DIR / "selected.jsonl"
PICKS = DIR / "picks.yaml"
CASES = DIR / "cases.jsonl"
EXPORT = ROOT / "exports" / "explorer_dev_fewshot3.jsonl"
DATA_KINDS = ("dashboard", "grid", "stats", "list", "detail", "filter", "form", "state")
PER_DOMAIN = 5
GLIMMER_TAG = "model:glimmer-base"


class SliceError(Exception):
    pass


def slice_content(domains: list[Domain], slice_slugs: list[str], seed: int = 0) -> list[dict[str, Any]]:
    """One spec of each data kind per held-out domain (6 × 8), in a seeded order per domain, so
    the specs dropped by `select` aren't always the same kinds. A kind the domain can't support
    (no status column) is skipped."""
    rng = random.Random(seed)
    held = [d for d in domains if d.slug in set(slice_slugs)]
    if {d.slug for d in held} != set(slice_slugs):
        raise SliceError(f"slice domains missing from the bank: {sorted(set(slice_slugs) - {d.slug for d in held})}")
    rows = []
    for d in sorted(held, key=lambda d: d.slug):
        kinds = list(DATA_KINDS)
        rng.shuffle(kinds)
        n = 0
        for kind in kinds:
            for _ in range(20):
                if (c := content_mod.build(kind, rng, d, rng.choice(d.entities))) is not None:
                    rows.append({"id": f"slice/{d.slug}/{n:02d}", "content": c, "seed": seed})
                    n += 1
                    break
    return rows


def select(content_rows: list[dict[str, Any]], verdicts: list[dict[str, Any]], per_domain: int = PER_DOMAIN,
           dropped: set[str] = frozenset()) -> list[dict[str, Any]]:
    """The first `per_domain` rows per domain whose verdict is ok and that the reviewer didn't
    drop, in content order. Raises when a domain has too few."""
    ok = {v["id"] for v in verdicts if v["verdict"] == "ok"} - set(dropped)
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for r in content_rows:
        if r["id"] in ok:
            by_domain.setdefault(r["content"]["domain"], []).append(r)
    domains = sorted({r["content"]["domain"] for r in content_rows})
    short = [d for d in domains if len(by_domain.get(d, [])) < per_domain]
    if short:
        raise SliceError(f"fewer than {per_domain} usable rows in: {short}")
    return [r for d in domains for r in by_domain[d][:per_domain]]


def export_rows(selected: list[dict[str, Any]], intents: dict[str, str], system_prompt: str,
                examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prompt rows in the dev export's shape: frozen system prompt, the 3 frozen examples, the
    intent. Only `id` and `messages` are read by `glimmer_trainer sample`."""
    from ..checkpoints import with_fewshot

    rows = [{"id": r["id"], "source": "explorer", "subset": True,
             "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": intents[r["id"]]}]}
            for r in selected]
    return with_fewshot(rows, examples)


def candidates(preds: list[dict[str, Any]], check: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Validator verdicts per case: `{id: [{sample, text, finish, tokens, pass, layers}]}`,
    sample-ordered. `check(pred)` runs the validator (layers 1-3) on one prediction row."""
    out: dict[str, list[dict[str, Any]]] = {}
    for p in sorted(preds, key=lambda p: (p["id"], p.get("sample", 0))):
        v = check(p)
        out.setdefault(p["id"], []).append({"sample": p.get("sample", 0), "text": p.get("text"),
                                             "finish": p.get("finish"), "tokens": p.get("tokens"), **v})
    return out


def express_of(sdk: ExpressSDK, text: str) -> str:
    """The Express inside a candidate's single `<a2ui>` block."""
    _, blocks = sdk.unwrap(text or "")
    if len(blocks) != 1:
        raise SliceError(f"expected one a2ui block, found {len(blocks)}")
    return blocks[0].text.strip()


def page(selected: list[dict[str, Any]], intents: dict[str, str], cands: dict[str, list[dict[str, Any]]],
         shots: Callable[[str, int], list[str]]) -> str:
    """A static review page: per case the intent, then each candidate's screenshots, Express and
    validator reasons. `shots(id, sample)` gives screenshot paths relative to the page."""
    parts = ["<!doctype html><meta charset=utf-8><title>Slice picks</title><style>"
             "body{font:14px system-ui;margin:16px;max-width:1400px}"
             ".case{border-top:3px solid #333;margin-top:32px}.cand{display:inline-block;vertical-align:top;"
             "width:330px;margin:8px;padding:8px;border:1px solid #ccc}.pass{border-color:#2a2}.fail{border-color:#c33;opacity:.75}"
             "img{width:100%;border:1px solid #eee}pre{white-space:pre-wrap;font-size:11px;max-height:240px;overflow:auto}"
             "</style><h1>Explorer slice: pick one candidate per case</h1>"
             "<p>Write picks in <code>data/explorer_dev/picks.yaml</code>: <code>&lt;id&gt;: {pick: k}</code>, "
             "<code>&lt;id&gt;: {express: \"...\", from: k}</code> for an edit, or "
             "<code>&lt;id&gt;: {drop: \"reason\"}</code> for a bad intent.</p>"]
    for r in selected:
        cid = r["id"]
        parts.append(f"<div class=case><h2>{html.escape(cid)} · {html.escape(r['content']['kind'])}</h2>"
                     f"<p><b>Intent:</b> {html.escape(intents[cid]).replace(chr(10), '<br>')}</p>")
        for c in cands.get(cid, []):
            reasons = [f"{name}: {x}" for name, layer in c["layers"].items() if layer["pass"] is False for x in layer["reasons"]]
            imgs = "".join(f'<img src="{html.escape(s)}" loading=lazy>' for s in shots(cid, c["sample"]))
            parts.append(f"<div class='cand {'pass' if c['pass'] else 'fail'}'><b>k={c['sample']}</b> "
                         f"{'PASS' if c['pass'] else 'FAIL'} · {c.get('tokens')} tok{imgs}"
                         f"<pre>{html.escape(c.get('text') or '')}</pre>"
                         f"<small>{html.escape('; '.join(reasons)[:600])}</small></div>")
        parts.append("</div>")
    return "\n".join(parts)


def load_picks(path: Path = PICKS) -> dict[str, dict[str, Any]]:
    d = yaml.safe_load(path.read_text()) if path.exists() else {}
    if not isinstance(d, dict):
        raise SliceError(f"{path}: expected a mapping of id -> pick")
    return d


def build_cases(selected: list[dict[str, Any]], intents: dict[str, str], cands: dict[str, list[dict[str, Any]]],
                picks: dict[str, dict[str, Any]], sdk: ExpressSDK, index: RefIndex, eval_fingerprints: set[str],
                mechanical: Callable[[str], dict[str, Any]], person: str, intent_tag: str) -> list[dict[str, Any]]:
    """Frozen eval cases from the picks. Refuses: a selected case without a pick; a pick of a
    candidate that fails layer 1 (`mechanical(text)`); a reference whose fingerprint is a gallery
    dev/test design (`eval_fingerprints`)."""
    from ..io import wrap

    out = []
    counters: dict[str, int] = {}
    for r in selected:
        cid, dom = r["id"], r["content"]["domain"]
        p = picks.get(cid)
        if p is None:
            raise SliceError(f"no pick for {cid}")
        if not isinstance(p, dict):
            raise SliceError(f"{cid}: a pick must be a mapping, such as {{pick: 3}}")
        if "drop" in p:
            raise SliceError(f"{cid} is dropped; run slice-select again so a replacement is selected")
        k = p.get("pick", p.get("from"))
        by_k = {c["sample"]: c for c in cands.get(cid, [])}
        if k not in by_k:
            raise SliceError(f"{cid}: candidate {k!r} doesn't exist")
        edited = "express" in p
        express = p["express"].strip() if edited else express_of(sdk, by_k[k]["text"])
        m = mechanical(wrap(express))
        if not m["pass"]:
            raise SliceError(f"{cid}: the reference fails layer 1: {'; '.join(m['reasons'])[:300]}")
        reference = sdk.compile_express(express)
        fp = fingerprint(reference, index)
        if fp in eval_fingerprints:
            raise SliceError(f"{cid}: reference has the fingerprint of a gallery dev/test design ({fp})")
        n = counters.get(dom, 0)
        counters[dom] = n + 1
        out.append({
            "id": f"explorer/{dom}/{n:04d}", "slice_id": cid, "family": dom, "intent": intents[cid],
            "reference": reference, "reference_express": sdk.decompile(reference), "fingerprint": fp,
            "expect_ui": True, "existing_surface": [], "split": "dev", "source": "explorer", "reviewed": True,
            "kind": r["content"]["kind"], "picked": k,
            "provenance": {"intent": intent_tag,
                           "reference": GLIMMER_TAG + (f" + person:{person}" if edited else "")},
        })
    return out


def require_complete(content_rows: list[dict[str, Any]], worded: list[dict[str, Any]], partial_exists: bool) -> None:
    """The slice/train near-duplicate guard must see every train intent: refuse while a wording
    run is unfinished (a partial file) or any content row lacks a wording."""
    if partial_exists:
        raise SliceError("a wording run left a partial file: finish `word` first")
    done = {w["id"] for w in worded if w.get("finish") != "error"}
    if todo := [r["id"] for r in content_rows if r["id"] not in done]:
        raise SliceError(f"{len(todo)} train content rows are not worded yet (e.g. {todo[0]}): finish `word` first")


def references_for_slice(gallery: list[str], cases: list[dict[str, Any]], train: list[str]) -> list[str]:
    """What slice intents are checked against: gallery eval intents and every train intent, but
    not the slice's own built cases (each would match itself)."""
    return gallery + [t for c in cases if c.get("source") != "explorer" for t in [c["intent"]]] + train


def dump(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
