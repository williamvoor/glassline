"""Content specs: what one intent asks for, picked by seeded code from the schema bank.

The script only picks and arranges bank text; it never writes words the request must contain.
Rows are whole bank records projected onto the chosen columns, so the data stays consistent.
`layout` is an instruction to the wording model (how the person wants it arranged), not text the
request must quote. Spark Open turns a spec into a request (`wording.py`), and `checks.missing`
confirms every string of `required_strings` made it in.
"""

from __future__ import annotations

import random
from typing import Any

from .bank import Domain, Entity

# SPEC Phase 2 "Kind mix", in percent. `update` rows point at an earlier row of a data kind.
KIND_MIX: dict[str, int] = {
    "dashboard": 12,
    "grid": 12,
    "stats": 10,
    "list": 12,
    "detail": 12,
    "filter": 8,
    "form": 12,
    "state": 10,
    "update": 7,
    "text_only": 5,
}
PARENT_KINDS = ("dashboard", "grid", "list", "detail", "filter", "state", "stats")
FORM_TYPES = {"text": "text", "email": "text", "url": "text", "number": "number", "money": "number",
              "percent": "number", "date": "date", "status": "choice", "category": "choice", "bool": "checkbox"}

# Layout instructions for the wording model, by kind (SPEC Phase 2 fix 4). `list` always gets
# one, so a list stops reading as a table. Others get one with LAYOUT_P.
LAYOUTS: dict[str, tuple[str, ...]] = {
    "list": (
        "a simple list, one record per line with the name first and the rest smaller; not a table",
        "a list of small cards, one per record; not a table",
        "a stacked list with the name on its own line and the other values under it; not a table",
    ),
    "grid": ("a compact table", "a table with the name column first and wider"),
    "stats": ("the figures side by side in one row", "the figures as small cards in a grid",
              "the figures stacked, each with its label above the number"),
    "dashboard": ("the figures side by side at the top, the table below",
                  "the figures as small cards in a row, the table under them"),
    "detail": ("the name as a heading, then the other details as label and value pairs",
               "a card with the details in two columns side by side",
               "the details as label and value pairs, with the buttons at the bottom"),
    "filter": ("the filter above the table", "the filter and the search box side by side above the table"),
    "form": ("the fields stacked one per line with the button at the bottom",
             "two short fields side by side where it fits, the button at the bottom"),
    "state": ("cards, one per record, rather than a table", "a table", "a simple list, one record per line"),
}
LAYOUT_P = 0.6


def _layout(kind: str, rng: random.Random) -> dict[str, str]:
    opts = LAYOUTS.get(kind)
    if not opts or (kind != "list" and rng.random() >= LAYOUT_P):
        return {}
    return {"layout": rng.choice(opts)}


def _rows(rng: random.Random, e: Entity, labels: list[str], n: int) -> list[list[str]]:
    """`n` distinct whole records projected onto `labels`."""
    idx = [e.index(lab) for lab in labels]
    return [[rec[i] for i in idx] for rec in rng.sample(e.records, min(n, len(e.records)))]


def _labels(rng: random.Random, e: Entity, k: int, must: tuple[str, ...] = ()) -> list[str]:
    """The naming column first, then `must`, then random others; `k` in all, in bank order."""
    rest = [c.label for c in e.columns[1:] if c.label not in must]
    chosen = {e.columns[0].label, *must, *rng.sample(rest, max(0, min(len(rest), k - 1 - len(must))))}
    return [c.label for c in e.columns if c.label in chosen]


def _metrics(rng: random.Random, e: Entity, k: int) -> list[dict[str, str]]:
    return [{"label": m.label, "value": rng.choice(m.samples)} for m in rng.sample(e.metrics, min(k, len(e.metrics)))]


