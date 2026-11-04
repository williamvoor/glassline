"""Layer 4, intent fidelity: the labelling pool, the labelling page and judge agreement (Phase 3).

A person labels candidates that passed layers 1-3 against a fixed rubric. A candidate passes
when no rubric item fails. The judge fills the same rubric and must agree with the person on at
least `AGREEMENT_BAR` of pass/fail (distill brief, Phase 3), with Cohen's kappa of at least
`KAPPA_BAR` (user, 2026-11-05), before it filters anything."""

from __future__ import annotations

import html
import json
import random
from pathlib import Path
from typing import Any, Callable

from .seeds import ROOT

DIR = ROOT / "data" / "judge"
POOL = DIR / "pool.jsonl"
LABELS = DIR / "labels.json"
PAGE = DIR / "label.html"
RUN = ROOT / "runs" / "judge" / "calibration.jsonl"
AGREEMENT_BAR = 0.85
KAPPA_BAR = 0.40  # user, 2026-11-05: raw agreement alone passes an always-pass judge

# The brief's five items, plus `layout` from the user's review standard (2026-11-03): layout as
# rendered matters, wording mismatches alone don't.
RUBRIC: list[tuple[str, str]] = [
    ("elements", "Every element the request asks for is there: each column, row, field, stat and section."),
    ("action", "The main action the request asks for is there (submit, save, filter, ...), or none was asked for."),
    ("grouping", "Related things are grouped sensibly and read in a sensible order."),
    ("labels", "Labels and headings fit the request. Judge the meaning, not exact spelling."),
    ("invented", "Nothing is invented: no data, fields, actions or sections the request doesn't ask for or imply."),
    ("layout", "The rendered layout looks right at both widths: aligned, nothing cramped, clipped or oddly empty."),
]
RUBRIC_KEYS = [k for k, _ in RUBRIC]


class JudgeError(Exception):
    pass


def key(cid: str, sample: int) -> str:
    return f"{cid}#{sample}"


def label_pool(cands: dict[str, list[dict[str, Any]]], n: int, seed: int) -> list[dict[str, Any]]:
    """`n` candidates that passed layers 1-3, spread over intents: a seeded round-robin that takes
    one unpicked candidate per intent per round, so no intent gets more than its share."""
    rng = random.Random(seed)
    queues = {cid: rng.sample([c for c in cs if c["pass"]], sum(c["pass"] for c in cs)) for cid, cs in sorted(cands.items())}
    order = [cid for cid in queues if queues[cid]]
    rng.shuffle(order)
    out: list[dict[str, Any]] = []
    while len(out) < n and any(queues[c] for c in order):
        for cid in order:
            if queues[cid] and len(out) < n:
                c = queues[cid].pop()
                out.append({"key": key(cid, c["sample"]), "id": cid, "sample": c["sample"], "text": c["text"]})
    if len(out) < n:
        raise JudgeError(f"only {len(out)} passing candidates, {n} asked for")
    return out


