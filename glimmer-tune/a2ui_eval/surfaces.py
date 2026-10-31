"""Plain-dict view of A2UI v1.0 messages, grouped by surface."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

CREATE, UPDATE, DATA, DELETE = "createSurface", "updateComponents", "updateDataModel", "deleteSurface"
LIFECYCLE = (CREATE, UPDATE, DATA, DELETE)


@dataclass
class SurfaceTurn:
    """What one message list does to one surface."""

    surface_id: str
    ops: list[str] = field(default_factory=list)  # message kinds, in order, deduplicated
    components: dict[str, dict[str, Any]] = field(default_factory=dict)
    data_ops: list[tuple[str, Any]] = field(default_factory=list)  # (json pointer, value)

    @property
    def op(self) -> str:
        """The turn's main effect: delete beats create beats update beats data."""
        for kind in (DELETE, CREATE, UPDATE, DATA):
            if kind in self.ops:
                return kind
        return "none"


def message_kind(message: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    for kind in LIFECYCLE:
        if kind in message:
            return kind, message[kind]
    return None, {}


def collect(messages: Iterable[dict[str, Any]]) -> dict[str, SurfaceTurn]:
    turns: dict[str, SurfaceTurn] = {}
    for message in messages:
        kind, body = message_kind(message)
        if kind is None:
            continue
        sid = body.get("surfaceId", "")
        turn = turns.setdefault(sid, SurfaceTurn(sid))
        if kind not in turn.ops:
            turn.ops.append(kind)
        for comp in body.get("components") or []:
            if isinstance(comp, dict) and isinstance(comp.get("id"), str):
                turn.components[comp["id"]] = comp
        if kind == CREATE and "dataModel" in body:
            turn.data_ops.append(("/", body["dataModel"]))
        if kind == DATA:
            turn.data_ops.append((body.get("path", "/"), body.get("value")))
    return turns


@dataclass
class SurfaceState:
    components: dict[str, dict[str, Any]] = field(default_factory=dict)
    data: Any = field(default_factory=dict)


def fold(messages: Iterable[dict[str, Any]], into: dict[str, SurfaceState] | None = None) -> dict[str, SurfaceState]:
    """Applies messages in order, as a renderer would, without validating them."""
    states = copy.deepcopy(into) if into else {}
    for message in messages:
        kind, body = message_kind(message)
        if kind is None:
            continue
        sid = body.get("surfaceId", "")
        if kind == DELETE:
            states.pop(sid, None)
            continue
        if kind == CREATE:
            states[sid] = SurfaceState()
        state = states.setdefault(sid, SurfaceState())
        for comp in body.get("components") or []:
            if isinstance(comp, dict) and isinstance(comp.get("id"), str):
                state.components[comp["id"]] = comp
        if kind == CREATE and "dataModel" in body:
            state.data = copy.deepcopy(body["dataModel"])
        if kind == DATA:
            state.data = set_pointer(state.data, body.get("path", "/"), copy.deepcopy(body.get("value")))
    return states


def pointer_segments(pointer: str) -> list[str]:
    if pointer in ("", "/"):
        return []
    return [s.replace("~1", "/").replace("~0", "~") for s in pointer.strip("/").split("/")]


def set_pointer(root: Any, pointer: str, value: Any) -> Any:
    segments = pointer_segments(pointer)
    if not segments:
        return value
    if not isinstance(root, (dict, list)):
        root = {}
    node = root
    for i, seg in enumerate(segments):
        last = i == len(segments) - 1
        if isinstance(node, list):
            if not seg.isdigit():
                return root
            idx = int(seg)
            while len(node) <= idx:
                node.append(None)
            if last:
                node[idx] = value
            else:
                if not isinstance(node[idx], (dict, list)):
                    node[idx] = [] if segments[i + 1].isdigit() else {}
                node = node[idx]
        else:
            if last:
                node[seg] = value
            else:
                if not isinstance(node.get(seg), (dict, list)):
                    node[seg] = [] if segments[i + 1].isdigit() else {}
                node = node[seg]
    return root


def has_path(root: Any, segments: list[str]) -> bool:
    """Whether the path exists. A `*` segment matches any list item or map value."""
    if not segments:
        return True
    seg, rest = segments[0], segments[1:]
    if seg == "*":
        children = root if isinstance(root, list) else list(root.values()) if isinstance(root, dict) else []
        return any(has_path(c, rest) for c in children)
    if isinstance(root, dict):
        return seg in root and has_path(root[seg], rest)
    if isinstance(root, list) and seg.isdigit():
        return int(seg) < len(root) and has_path(root[int(seg)], rest)
    return False
