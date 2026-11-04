"""The schema bank: domains → entities → columns with sample display values.

Spark Open drafts it (SPEC Phase 2 Q1); code validates it. Every text in the bank is a sample a
user might type, so content specs only ever pick from it and never invent text themselves.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..seeds import ROOT

BANK_PATH = ROOT / "data" / "intents" / "schema_bank.json"
COLUMN_TYPES = ("text", "number", "money", "percent", "date", "status", "category", "bool", "email", "url")
# Columns with a fixed option list. `status` is a record's state (Open, Overdue); `category` is a
# fixed kind that is not a state (Service Type). Only statuses are highlighted.
CHOICE_TYPES = ("status", "category", "bool")
MIN_RECORDS = 12  # the prompt asks for 16; content draws up to 6 rows, each a distinct record
MIN_SAMPLES = 3  # metrics
MIN_COLUMNS = 4
MIN_METRICS = 3
MIN_ACTIONS = 1


class BankError(Exception):
    pass


@dataclass(frozen=True)
class Column:
    label: str
    type: str
    samples: tuple[str, ...]  # the distinct values this column takes in the records
    options: tuple[str, ...] = ()  # status and bool columns: every possible value


@dataclass(frozen=True)
class Metric:
    label: str
    samples: tuple[str, ...]


@dataclass(frozen=True)
class Entity:
    name: str  # singular, lower case ("patient")
    plural: str
    columns: tuple[Column, ...]  # the first column names the record
    records: tuple[tuple[str, ...], ...]  # whole, consistent records, values in column order
    metrics: tuple[Metric, ...]
    actions: tuple[str, ...]  # button texts for one record ("Discharge")
    create_action: str  # the button text that saves a new record ("Admit patient")

    def column(self, label: str) -> Column:
        return next(c for c in self.columns if c.label == label)

    def index(self, label: str) -> int:
        return next(i for i, c in enumerate(self.columns) if c.label == label)

    @property
    def statuses(self) -> tuple[Column, ...]:
        return tuple(c for c in self.columns if c.type == "status")

    @property
    def choices(self) -> tuple[Column, ...]:
        """Columns a filter can narrow by: statuses and categories."""
        return tuple(c for c in self.columns if c.type in ("status", "category"))


@dataclass(frozen=True)
class Domain:
    slug: str
    name: str
    entities: tuple[Entity, ...]
    provenance: str


def _texts(raw: Any, where: str, minimum: int) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(s, str) and s.strip() for s in raw):
        raise BankError(f"{where}: expected a list of non-empty strings")
    out = tuple(dict.fromkeys(s.strip() for s in raw))  # drop duplicates, keep order
    if len(out) < minimum:
        raise BankError(f"{where}: {len(out)} distinct values, need {minimum}")
    return out


def _text(raw: Any) -> str | None:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        raw = str(raw)
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def parse_entity(raw: dict[str, Any], where: str) -> Entity:
    if not isinstance(raw, dict):
        raise BankError(f"{where}: not an object")
    name, plural = raw.get("name"), raw.get("plural")
    if not (isinstance(name, str) and name.strip() and isinstance(plural, str) and plural.strip()):
        raise BankError(f"{where}: needs name and plural")
    where = f"{where}/{name}"
    heads: list[tuple[str, str, tuple[str, ...]]] = []
    for i, c in enumerate(raw.get("columns") or []):
        if not isinstance(c, dict) or not isinstance(c.get("label"), str) or not c["label"].strip():
            raise BankError(f"{where}: column {i} has no label")
        if c.get("type") not in COLUMN_TYPES:
            raise BankError(f"{where}/{c['label']}: type {c.get('type')!r} not in {COLUMN_TYPES}")
        opts = _texts(c.get("options"), f"{where}/{c['label']} options", 2) if c["type"] in CHOICE_TYPES else ()
        heads.append((c["label"].strip(), c["type"], opts))
    if len(heads) < MIN_COLUMNS:
        raise BankError(f"{where}: {len(heads)} columns, need {MIN_COLUMNS}")
    if len({h[0].lower() for h in heads}) != len(heads):
        raise BankError(f"{where}: duplicate column labels")
    if heads[0][1] not in ("text", "email"):
        raise BankError(f"{where}: the first column must name the record (text), got {heads[0][1]}")
    records = []
    for j, r in enumerate(raw.get("records") or []):
        if not isinstance(r, dict):
            raise BankError(f"{where}: record {j} is not an object")
        rec = []
        for lab, typ, opts in heads:
            v = _text(r.get(lab))
            if v is None:
                raise BankError(f"{where}: record {j} missing {lab}")
            if opts and v not in opts:
                raise BankError(f"{where}: record {j} {lab}={v!r} not in its options")
            rec.append(v)
        records.append(tuple(rec))
    if len(records) < MIN_RECORDS:
        raise BankError(f"{where}: {len(records)} records, need {MIN_RECORDS}")
    if len({r[0] for r in records}) != len(records):
        raise BankError(f"{where}: the naming column repeats a value")
    cols = tuple(Column(lab, typ, tuple(dict.fromkeys(r[i] for r in records)), opts)
                 for i, (lab, typ, opts) in enumerate(heads))
    metrics = []
    for i, m in enumerate(raw.get("metrics") or []):
        if not isinstance(m, dict) or not isinstance(m.get("label"), str) or not m["label"].strip():
            raise BankError(f"{where}: metric {i} has no label")
        metrics.append(Metric(m["label"].strip(), _texts(m.get("samples"), f"{where}/metric {m['label']}", MIN_SAMPLES)))
    if len(metrics) < MIN_METRICS:
        raise BankError(f"{where}: {len(metrics)} metrics, need {MIN_METRICS}")
    actions = _texts(raw.get("actions"), f"{where}/actions", MIN_ACTIONS)
    create = _text(raw.get("create_action"))
    if create is None:
        raise BankError(f"{where}: needs a create_action")
    return Entity(name.strip(), plural.strip(), cols, tuple(records), tuple(metrics), actions, create)


def parse_domain(raw: dict[str, Any], provenance: str) -> Domain:
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw["name"].strip():
        raise BankError("domain without a name")
    name = raw["name"].strip()
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    ents = raw.get("entities") or []
    if not ents:
        raise BankError(f"{slug}: no entities")
    entities = tuple(parse_entity(e, slug) for e in ents)
    return Domain(slug, name, entities, provenance)


def load_bank(path: Path = BANK_PATH) -> list[Domain]:
    d = json.loads(path.read_text())
    domains = [parse_domain(x, x["provenance"]) for x in d["domains"]]
    if len({x.slug for x in domains}) != len(domains):
        raise BankError("duplicate domain slugs")
    return domains


def domain_to_json(d: Domain) -> dict[str, Any]:
    return {
        "name": d.name,
        "provenance": d.provenance,
        "entities": [
            {
                "name": e.name,
                "plural": e.plural,
                "columns": [{"label": c.label, "type": c.type, **({"options": list(c.options)} if c.options else {})}
                            for c in e.columns],
                "records": [dict(zip((c.label for c in e.columns), r)) for r in e.records],
                "metrics": [{"label": m.label, "samples": list(m.samples)} for m in e.metrics],
                "actions": list(e.actions),
                "create_action": e.create_action,
            }
            for e in d.entities
        ],
    }


# ---------------------------------------------------------------- drafting prompts (Spark Open)

DOMAINS_PROMPT = """\
List {n} different areas of work or life where someone keeps records in tables and would want \
screens to view and edit them: dashboards, lists, detail pages, forms. Mix work (clinics, \
warehouses, schools, councils, labs, trades) and personal life (budgets, fitness, hobbies, \
travel). Avoid near-repeats. Do not include: {avoid}.