def page(pool: list[dict[str, Any]], intents: dict[str, str], shots: Callable[[str, int], list[str]]) -> str:
    """A static labelling page. Clicking a rubric item marks it failed; the verdict is pass when
    nothing failed. Progress stays in this browser; "Download labels.json" saves the file."""
    rubric = "".join(f"<li><b>{k}</b>: {html.escape(q)}</li>" for k, q in RUBRIC)
    parts = [
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Judge labels</title><style>"
        "body{font:14px system-ui;margin:16px;max-width:1500px}.c{border-top:3px solid #333;margin-top:28px;padding-top:8px}"
        ".shots{display:flex;gap:8px;align-items:flex-start}.shots img{border:1px solid #ddd}.s375{width:280px}.s1280{width:760px}"
        "pre{white-space:pre-wrap;font-size:11px;max-height:300px;overflow:auto;background:#f6f6f6;padding:6px}"
        "button.r{margin:2px;padding:4px 8px;border:1px solid #999;background:#fff;border-radius:4px;cursor:pointer}"
        "button.r.f{background:#c33;color:#fff;border-color:#c33}.v{font-weight:bold}.v.p{color:#2a2}.v.x{color:#c33}"
        "#bar{position:sticky;top:0;background:#fff;padding:8px 0;border-bottom:1px solid #ccc;z-index:1}"
        "textarea{width:100%;max-width:760px;height:2.6em}</style>",
        f"<div id=bar><b>Judge calibration</b> · <span id=n></span> labelled · "
        "<button onclick=dl()>Download labels.json</button> → save it as <code>data/judge/labels.json</code></div>",
        f"<p>Click every rubric item the candidate <b>fails</b>. It passes when nothing is marked. "
        f"Tick <i>looked at</i> for a candidate that passes, so it counts as labelled.</p><ul>{rubric}</ul>",
    ]
    for i, p in enumerate(pool):
        imgs = "".join(f'<img class="s{s.rsplit(".", 2)[-2]}" src="{html.escape(s)}" loading=lazy>' for s in shots(p["id"], p["sample"]))
        btns = "".join(f'<button class=r data-k="{k}" title="{html.escape(q)}">{k}</button>' for k, q in RUBRIC)
        parts.append(
            f'<div class=c data-key="{html.escape(p["key"])}"><h3>{i + 1}. {html.escape(p["key"])}</h3>'
            f"<p><b>Request:</b> {html.escape(intents[p['id']]).replace(chr(10), '<br>')}</p>"
            f"<div class=shots>{imgs}</div><p>Fails: {btns} <label><input type=checkbox class=seen> looked at</label> "
            f"· <span class=v></span></p><textarea placeholder='note (optional)'></textarea>"
            f"<details><summary>Express</summary><pre>{html.escape(p['text'] or '')}</pre></details></div>")
    parts.append("""<script>
const SK = 'judge-labels-v1';
let st = {}; try { st = JSON.parse(localStorage.getItem(SK) || '{}'); } catch (e) {}
function save() { try { localStorage.setItem(SK, JSON.stringify(st)); } catch (e) {} draw(); }
function draw() {
  let n = 0;
  document.querySelectorAll('.c').forEach(c => {
    const s = st[c.dataset.key] || {fails: [], seen: false, note: ''};
    c.querySelectorAll('button.r').forEach(b => b.classList.toggle('f', s.fails.includes(b.dataset.k)));
    c.querySelector('.seen').checked = s.seen;
    const done = s.seen || s.fails.length > 0, v = c.querySelector('.v');
    v.className = 'v ' + (done ? (s.fails.length ? 'x' : 'p') : '');
    v.textContent = done ? (s.fails.length ? 'FAIL' : 'PASS') : 'not labelled';
    if (c.querySelector('textarea') !== document.activeElement) c.querySelector('textarea').value = s.note;
    n += done;
  });
  document.getElementById('n').textContent = n + ' / ' + document.querySelectorAll('.c').length;
}
document.querySelectorAll('.c').forEach(c => {
  const k = c.dataset.key, get = () => (st[k] = st[k] || {fails: [], seen: false, note: ''});
  c.querySelectorAll('button.r').forEach(b => b.onclick = () => {
    const s = get(), i = s.fails.indexOf(b.dataset.k); i < 0 ? s.fails.push(b.dataset.k) : s.fails.splice(i, 1); s.seen = true; save(); });
  c.querySelector('.seen').onchange = e => { get().seen = e.target.checked; save(); };
  c.querySelector('textarea').oninput = e => { get().note = e.target.value; save(); };
});
function dl() {
  const labels = {};
  for (const [k, s] of Object.entries(st)) if (s.seen || s.fails.length) labels[k] = {fails: s.fails, note: s.note};
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify({labels}, null, 1)], {type: 'application/json'}));
  a.download = 'labels.json'; a.click();
}
draw();
</script>""")
    return "\n".join(parts)