def build(kind: str, rng: random.Random, domain: Domain, entity: Entity) -> dict[str, Any] | None:
    """One content spec, or None when the entity can't support the kind (e.g. no status column)."""
    e = entity
    base = {"kind": kind, "domain": domain.slug, "domain_name": domain.name, "entity": e.name, "plural": e.plural,
            "bank_provenance": domain.provenance}
    if kind == "grid":
        labels = _labels(rng, e, rng.randint(3, 5))
        out = {**base, "columns": labels, "rows": _rows(rng, e, labels, rng.randint(3, 6))}
        if rng.random() < 0.4:
            out["row_action"] = rng.choice(e.actions)
    elif kind == "list":
        labels = _labels(rng, e, rng.randint(2, 3))
        out = {**base, "columns": labels, "rows": _rows(rng, e, labels, rng.randint(3, 6))}
    elif kind == "stats":
        out = {**base, "metrics": _metrics(rng, e, rng.randint(3, 4))}
    elif kind == "detail":
        labels = _labels(rng, e, rng.randint(4, min(7, len(e.columns))))
        (row,) = _rows(rng, e, labels, 1)
        out = {**base, "record": [list(p) for p in zip(labels, row)],
               "actions": rng.sample(e.actions, min(len(e.actions), rng.randint(1, 2)))}
    elif kind == "dashboard":
        labels = _labels(rng, e, 3)
        out = {**base, "metrics": _metrics(rng, e, rng.randint(2, 3)), "columns": labels,
               "rows": _rows(rng, e, labels, rng.randint(3, 4))}
    elif kind == "filter":
        if not e.choices:
            return None
        status = rng.choice(e.choices)
        labels = _labels(rng, e, rng.randint(3, 4), must=(status.label,))
        out = {**base, "columns": labels, "rows": _rows(rng, e, labels, rng.randint(3, 5)),
               "filter": {"label": status.label, "options": list(status.options)},
               "search": rng.random() < 0.5}
    elif kind == "form":
        cols = rng.sample(e.columns, min(len(e.columns), rng.randint(3, 5)))
        fields = []
        for c in sorted(cols, key=e.columns.index):
            f = {"label": c.label, "type": FORM_TYPES[c.type]}
            if c.type in ("status", "category"):
                f["options"] = list(c.options)
            fields.append(f)
        out = {**base, "fields": fields, "submit": e.create_action if rng.random() < 0.6 else None}
    elif kind == "state":
        if not e.statuses:
            return None
        status = rng.choice(e.statuses)
        labels = _labels(rng, e, rng.randint(2, 4), must=(status.label,))
        rows = _rows(rng, e, labels, rng.randint(3, 6))
        col = labels.index(status.label)
        values = sorted({r[col] for r in rows})
        if len(values) < 2:  # nothing to tell apart
            return None
        out = {**base, "columns": labels, "rows": rows,
               "highlight": {"column": status.label, "value": rng.choice(values)}}
    elif kind == "text_only":
        return {**base, "about": rng.sample([c.label for c in e.columns], 2)}
    else:
        raise ValueError(f"unknown kind {kind!r}")
    return {**out, **_layout(kind, rng)}


UPDATE_OPS = ("add_column", "remove_column", "add_row", "change_value", "add_action")


