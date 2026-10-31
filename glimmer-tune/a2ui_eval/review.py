"""Local review page for `data/intents_draft.jsonl`: `python -m a2ui_eval.review`.

Serves the built page (`review/dist`) and a tiny JSON API on 127.0.0.1. Each row's
reference Express is compiled with the v0.9 basic catalog, the one `@a2ui/lit`
renders and the one training uses (ADR 0008). Saving changes only `intent` and
`reviewed`; every other field is written back unchanged.
"""

from __future__ import annotations

import argparse
import json
import re
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


def make_handler(draft: Path, dist: Path):
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
                return self._json(HTTPStatus.OK, view_rows(draft))
            return super().do_GET()

        def do_POST(self):
            if self.path != "/api/rows":
                return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                row = update_row(draft, body.get("id"), body.get("intent"), body.get("reviewed"))
            except (ReviewError, json.JSONDecodeError, AttributeError) as e:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            return self._json(HTTPStatus.OK, {"id": row["id"], "intent": row["intent"], "reviewed": row["reviewed"]})

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--draft", type=Path, default=DRAFT)
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    if not (DIST / "index.html").exists():
        raise SystemExit(f"{DIST} is missing; run `npm install && npm run build` in review/")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.draft, DIST))
    print(f"reviewing {args.draft} at http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