def load_labels(path: Path, pool: list[dict[str, Any]]) -> dict[str, bool]:
    """`key -> passed` from the page's download. Refuses unknown keys, unknown rubric items and
    a pool candidate without a label."""
    d = json.loads(path.read_text())
    labels = d.get("labels") if isinstance(d, dict) else None
    if not isinstance(labels, dict):
        raise JudgeError(f"{path}: expected {{\"labels\": {{key: {{fails, note}}}}}}")
    keys = {p["key"] for p in pool}
    if extra := sorted(set(labels) - keys):
        raise JudgeError(f"labels for candidates outside the pool: {extra[:5]}")
    if missing := sorted(keys - set(labels)):
        raise JudgeError(f"{len(missing)} pool candidates have no label, e.g. {missing[:3]}")
    out = {}
    for k, v in labels.items():
        if bad := sorted(set(v.get("fails", [])) - set(RUBRIC_KEYS)):
            raise JudgeError(f"{k}: unknown rubric items {bad}")
        out[k] = not v.get("fails")
    return out


def agreement(person: dict[str, bool], judge: dict[str, bool]) -> dict[str, Any]:
    """Pass/fail agreement on the keys both labelled, with Cohen's kappa and the confusion counts.
    Raw agreement alone flatters a judge that always says pass when most candidates pass."""
    keys = sorted(set(person) & set(judge))
    if not keys:
        raise JudgeError("no candidates labelled by both")
    n = len(keys)
    tp = sum(person[k] and judge[k] for k in keys)
    tn = sum(not person[k] and not judge[k] for k in keys)
    fp = sum(not person[k] and judge[k] for k in keys)  # judge passes what the person failed
    fn = sum(person[k] and not judge[k] for k in keys)
    po = (tp + tn) / n
    pp, pj = (tp + fn) / n, (tp + fp) / n
    pe = pp * pj + (1 - pp) * (1 - pj)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 0.0
    return {"n": n, "agreement": round(po, 3), "kappa": round(kappa, 3), "meets_bar": po >= AGREEMENT_BAR and kappa >= KAPPA_BAR,
            "both_pass": tp, "both_fail": tn, "judge_passes_person_fail": fp, "judge_fails_person_pass": fn,
            "person_pass_rate": round(pp, 3)}


# ---------------------------------------------------------------- the judge call

CACHE_KEY = "glimmer-tune-judge"
REPEATS = 3
MAX_TOKENS = 2048  # reasoning counts against it
WIDTHS = (375, 1280)
IMAGE_TOKENS = 1500  # per screenshot, for the dry-run estimate only. UNVERIFIED: replace with measured usage

# The stable prefix: identical for every call, so Baseten can cache it. Per-candidate text goes
# only in the user message.
SYSTEM = (
    "You review a user interface generated from a request. You get the request, the A2UI Express "
    "source, and screenshots of the rendered UI at a phone width (375 px) and a desktop width "
    "(1280 px). Judge the UI as rendered; use the Express only to check what the screenshots can't "
    "show. Fill this rubric:\n"
    + "".join(f"- {k}: {q}\n" for k, q in RUBRIC)
    + "Notes:\n"
    "- Forms render with their validation messages (\"X is required\") already showing on empty "
    "fields. That is the renderer, not the candidate: never fail an item for it.\n"
    "- Mismatched wording alone (a title, tab or button worded differently from the request) is not "
    "a fail if the meaning fits.\n"
    "- Placeholder or sample values are fine where the request implies data; they are not invented.\n"
    "Reply with only one JSON object, no prose and no code fence, with exactly these keys: "
    + ", ".join(RUBRIC_KEYS)
    + ". Each value is {\"why\": \"<one short sentence>\", \"ok\": true|false}."
)


def _data_uri(path: Path) -> str:
    import base64

    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def judge_messages(intent: str, express: str, shots: list[Path]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": f"Request:\n{intent}\n\nExpress:\n{express}"}]
    content += [{"type": "image_url", "image_url": {"url": _data_uri(p)}} for p in shots]
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]


