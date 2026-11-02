"""Local review page for `data/intents_draft.jsonl`: `python -m a2ui_eval.review`.

Serves the built page (`review/dist`) and a tiny JSON API on 127.0.0.1. Each row's
reference Express is compiled with the v0.9 basic catalog, the one `@a2ui/lit`
renders and the one training uses (ADR 0008). Saving changes only `intent` and
`reviewed`; every other field is written back unchanged.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from dataclasses import dataclass
from functools import cache
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .generate import write_jsonl
from .sdk import ExpressSDK
from .seeds import ROOT

DRAFT = ROOT / "data" / "intents_draft.jsonl"
CATALOG = ROOT / "catalogs" / "basic_v0_9.json"
DIST = ROOT / "review" / "dist"
VERDICTS = ROOT / "data" / "synth" / "review.jsonl"
VERDICT_KINDS = ("approve", "reject")

# Component props whose literal string values a reader would expect in the intent.
_TEXT_PROPS = ("text", "label", "name", "title", "placeholder")
_FORMAT_REF = re.compile(r"\$\{(/[^}]*)\}")


class ReviewError(Exception):
    pass


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    write_jsonl(path, rows)  # atomic, same key order as the CLI's writes


def update_row(path: Path, row_id: str, intent: str, reviewed: bool) -> dict[str, Any]:
    if not isinstance(intent, str) or not intent.strip():
        raise ReviewError("intent must be a non-empty string")
    if not isinstance(reviewed, bool):
        raise ReviewError("reviewed must be true or false")
    rows = read_rows(path)
    for row in rows:
        if row["id"] == row_id:
            row["intent"] = intent
            row["reviewed"] = reviewed
            write_rows(path, rows)
            return row
    raise ReviewError(f"unknown row id {row_id!r}")


def _data_model(reference: list[dict[str, Any]]) -> dict[str, Any]:
    model: dict[str, Any] = {}
    for m in reference:
        create = m.get("createSurface") or {}
        if isinstance(create.get("dataModel"), dict):
            model.update(create["dataModel"])
        update = m.get("updateDataModel") or {}
        if update.get("path") in (None, "/") and isinstance(update.get("value"), dict):
            model.update(update["value"])
    return model


def _lookup(model: Any, pointer: str) -> Any:
    for part in pointer.strip("/").split("/"):
        if isinstance(model, dict) and part in model:
            model = model[part]
        elif isinstance(model, list) and part.isdigit() and int(part) < len(model):
            model = model[int(part)]
        else:
            return None
    return model


def _scalars(value: Any, out: list[str]) -> None:
    if isinstance(value, dict):
        for v in value.values():
            _scalars(v, out)
    elif isinstance(value, list):
        for v in value:
            _scalars(v, out)
    elif isinstance(value, str | int | float) and not isinstance(value, bool):
        out.append(str(value))


def expected_values(reference: list[dict[str, Any]]) -> list[str]:
    """Values a complete intent should mention: data-model scalars, literal text props
    (Markdown heading marks stripped), `formatString` text with data filled in, and event names.

    A hint for the reviewer, not a rule: "$7,310" mentions 7310 but won't match it.
    """
    model = _data_model(reference)
    out: list[str] = []
    _scalars(model, out)
    for m in reference:
        components = (m.get("createSurface") or {}).get("components") or (m.get("updateComponents") or {}).get("components") or []
        for c in components:
            for prop in _TEXT_PROPS:
                v = c.get(prop)
                if isinstance(v, str):
                    out.append(v.lstrip("#").strip())
                elif isinstance(v, dict) and (v.get("call") or v.get("@call")) == "formatString":  # v0.9 / v1
                    template = (v.get("args") or {}).get("value")
                    if isinstance(template, str):
                        out.append(_FORMAT_REF.sub(lambda r: str(_lookup(model, r.group(1)) or r.group(0)), template))
            event = ((c.get("action") or {}).get("event") or {}).get("name")
            if isinstance(event, str):
                out.append(event)
    seen: set[str] = set()
    return [v for v in out if v and not (v in seen or seen.add(v))]


@cache
def v09_sdk() -> ExpressSDK:
    return ExpressSDK(CATALOG)


def view_rows(path: Path) -> list[dict[str, Any]]:
    sdk = v09_sdk()
    out = []
    for row in read_rows(path):
        view = {k: row[k] for k in ("id", "family", "split", "intent", "reference_express", "fingerprint")}
        view["reviewed"] = bool(row.get("reviewed"))
        view["expected"] = expected_values(row["reference"])
        try:
            view["v09"] = sdk.compile_express(row["reference_express"])
        except Exception as e:  # shown in the page; the row can still be reviewed
            view["v09"], view["render_error"] = [], str(e)
        out.append(view)
    return out


@dataclass(frozen=True)
class SyntheticReview:
    cases: Path
    sample: int = 50
    seed: int = 0
    verdicts: Path = VERDICTS


def sample_rows(rows: list[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    """`n` rows chosen without replacement by `seed`, in id order. The same rows give the same sample."""
    ordered = sorted(rows, key=lambda r: r["id"])
    return sorted(random.Random(seed).sample(ordered, min(n, len(ordered))), key=lambda r: r["id"])


def read_verdicts(path: Path) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in read_rows(path)} if path.exists() else {}


def record_verdict(review: SyntheticReview, row_id: Any, verdict: Any, reason: Any) -> dict[str, Any]:
    """Upserts `{id, verdict, reason}` (the latest verdict per id wins; first-seen order is kept).
    Refuses an id outside the sample, an unknown verdict, and a reject without a reason."""
    if verdict not in VERDICT_KINDS:
        raise ReviewError(f"verdict must be one of {VERDICT_KINDS}")
    if row_id not in {r["id"] for r in sample_rows(read_rows(review.cases), review.sample, review.seed)}:
        raise ReviewError(f"unknown row id {row_id!r}")
    reason = reason.strip() if isinstance(reason, str) else ""
    if verdict == "reject" and not reason:
        raise ReviewError("a reject needs a reason")
    record = {"id": row_id, "verdict": verdict, "reason": reason}
    records = read_verdicts(review.verdicts)
    records[row_id] = record  # keeps the dict position of an earlier verdict
    write_rows(review.verdicts, list(records.values()))
    return record


@cache
def _lint_context():
    from .synth.catalog_model import CatalogModel
    from .synth.skeleton import load_skeletons

    model = CatalogModel(v09_sdk())
    return model, {k: s.tags for k, s in load_skeletons(model).items()}


def synthetic_views(review: SyntheticReview) -> list[dict[str, Any]]:
    from .synth.lint import lint_surface

    sdk, (model, tags) = v09_sdk(), _lint_context()
    verdicts = read_verdicts(review.verdicts)
    out = []
    for row in sample_rows(read_rows(review.cases), review.sample, review.seed):
        keys = ("id", "family", "split", "intent", "reference_express", "fingerprint", "skeleton", "domain", "topic", "detail")
        view: dict[str, Any] = {k: row.get(k) for k in keys}
        verdict = verdicts.get(row["id"])
        view.update(
            synthetic=True,
            reviewed=verdict is not None,
            verdict=verdict["verdict"] if verdict else None,
            reason=verdict["reason"] if verdict else "",
            lint=lint_surface(row["reference"], model, tags.get(row.get("skeleton"), ())),
            expected=expected_values(row["reference"]),
        )
        try:
            view["v09"] = sdk.compile_express(row["reference_express"])
        except Exception as e:
            view["v09"], view["render_error"] = [], str(e)
        out.append(view)
    return out


PREDICTION_VERDICTS = ("ok", "model", "reference", "scorer")  # what a low score is due to


@dataclass(frozen=True)
class PredictionReview:
    """A checkpoint's answers on gallery dev, worst first by name-blind slot F1 (`checkpoints score` output)."""

    predictions: Path
    scores: Path
    verdicts: Path


