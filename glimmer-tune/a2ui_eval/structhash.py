"""Dedup hash: the fingerprint shape plus enum props, optional-slot pattern, action kind and check kinds.

For near-duplicate layouts (validator layer 5); the fingerprint alone ignores enum props and actions.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .fingerprint import shape
from .refs import RefIndex
from .catalog_model import CatalogModel


def components_of(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for m in messages for c in (m.get("updateComponents") or {}).get("components", [])]


def _action_kind(action: dict[str, Any]) -> str:
    if "event" in action:
        return "event+ctx" if action["event"].get("context") else "event"
    return (action.get("functionCall") or {}).get("call", "?")


def _check_kinds(checks: list[dict[str, Any]]) -> str:
    def kind(c: dict[str, Any]) -> str:
        args = c.get("args") or {}
        inner = [kind(v) for v in args.get("values", []) if isinstance(v, dict)]
        if isinstance(args.get("value"), dict) and "call" in args["value"]:
            inner.append(kind(args["value"]))
        bounds = "".join(b for b in ("min", "max") if b in args)
        return c.get("call", "?") + bounds + (f"({','.join(inner)})" if inner else "")

    return ",".join(kind(ch.get("condition") or {}) for ch in checks)


def struct_hash(messages: list[dict[str, Any]], index: RefIndex, model: CatalogModel) -> str:
    parts = [shape(messages, index)]
    for c in components_of(messages):
        comp = model.components.get(c.get("component"))
        if comp is None:
            continue
        enums = ",".join(f"{s.name}={c[s.name]}" for s in comp.slots if s.kind == "static-enum" and isinstance(c.get(s.name), str))
        pattern = ",".join(s.name for s in comp.slots if s.name in comp.optional and s.name in c)
        action = _action_kind(c["action"]) if isinstance(c.get("action"), dict) else ""
        checks = _check_kinds(c.get("checks") or [])
        parts.append(f"{comp.name}[{enums}|{pattern}|{action}|{checks}]")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]