def parse_reply(text: str) -> list[str]:
    """The failed rubric items from a reply. Raises JudgeError on anything but one JSON object
    with every rubric key and a boolean `ok` on each, so a malformed reply never counts as a pass."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").removeprefix("json").strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < start:
        raise JudgeError("no JSON object in the reply")
    try:
        d = json.loads(t[start:end + 1])
    except json.JSONDecodeError as e:
        raise JudgeError(f"bad JSON: {e}") from e
    if not isinstance(d, dict) or set(d) != set(RUBRIC_KEYS):
        raise JudgeError(f"rubric keys {sorted(d) if isinstance(d, dict) else type(d).__name__}")
    if bad := [k for k in RUBRIC_KEYS if not isinstance(d[k], dict) or not isinstance(d[k].get("ok"), bool)]:
        raise JudgeError(f"no boolean ok on {bad}")
    return [k for k in RUBRIC_KEYS if not d[k]["ok"]]


def shot_paths(shots_dir: Path, cid: str, sample: int) -> list[Path]:
    paths = [shots_dir / f"{cid.replace('/', '_')}_{sample}.{w}.png" for w in WIDTHS]
    if missing := [p.name for p in paths if not p.exists()]:
        raise JudgeError(f"missing screenshots {missing}")
    return paths


def estimate(pool: list[dict[str, Any]], intents: dict[str, str], repeats: int = REPEATS,
             out_tokens: int = 600) -> dict[str, float]:
    from .teacher import CHARS_PER_TOKEN, PRICE_IN, PRICE_OUT

    chars = sum(len(SYSTEM) + len(intents[p["id"]]) + len(p["text"] or "") for p in pool)
    tin = repeats * (chars / CHARS_PER_TOKEN + len(pool) * len(WIDTHS) * IMAGE_TOKENS)
    tout = repeats * len(pool) * out_tokens
    return {"calls": repeats * len(pool), "input_tokens": round(tin), "output_tokens": tout,
            "usd": round(tin * PRICE_IN / 1e6 + tout * PRICE_OUT / 1e6, 3)}


async def run_judge(pool: list[dict[str, Any]], intents: dict[str, str], shots_dir: Path, out: Path,
                    complete: Callable[[dict[str, Any]], Any], repeats: int = REPEATS, workers: int = 8,
                    temperature: float = 0.0) -> dict[str, Any]:
    """Judges every (candidate, repeat) not already in `out`; error and bad-output rows are
    retried. Resumable and written in pool order. Reasoning is `low` (glimmer-tune's rule)."""
    import asyncio
    import time

    from .io import read_jsonl
    from .teacher import MODEL, ROOT as T_ROOT, actual_usd

    done = {(r["key"], r["repeat"]): r for r in (read_jsonl(out) if out.exists() else []) if r["finish"] == "ok"}
    todo = [(p, i) for p in pool for i in range(repeats) if (p["key"], i) not in done]
    sem = asyncio.Semaphore(workers)

    async def one(p: dict[str, Any], i: int) -> dict[str, Any]:
        row = {"key": p["key"], "repeat": i, "model": MODEL}
        async with sem:
            t0 = time.monotonic()
            try:
                req = {"model": MODEL, "messages": judge_messages(intents[p["id"]], p["text"] or "", shot_paths(shots_dir, p["id"], p["sample"])),
                       "reasoning_effort": "low", "cache_key": CACHE_KEY, "temperature": temperature,
                       "max_tokens": MAX_TOKENS, "allowed_openai_params": ["reasoning_effort", "prompt_cache_key"],
                       "env_file": str(T_ROOT.parent / ".env")}
                reply = await complete(req)
            except Exception as e:  # noqa: BLE001 - one failed call must not stop the run
                return {**row, "finish": "error", "error": str(e)[-500:], "seconds": round(time.monotonic() - t0, 2)}
        row |= {k: reply.get(k) for k in ("prompt_tokens", "cached_tokens", "completion_tokens", "reasoning_tokens")}
        row |= {"text": reply.get("content"), "seconds": round(time.monotonic() - t0, 2)}
        if reply.get("finish_reason") == "length":
            return {**row, "finish": "length"}
        try:
            return {**row, "finish": "ok", "fails": parse_reply(reply.get("content") or "")}
        except JudgeError as e:
            return {**row, "finish": "bad_output", "error": str(e)}

    new = await asyncio.gather(*(one(p, i) for p, i in todo))
    order = {p["key"]: n for n, p in enumerate(pool)}
    merged = {**done, **{(r["key"], r["repeat"]): r for r in new}}
    rows = sorted(merged.values(), key=lambda r: (order.get(r["key"], len(order)), r["repeat"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return {"calls": len(todo), "ok": sum(r["finish"] == "ok" for r in rows),
            "not_ok": {f: sum(r["finish"] == f for r in rows) for f in ("error", "length", "bad_output")},
            "usd_upper_bound": round(actual_usd(new), 4)}


def verdicts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per candidate: the majority pass/fail over its ok repeats (a tie fails, the strict side),
    the judge score (mean share of rubric items passed, for selection), and whether the repeats
    disagreed. A candidate with no ok repeat gets no verdict and is listed as `unjudged`."""
    by: dict[str, list[list[str]]] = {}
    keys = []
    for r in rows:
        keys.append(r["key"])
        if r["finish"] == "ok":
            by.setdefault(r["key"], []).append(r["fails"])
    passed, score, flipped = {}, {}, []
    for k, runs in by.items():
        votes = [not f for f in runs]
        passed[k] = sum(votes) * 2 > len(votes)
        score[k] = round(sum(1 - len(f) / len(RUBRIC_KEYS) for f in runs) / len(runs), 3)
        if len(set(votes)) > 1:
            flipped.append(k)
    return {"passed": passed, "score": score, "flipped": sorted(flipped),
            "unjudged": sorted(set(keys) - set(by))}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import asyncio
    import shutil
    import sys

    from .io import read_jsonl, write_jsonl

    p = argparse.ArgumentParser(prog="python -m a2ui_eval.judge")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("pool", help="draw the labelling pool and write the labelling page")
    b.add_argument("--cands", type=Path, required=True, help="validator output (layers 1-3)")
    b.add_argument("--shots", type=Path, required=True, help="the candidates' screenshots")
    b.add_argument("--n", type=int, default=50)
    b.add_argument("--seed", type=int, default=20261105)
    r = sub.add_parser("run", help="judge the pool (paid: calls Spark Open on Baseten)")
    r.add_argument("--out", type=Path, default=RUN)
    r.add_argument("--repeats", type=int, default=REPEATS)
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--dry-run", action="store_true", help="print the cost estimate and stop")
    a = sub.add_parser("agree", help="agreement between labels.json and the judge run")
    a.add_argument("--run", type=Path, default=RUN, help="the judge run file")
    args = p.parse_args(argv)
    try:
        if args.cmd == "pool":
            pool = label_pool(json.loads(args.cands.read_text()), args.n, args.seed)
            intents = {r["id"]: r["intent"] for r in read_jsonl(ROOT / "data" / "intents" / "tune.jsonl")}
            if missing := sorted({q["id"] for q in pool} - set(intents)):
                raise JudgeError(f"pool candidates outside the tune split: {missing[:3]}")
            (DIR / "shots").mkdir(parents=True, exist_ok=True)

            def shots(cid: str, k: int) -> list[str]:
                out = []
                for w in (375, 1280):
                    name = f"{cid.replace('/', '_')}_{k}.{w}.png"
                    if (args.shots / name).exists():
                        shutil.copy(args.shots / name, DIR / "shots" / name)
                        out.append(f"shots/{name}")
                return out

            write_jsonl(POOL, pool)
            PAGE.write_text(page(pool, intents, shots))
            print(json.dumps({"pool": len(pool), "intents": len({q["id"] for q in pool}), "page": str(PAGE)}))
        elif args.cmd == "run":
            pool = read_jsonl(POOL)
            intents = {r["id"]: r["intent"] for r in read_jsonl(ROOT / "data" / "intents" / "tune.jsonl")}
            for q in pool:
                shot_paths(DIR / "shots", q["id"], q["sample"])  # refuse before spending anything
            if args.dry_run:
                print(json.dumps(estimate(pool, intents, args.repeats)))
                return 0
            from .teacher import litellm_complete

            print(json.dumps(asyncio.run(run_judge(pool, intents, DIR / "shots", args.out, litellm_complete,
                                                   args.repeats, args.workers))))
        else:
            pool = read_jsonl(POOL)
            v = verdicts(read_jsonl(args.run))
            report = agreement(load_labels(LABELS, pool), v["passed"])
            report |= {"flipped": len(v["flipped"]), "unjudged": v["unjudged"]}
            print(json.dumps(report))
    except JudgeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