def _gallery_scored(review: PredictionReview) -> list[dict[str, Any]]:
    rows = [r for r in json.loads(review.scores.read_text(encoding="utf-8"))["rows"] if r["source"] == "gallery"]
    return sorted(rows, key=lambda r: (r["slot_f1_blind"], r["slot_f1"], r["id"]))


def record_prediction_verdict(review: PredictionReview, row_id: Any, verdict: Any, note: Any) -> dict[str, Any]:
    """Upserts `{id, verdict, note}`; the latest verdict per id wins. Refuses unknown ids and verdicts."""
    if verdict not in PREDICTION_VERDICTS:
        raise ReviewError(f"verdict must be one of {PREDICTION_VERDICTS}")
    if row_id not in {r["id"] for r in _gallery_scored(review)}:
        raise ReviewError(f"unknown row id {row_id!r}")
    record = {"id": row_id, "verdict": verdict, "note": note.strip() if isinstance(note, str) else ""}
    records = read_verdicts(review.verdicts)
    records[row_id] = record
    write_rows(review.verdicts, list(records.values()))
    return record


def prediction_views(review: PredictionReview) -> list[dict[str, Any]]:
    from .checkpoints import dev_cases
    from .score import close_stopped_block

    sdk = v09_sdk()
    cases = {c["id"]: c for c in dev_cases()}
    preds = {p["id"]: p for p in read_rows(review.predictions)}
    verdicts = read_verdicts(review.verdicts)
    out = []
    for score in _gallery_scored(review):
        case, pred, verdict = cases[score["id"]], preds[score["id"]], verdicts.get(score["id"])
        view: dict[str, Any] = {k: case.get(k) for k in ("id", "family", "split", "intent", "reference_express")}
        view.update(
            prediction_mode=True,
            reviewed=verdict is not None,
            verdict=verdict["verdict"] if verdict else None,
            note=verdict["note"] if verdict else "",
            expected=expected_values(case["reference"]),
            prediction_text=pred.get("text"),
            finish=pred["finish"],
            scores={k: score.get(k) for k in ("slot_f1_blind", "slot_f1", "compiles")},
            outside_catalog_kinds=score.get("outside_catalog_kinds", []),
        )
        try:
            view["v09"] = sdk.compile_express(case["reference_express"])
        except Exception as e:
            view["v09"], view["render_error"] = [], str(e)
        view["prediction_v09"] = []
        if pred.get("text") is None:
            view["prediction_error"] = f"no response ({pred['finish']})"
        else:
            try:
                _, blocks = sdk.unwrap(close_stopped_block(pred["text"], pred["finish"], sdk))
                view["prediction_v09"] = [m for b in blocks for m in sdk.compile(b)]
                if not blocks:
                    view["prediction_error"] = "no <a2ui> block"
            except Exception as e:
                view["prediction_v09"], view["prediction_error"] = [], str(e)
        out.append(view)
    return out


