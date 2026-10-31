"""Structural fingerprint of a reference: component types and tree shape only.

Ids, text, data values, bindings and surface ids are ignored, so variants of one
design that differ only in wording share a fingerprint.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

from .refs import RefIndex
from .structure import ROOT
from .surfaces import fold


def _shape(cid: str, components: dict[str, dict[str, Any]], index: RefIndex, seen: frozenset[str]) -> str:
    comp = components.get(cid)
    if comp is None:
        return "?"
    if cid in seen:
        return "<cycle>"
    seen = seen | {cid}
    parts = [f"{r.slot}={_shape(r.target, components, index, seen)}" for r in index.refs(comp)]
    return f"{comp.get('component')}({','.join(parts)})"


def shape(messages: Iterable[dict[str, Any]], index: RefIndex) -> str:
    """The canonical string the fingerprint hashes; one entry per surface, sorted."""
    states = fold(messages)
    return "|".join(sorted(_shape(ROOT, s.components, index, frozenset()) for s in states.values()))


def fingerprint(messages: Iterable[dict[str, Any]], index: RefIndex) -> str:
    return hashlib.sha256(shape(messages, index).encode()).hexdigest()[:16]
