"""Aligns predicted components to reference components and scores slots.

Component IDs are free choices, so they can't be compared directly. Alignment:
1. anchors: IDs the existing surface already holds must match by ID (an update
   turn has to target them by name);
2. root to root;
3. from each aligned pair, children slot by slot in order, when types agree;
4. the parent's leftover children, then any components still unaligned, by
   best match: same type, most similar text content.

Slot scoring compares (component_type, property, value) triples within aligned
pairs. A reference property counts too, with its value written as the
reference ID of the child it points at, so a child in the wrong slot or order
costs a triple.
"""

from __future__ import annotations

import difflib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .refs import Path, Ref, RefIndex, path_str

ROOT = "root"
Components = dict[str, dict[str, Any]]
Triple = tuple[str, str, str]


def _leaves(value: Any, path: Path, skip: set[Path], out: list[tuple[Path, Any]]) -> None:
    if path in skip:
        return
    if isinstance(value, dict) and value:
        for k, v in value.items():
            _leaves(v, path + (k,), skip, out)
    elif isinstance(value, list) and value:
        for i, v in enumerate(value):
            _leaves(v, path + (i,), skip, out)
    else:
        out.append((path, value))


def leaf_values(comp: dict[str, Any], refs: list[Ref]) -> list[tuple[str, Any]]:
    """(property path, value) for every non-reference leaf, minus id and type."""
    skip = {r.path for r in refs}
    out: list[tuple[Path, Any]] = []
    for key, value in comp.items():
        if key not in ("id", "component"):
            _leaves(value, (key,), skip, out)
    return [(path_str(p), v) for p, v in out]


def _text(comp: dict[str, Any], index: RefIndex) -> str:
    return " ".join(str(v) for _, v in leaf_values(comp, index.refs(comp)) if isinstance(v, (str, int, float)))


def similarity(a: dict[str, Any], b: dict[str, Any], index: RefIndex) -> float:
    ta, tb = _text(a, index), _text(b, index)
    if not ta and not tb:
        return 0.5
    return difflib.SequenceMatcher(None, ta, tb, autojunk=False).ratio()


def _children_by_slot(comp: dict[str, Any], index: RefIndex) -> dict[str, list[str]]:
    slots: dict[str, list[str]] = {}
    for ref in index.refs(comp):
        slots.setdefault(ref.slot, []).append(ref.target)
    return slots


def align(pred: Components, ref: Components, index: RefIndex, anchors: frozenset[str] = frozenset()) -> dict[str, str]:
    """Returns {pred_id: ref_id}, one-to-one."""
    mapping: dict[str, str] = {}
    taken: set[str] = set()
    order_p = {cid: i for i, cid in enumerate(pred)}
    order_r = {cid: i for i, cid in enumerate(ref)}

    def same_type(p: str, r: str) -> bool:
        return pred[p].get("component") == ref[r].get("component")

    def pair(p: str, r: str) -> None:
        mapping[p] = r
        taken.add(r)

    def best_match(ps: list[str], rs: list[str]) -> list[tuple[str, str]]:
        scored = sorted(
            (
                (-similarity(pred[p], ref[r], index), abs(order_p[p] - order_r[r]), order_p[p], p, r)
                for p in ps
                for r in rs
                if same_type(p, r)
            )
        )
        out, used_p, used_r = [], set(), set()
        for *_, p, r in scored:
            if p not in used_p and r not in used_r:
                used_p.add(p)
                used_r.add(r)
                out.append((p, r))
        return out

    for cid in pred:
        if cid in anchors and cid in ref and same_type(cid, cid):
            pair(cid, cid)
    if ROOT in pred and ROOT in ref and ROOT not in mapping and ROOT not in taken:
        pair(ROOT, ROOT)

    queue = list(mapping.items())
    while True:
        while queue:
            p, r = queue.pop(0)
            pc, rc = _children_by_slot(pred[p], index), _children_by_slot(ref[r], index)
            new: list[tuple[str, str]] = []
            for slot, p_kids in pc.items():
                for a, b in zip(p_kids, rc.get(slot, [])):
                    if a in pred and b in ref and a not in mapping and b not in taken and same_type(a, b):
                        pair(a, b)
                        new.append((a, b))
            left_p = [a for kids in pc.values() for a in kids if a in pred and a not in mapping]
            left_r = [b for kids in rc.values() for b in kids if b in ref and b not in taken]
            for a, b in best_match(list(dict.fromkeys(left_p)), list(dict.fromkeys(left_r))):
                pair(a, b)
                new.append((a, b))
            queue.extend(new)
        rest_p = [p for p in pred if p not in mapping]
        rest_r = [r for r in ref if r not in taken]
        found = best_match(rest_p, rest_r)
        if not found:
            return mapping
        # Take the single best global pair, then let its subtree align by structure.
        pair(*found[0])
        queue.append(found[0])