def make_handler(draft: Path, dist: Path, synthetic: SyntheticReview | None = None, predictions: PredictionReview | None = None):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(dist), **kwargs)

        def _json(self, status: int, body: Any) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/api/rows":
                if predictions:
                    return self._json(HTTPStatus.OK, prediction_views(predictions))
                return self._json(HTTPStatus.OK, synthetic_views(synthetic) if synthetic else view_rows(draft))
            return super().do_GET()

        def do_POST(self):
            if self.path != ("/api/verdict" if synthetic or predictions else "/api/rows"):
                return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                if predictions:
                    return self._json(HTTPStatus.OK, record_prediction_verdict(predictions, body.get("id"), body.get("verdict"), body.get("note")))
                if synthetic:
                    return self._json(HTTPStatus.OK, record_verdict(synthetic, body.get("id"), body.get("verdict"), body.get("reason")))
                row = update_row(draft, body.get("id"), body.get("intent"), body.get("reviewed"))
            except (ReviewError, json.JSONDecodeError, AttributeError) as e:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return self._json(HTTPStatus.OK, {"id": row["id"], "intent": row["intent"], "reviewed": row["reviewed"]})

    return Handler


_BUILD_INPUTS = ("src", "index.html", "package.json", "package-lock.json", "vite.config.ts", "tsconfig.json")


def build_problem(client: Path) -> str | None:
    """Why `client/dist` can't be served, or None. `dist` is git-ignored and never rebuilt
    automatically, so a stale bundle silently talks to an API the server no longer has."""
    built = client / "dist" / "index.html"
    fix = f"run `npm install && npm run build` in {client}"
    if not built.exists():
        return f"{built.parent} is missing; {fix}"
    inputs = [p for name in _BUILD_INPUTS for p in ((client / name).rglob("*") if (client / name).is_dir() else [client / name])]
    newer = sorted(p.relative_to(client).as_posix() for p in inputs if p.is_file() and p.stat().st_mtime > built.stat().st_mtime)
    return f"{built.parent} is older than {', '.join(newer)}; {fix}" if newer else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--draft", type=Path, default=DRAFT)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--synthetic", type=Path, help="review a sample of synthetic rows from this cases.jsonl instead of the draft")
    ap.add_argument("--sample", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--verdicts", type=Path, default=VERDICTS)
    ap.add_argument("--predictions", type=Path, help="review a checkpoint's gallery dev answers, worst first (needs --scores)")
    ap.add_argument("--scores", type=Path, help="the `checkpoints score` output for --predictions")
    args = ap.parse_args()
    review = SyntheticReview(args.synthetic, args.sample, args.seed, args.verdicts) if args.synthetic else None
    preds = None
    if args.predictions:
        if not args.scores:
            raise SystemExit("--predictions needs --scores")
        verdicts = args.verdicts if args.verdicts != VERDICTS else args.scores.with_suffix(".review.jsonl")
        preds = PredictionReview(args.predictions, args.scores, verdicts)
    if problem := build_problem(DIST.parent):
        raise SystemExit(problem)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.draft, DIST, review, preds))
    print(f"reviewing {args.predictions or args.synthetic or args.draft} at http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