def build_update(rng: random.Random, parent: dict[str, Any], entity: Entity) -> dict[str, Any] | None:
    """A follow-up turn on `parent`'s content. Phase 4 answers the parent first and uses that
    answer as the existing surface. New values come from the same bank records."""
    e = entity
    base = {"kind": "update", "parent_kind": parent["kind"], "domain": parent["domain"],
            "domain_name": parent["domain_name"], "entity": e.name, "plural": e.plural,
            "bank_provenance": parent["bank_provenance"]}
    cols = parent.get("columns")
    ops = [op for op in UPDATE_OPS if cols or op == "add_action"]
    op = rng.choice(ops)
    by_name = {r[0]: r for r in e.records}
    if op == "add_column":
        unused = [c.label for c in e.columns if c.label not in cols]
        if not unused:
            return None
        lab = rng.choice(unused)
        i = e.index(lab)
        return {**base, "op": op, "column": lab, "values": [by_name[r[0]][i] for r in parent["rows"]]}
    if op == "remove_column":
        if len(cols) < 3:
            return None
        return {**base, "op": op, "column": rng.choice(cols[1:])}
    if op == "add_row":
        used = {r[0] for r in parent["rows"]}
        spare = [r for r in e.records if r[0] not in used]
        if not spare:
            return None
        rec = rng.choice(spare)
        return {**base, "op": op, "row": [rec[e.index(c)] for c in cols]}
    if op == "change_value":
        i, j = rng.randrange(len(parent["rows"])), rng.randrange(1, len(cols))
        col = e.column(cols[j])
        old = parent["rows"][i][j]
        new = [s for s in (col.options or col.samples) if s != old]
        if not new:
            return None
        return {**base, "op": op, "target": parent["rows"][i][0], "column": cols[j], "value": rng.choice(new)}
    return {**base, "op": "add_action", "action": rng.choice(e.actions)}


def required_strings(content: dict[str, Any]) -> list[str]:
    """Every label and value the request must state (R2). Option lists of filters and form choices
    are included: a user would say them. `layout` and `plural` are not: they guide the wording."""
    k = content["kind"]
    out: list[str] = []
    out += content.get("columns") or []
    for row in content.get("rows") or []:
        out += row
    for m in content.get("metrics") or []:
        out += [m["label"], m["value"]]
    for lab, val in content.get("record") or []:
        out += [lab, val]
    out += content.get("actions") or []
    if content.get("row_action"):
        out.append(content["row_action"])
    if f := content.get("filter"):
        out += [f["label"], *f["options"]]
    for f in content.get("fields") or []:
        out += [f["label"], *(f.get("options") or [])]
    if content.get("submit"):
        out.append(content["submit"])
    if h := content.get("highlight"):
        out.append(h["value"])
    if k == "text_only":
        out += content["about"]
    if k == "update":
        for key in ("column", "target", "value", "action"):
            if content.get(key):
                out.append(content[key])
        out += content.get("values") or []
        out += content.get("row") or []
    return list(dict.fromkeys(out))


def plan(rng: random.Random, total: int, mix: dict[str, int] = KIND_MIX) -> list[str]:
    """`total` kinds in the mix's proportions (largest remainder), shuffled."""
    weight = sum(mix.values())
    exact = {k: total * w / weight for k, w in mix.items()}
    counts = {k: int(v) for k, v in exact.items()}
    for k in sorted(exact, key=lambda k: exact[k] - counts[k], reverse=True)[: total - sum(counts.values())]:
        counts[k] += 1
    kinds = [k for k, n in counts.items() for _ in range(n)]
    rng.shuffle(kinds)
    return kinds


def generate(seed: int, total: int, domains: list[Domain]) -> list[dict[str, Any]]:
    """`total` content specs over `domains`, deterministic in `seed`. Kinds the drawn entity can't
    support are redrawn (another entity), up to a bound; `update` rows come last, each on an
    earlier spec of a parent kind."""
    rng = random.Random(seed)
    pairs = [(d, e) for d in domains for e in d.entities]
    if not pairs:
        raise ValueError("no entities")
    kinds = plan(rng, total)
    specs: list[dict[str, Any]] = []
    n_updates = kinds.count("update")
    for kind in (k for k in kinds if k != "update"):
        for _ in range(20):
            d, e = rng.choice(pairs)
            if (c := build(kind, rng, d, e)) is not None:
                specs.append(c)
                break
    by_name = {(d.slug, e.name): e for d, e in pairs}
    parents = [i for i, s in enumerate(specs) if s["kind"] in PARENT_KINDS and s.get("columns")]
    for _ in range(n_updates):
        for _ in range(20):
            i = rng.choice(parents)
            if (c := build_update(rng, specs[i], by_name[(specs[i]["domain"], specs[i]["entity"])])) is not None:
                specs.append({**c, "parent_index": i})
                break
    return specs