Reply with only a JSON array of short names, for example ["Veterinary clinic", "Home budget"]."""

DOMAIN_PROMPT = """\
Describe the records kept in this area: "{name}".

Give 2 kinds of record (entities). For each give:
- "name" (singular, lower case) and "plural";
- "columns": 5 to 8 columns. The first column names the record (type "text"). Each column has a \
"label" (as a person would write it as a table heading) and a "type" (one of {types}). Use \
"status" only for the state a record is in (such as Open, Overdue, Shipped) and "category" for a \
fixed kind that is not a state (such as Service Type or Genre). Status, category and bool \
columns also have "options": every value they can take, at least 2;
- "records": 16 whole records, each an object mapping every column label to its value, written \
exactly as it would appear on screen. Each record must be internally consistent and true to \
life (an end date after its start date, an author who wrote that book, an email that fits the \
person). The first column's value is different in every record. Status and bool values come \
from their options;
- "metrics": 3 to 5 summary figures someone would want at a glance, each with a "label" and 6 \
realistic "samples" written as displayed (with units or currency where natural);
- "actions": 2 to 4 short button texts for things a person does with one existing record;
- "create_action": the short button text that saves a new record of this kind.

Use plain, real-world wording and varied, realistic values, with no placeholders like "Item 1". \
Reply with only a JSON object: {{"name": "{name}", "entities": [...]}}."""


def extract_json(text: str) -> Any:
    """The first JSON value in a model reply (code fences and prose around it are ignored)."""
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise BankError("no JSON in reply")
    value, _ = json.JSONDecoder().raw_decode(text[start:])
    return value