def triples(comp: dict[str, Any], index: RefIndex, target_name: dict[str, str] | None = None) -> Counter[Triple]:
    """Slot triples of one component. `target_name` maps child IDs to the names
    they are compared under (reference IDs); unmapped children get a marker
    that never matches."""
    ctype = str(comp.get("component"))
    refs = index.refs(comp)
    out: Counter[Triple] = Counter()
    for prop, value in leaf_values(comp, refs):
        out[(ctype, prop, json.dumps(value, sort_keys=True))] += 1
    for r in refs:
        name = r.target if target_name is None else target_name.get(r.target, f"?unaligned:{r.target}")
        out[(ctype, path_str(r.path), f"->{name}")] += 1
    return out


@dataclass
class SlotScore:
    tp: int = 0
    n_pred: int = 0
    n_ref: int = 0
    positional_errors: int = 0

    def add(self, other: "SlotScore") -> None:
        self.tp += other.tp
        self.n_pred += other.n_pred
        self.n_ref += other.n_ref
        self.positional_errors += other.positional_errors

    @property
    def precision(self) -> float:
        return self.tp / self.n_pred if self.n_pred else (1.0 if not self.n_ref else 0.0)

    @property
    def recall(self) -> float:
        return self.tp / self.n_ref if self.n_ref else (1.0 if not self.n_pred else 0.0)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tp": self.tp,
            "n_pred": self.n_pred,
            "n_ref": self.n_ref,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "positional_errors": self.positional_errors,
        }


def _positional_errors(pred_t: Counter[Triple], ref_t: Counter[Triple]) -> int:
    """A value the reference has in property q that the prediction put in another property.

    Only strings and numbers count: a stray `true` matching some other flag says
    nothing about argument order.
    """
    def by_value(t: Counter[Triple]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for _, prop, val in t:
            if val.startswith("->"):  # child references: order, not argument position
                continue
            parsed = json.loads(val)
            if (isinstance(parsed, str) and parsed) or (isinstance(parsed, (int, float)) and not isinstance(parsed, bool)):
                out.setdefault(val, set()).add(prop)
        return out

    pv, rv = by_value(pred_t), by_value(ref_t)
    count = 0
    for val, p_props in pv.items():
        r_props = rv.get(val, set())
        if r_props and p_props != r_props:
            count += len(p_props - r_props)
    return count


@dataclass
class Alignment:
    mapping: dict[str, str]
    score: SlotScore
    exact: bool
    unaligned_pred: list[str]
    unaligned_ref: list[str]
    per_type: dict[str, SlotScore]


def score_components(pred: Components, ref: Components, index: RefIndex, anchors: frozenset[str] = frozenset()) -> Alignment:
    mapping = align(pred, ref, index, anchors)
    # Children the turn didn't send (existing ones, by anchor ID) keep their own name.
    names = {a: a for a in anchors} | mapping
    total = SlotScore()
    per_type: dict[str, SlotScore] = {}
    exact = len(mapping) == len(pred) == len(ref)

    pred_t = {p: triples(c, index, names) for p, c in pred.items()}
    ref_t = {r: triples(c, index) for r, c in ref.items()}
    for p, t in pred_t.items():
        s = SlotScore(n_pred=sum(t.values()))
        if p in mapping:
            rt = ref_t[mapping[p]]
            s.tp = sum((t & rt).values())
            if pred[p].get("component") == ref[mapping[p]].get("component"):
                s.positional_errors = _positional_errors(t - rt, rt - t)
            exact = exact and t == rt
        total.add(s)
        per_type.setdefault(str(pred[p].get("component")), SlotScore()).add(s)
    for r, t in ref_t.items():
        n = sum(t.values())
        total.n_ref += n
        per_type.setdefault(str(ref[r].get("component")), SlotScore()).n_ref += n

    return Alignment(
        mapping=mapping,
        score=total,
        exact=exact,
        unaligned_pred=[p for p in pred if p not in mapping],
        unaligned_ref=[r for r in ref if r not in set(mapping.values())],
        per_type=per_type,
    )
