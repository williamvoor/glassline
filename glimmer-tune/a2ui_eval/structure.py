"""Graph checks on a surface's components: references, reachability, cycles, bindings."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .refs import RefIndex
from .surfaces import has_path, pointer_segments

ROOT = "root"


@dataclass
class StructureReport:
    missing_root: bool = False
    undefined_refs: list[tuple[str, str]] = field(default_factory=list)  # (from, missing id)
    orphans: list[str] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return int(self.missing_root) + len(self.undefined_refs) + len(self.orphans) + len(self.cycles)

    def to_dict(self) -> dict[str, Any]:
        return {
            "missing_root": self.missing_root,
            "undefined_refs": [list(r) for r in self.undefined_refs],
            "orphans": self.orphans,
            "cycles": self.cycles,
        }


def edges(components: dict[str, dict[str, Any]], index: RefIndex) -> dict[str, list[str]]:
    return {cid: [r.target for r in index.refs(comp)] for cid, comp in components.items()}


def check_structure(components: dict[str, dict[str, Any]], index: RefIndex) -> StructureReport:
    report = StructureReport()
    graph = edges(components, index)
    for src, targets in graph.items():
        report.undefined_refs.extend((src, t) for t in targets if t not in components)

    if ROOT not in components:
        report.missing_root = True
        return report

    seen: set[str] = set()
    stack = [ROOT]
    while stack:
        node = stack.pop()
        if node in seen or node not in components:
            continue
        seen.add(node)
        stack.extend(graph[node])
    report.orphans = [cid for cid in components if cid not in seen]

    # Iterative DFS with colours; a grey target is a back edge, i.e. a cycle.
    colour: dict[str, int] = {}
    for start in components:
        if colour.get(start):
            continue
        path: list[str] = []
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            node, i = work.pop()
            if i == 0:
                colour[node] = 1
                path.append(node)
            targets = [t for t in graph.get(node, []) if t in components]
            if i < len(targets):
                work.append((node, i + 1))
                nxt = targets[i]
                if colour.get(nxt) == 1:
                    report.cycles.append(path[path.index(nxt):] + [nxt])
                elif not colour.get(nxt):
                    work.append((nxt, 0))
            else:
                colour[node] = 2
                path.pop()
    return report


# --- bindings ---------------------------------------------------------------

# A `${...}` interpolation that is a bare path, not an expression.
_INTERP = re.compile(r"\$\{\s*(/?[\w\-~.]+(?:/[\w\-~.]+)*)\s*\}")


_BINDING_KEYS = ("path", "@path")


def binding_uses(value: Any) -> list[str]:
    """Every data path a component reads: a binding (`{"path": p}` in v0.9,
    `{"@path": p}` in v1) and `${p}` interpolations. A child-list template's
    `{"componentId", "path"}` is a component reference (refs.py), not a binding."""
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            key = None if "componentId" in v else next((k for k in _BINDING_KEYS if isinstance(v.get(k), str)), None)
            if key:
                out.append(v[key])
            for k, child in v.items():
                if k != key:
                    walk(child)
        elif isinstance(v, list):
            for child in v:
                walk(child)
        elif isinstance(v, str) and "${" in v:
            out.extend(_INTERP.findall(v))

    walk({k: v for k, v in value.items() if k not in ("id", "component")} if isinstance(value, dict) else value)
    return out


def template_contexts(components: dict[str, dict[str, Any]], index: RefIndex) -> dict[str, list[list[str]]]:
    """For each component, the data contexts it renders in.

    The root renders at the data root. A template child renders once per list
    item, so its context is the list path plus a `*` segment, and relative paths
    inside it resolve against that.
    """
    contexts: dict[str, list[list[str]]] = {}

    def spread(start: str) -> None:
        work: list[tuple[str, list[str]]] = [(start, [])]
        while work:
            cid, ctx = work.pop(0)
            known = contexts.setdefault(cid, [])
            if ctx in known or len(known) > 8:
                continue
            known.append(ctx)
            for ref in index.refs(components.get(cid, {})):
                if ref.target not in components:
                    continue
                child_ctx = ctx
                if ref.template_path is not None:
                    child_ctx = resolve(ctx, ref.template_path) + ["*"]
                work.append((ref.target, child_ctx))

    if ROOT in components:
        spread(ROOT)
    for cid in components:  # unreachable components read from the data root
        if cid not in contexts:
            spread(cid)
    return contexts


def resolve(ctx: list[str], path: str) -> list[str]:
    return pointer_segments(path) if path.startswith("/") else ctx + pointer_segments(path)


def unresolved_bindings(
    delta_ids: Iterable[str],
    components: dict[str, dict[str, Any]],
    index: RefIndex,
    data_models: list[Any],
    allowed: set[tuple[str, ...]] = frozenset(),
) -> list[tuple[str, str]]:
    """Paths read by the turn's components that no data model holds.

    `allowed` holds resolved paths the reference binds; a field the client
    writes (a TextField value) need not exist before the user types.
    """
    contexts = template_contexts(components, index)
    missing: list[tuple[str, str]] = []
    for cid in delta_ids:
        comp = components.get(cid)
        if comp is None:
            continue
        for path in binding_uses(comp):
            for ctx in contexts.get(cid, [[]]):
                segs = resolve(ctx, path)
                if tuple(segs) in allowed or any(has_path(dm, segs) for dm in data_models):
                    continue
                missing.append((cid, path))
                break
    return missing


def bound_paths(components: dict[str, dict[str, Any]], index: RefIndex) -> set[tuple[str, ...]]:
    contexts = template_contexts(components, index)
    out: set[tuple[str, ...]] = set()
    for cid, comp in components.items():
        for path in binding_uses(comp):
            for ctx in contexts.get(cid, [[]]):
                out.add(tuple(resolve(ctx, path)))
    return out
