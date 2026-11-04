"""Layered validator for candidate answers (distill Approach v2, ADR 0033).

Every candidate goes through every layer that can run; each records pass/fail and reasons.
1. mechanical: an `<a2ui>` block, not truncated, compiles, SDK and schema valid, a sound tree,
   bindings resolve against the candidate's own data model, nothing outside the catalog, no
   `required` check on a boolean input.
2. render: `@a2ui/lit` in headless Chromium with no errors, no empty containers and no overflow
   at a phone and a desktop width (`render.py`). Skipped when there is no renderer or layer 1 fails.
3. lint: the S8 rules (`lint.py`).
Layers 4 (judge) and 5 (diversity) come with Phase 3.

None of this reads a reference: a candidate is judged on its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .catalog_check import CatalogCheck
from .catalog_model import CatalogModel
from .lint import lint_surface
from .refs import RefIndex
from .render import Renderer
from .score import close_stopped_block, score_response
from .sdk import CompileError, ExpressSDK
from .structure import required_on_boolean_input


def _layer(passed: bool | None, reasons: list[str]) -> dict[str, Any]:
    return {"pass": passed, "reasons": reasons}


class Validator:
    def __init__(self, sdk: ExpressSDK, renderer: Renderer | None = None):
        self.sdk, self.renderer = sdk, renderer
        self.index = RefIndex(sdk.catalog_json)
        self.catalog = CatalogCheck(sdk)
        self.model = CatalogModel(sdk)

    def _messages(self, text: str, finish: str | None) -> list[dict[str, Any]]:
        _, blocks = self.sdk.unwrap(close_stopped_block(text, finish, self.sdk))
        out: list[dict[str, Any]] = []
        for b in blocks:
            out.extend(self.sdk.compile(b))
        return out

    def mechanical(self, text: str | None, finish: str | None, existing: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        case = {"id": "candidate", "expect_ui": True, "reference": [], "existing_surface": existing or []}
        r = score_response(self.sdk, self.index, case, text, finish)
        reasons: list[str] = []
        if text is None:
            return _layer(False, ["no response"])
        if not r["extracted"]:
            return _layer(False, ["no a2ui block"])
        if r["truncated"]:
            reasons.append("truncated")
        if not r["compiles"]:
            reasons += [f"does not compile: {e['kind']}: {e['message']}" for e in r["compile_errors"]]
            return _layer(False, reasons)
        if not r["sdk_valid"]:
            reasons.append(f"sdk: {r['sdk_error']}")
        reasons += [f"schema: {e}" for e in r["schema_errors"]]
        for sid, s in r["structure"].items():
            reasons.append(f"structure {sid}: " + ", ".join(k for k, v in s.items() if v))
        reasons += [f"unresolved binding {sid}/{cid}: {path}" for sid, cid, path in r["unresolved_bindings"]]
        reasons += [f"outside catalog: {f.kind}: {f.detail}" for f in self.catalog.response(text)]
        comps = [c for m in self._messages(text, finish) for c in m.get("updateComponents", {}).get("components", [])]
        if ids := required_on_boolean_input(comps, self.catalog.boolean_inputs):
            reasons.append(f"required on a boolean input: {', '.join(ids)}")
        return _layer(not reasons, reasons)

    def check(
        self,
        text: str | None,
        finish: str | None,
        existing: list[dict[str, Any]] | None = None,
        screenshot: Path | None = None,
    ) -> dict[str, Any]:
        layers = {"mechanical": self.mechanical(text, finish, existing)}
        messages: list[dict[str, Any]] = []
        if layers["mechanical"]["pass"]:
            try:
                messages = (existing or []) + self._messages(text or "", finish)
            except CompileError as e:  # mechanical passed, so this shouldn't happen
                layers["mechanical"] = _layer(False, [f"does not compile: {e.kind}: {e.message}"])
        if not layers["mechanical"]["pass"]:
            layers["render"] = _layer(None, ["not run"])
            layers["lint"] = _layer(None, ["not run"])
        else:
            if self.renderer is None:
                layers["render"] = _layer(None, ["not run"])
            else:
                reasons = self.renderer.check(messages, screenshot).reasons()
                layers["render"] = _layer(not reasons, reasons)
            failed = lint_surface(messages, self.model)
            layers["lint"] = _layer(not failed, failed)
        # A layer that didn't run doesn't block; one that ran and failed does.
        ok = layers["mechanical"]["pass"] and all(l["pass"] is not False for l in layers.values())
        return {"pass": bool(ok), "layers": layers}


def pick(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The candidate to keep for one intent until the judge exists: among those that passed,
    the fewest output tokens, then the earliest sample. None when nothing passed."""
    passed = [c for c in candidates if c["pass"]]
    return min(passed, key=lambda c: (c["tokens"], c["sample"])) if passed else None
