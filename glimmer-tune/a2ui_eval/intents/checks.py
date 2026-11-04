"""Code checks on worded intents (SPEC Phase 2 "Checks"). A failing row is dropped and logged.

- `register`: R1/R3. No catalog component, prop or enum word, no `/path`, no backticks.
  Words come from the catalog JSON; content strings (bank text the user supplies) are removed
  first, so a column called "Weight" is not a prop.
- `missing`: R2. Every required string of the content spec appears in the intent.
- `NearDup`: char 5-gram Jaccard against reference intents and the set itself.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# Catalog props or enum values that read as UI jargon in a request. Only the ones present in the
# catalog's own vocabulary are used (`catalog_jargon`), so this list is policy, not a catalog fact.
JARGON_POLICY = (
    # R1's list, plus the archived check's unambiguous additions. Everyday words that are also
    # props ("cover", "contain", "primary", "action") are left out: users say them.
    "justify", "align", "weight", "variant", "fit", "usageHint", "caption", "markdown", "stretch",
)
_URL = re.compile(r"https?://\S+|\bwww\.\S+")
_PATH = re.compile(r"(?<![\w.:/])/[A-Za-z_][\w/]*")


def catalog_vocabulary(catalog_json: dict[str, Any]) -> tuple[set[str], set[str]]:
    """(component names, prop names and enum values) from the catalog JSON."""
    comps = set(catalog_json["components"])
    words: set[str] = set()

    def walk(schema: Any) -> None:
        if isinstance(schema, dict):
            for k, v in (schema.get("properties") or {}).items():
                words.add(k)
                walk(v)
            for e in schema.get("enum") or []:
                if isinstance(e, str):
                    words.add(e)
            for k in ("allOf", "anyOf", "oneOf", "items", "additionalProperties"):
                v = schema.get(k)
                for s in v if isinstance(v, list) else [v]:
                    walk(s)
        elif isinstance(schema, list):
            for s in schema:
                walk(s)

    for c in catalog_json["components"].values():
        walk(c)
    words -= {"component", "id"}
    return comps, words


def catalog_jargon(catalog_json: dict[str, Any]) -> tuple[set[str], set[str]]:
    """(components, jargon): camelCase or digit-bearing props/enums (`h1`, `spaceBetween`), plus
    the policy words the catalog really has."""
    comps, words = catalog_vocabulary(catalog_json)
    techy = {w for w in words if re.search(r"[a-z][A-Z]|\d", w)}
    return comps, techy | (words & set(JARGON_POLICY))


def _without(text: str, strings: Iterable[str]) -> str:
    """`text` with each required string blanked where it stands as a whole word, so "1" doesn't
    hide `h1` and "Patients" doesn't hide `/patients`."""
    for s in sorted(set(strings), key=len, reverse=True):
        if s:
            text = re.sub(rf"(?<![\w/]){re.escape(s)}(?!\w)", " ", text, flags=re.I)
    return text


def register(text: str, required: Iterable[str], comps: set[str], jargon: set[str]) -> list[str]:
    """Catalog words in the request's own wording (R1/R3). Capitalised component names are
    exempt at the start of a sentence ("List the…", "Text me…")."""
    words = _URL.sub(" ", _without(text, required))
    hits = []
    for c in sorted(comps):
        for m in re.finditer(rf"\b{re.escape(c)}\b", words):
            before = words[: m.start()].rstrip(" \t\"'(*-")
            if before and before[-1] not in ".!?:\n":
                hits.append(c)
                break
    # Inflections ("stretched") count only for words of 5+ letters: "fits" is everyday English.
    hits += [w for w in sorted(jargon)
             if re.search(rf"(?<![\w-]){re.escape(w)}{'(?:ed|s)?' if len(w) >= 5 else ''}(?![\w-])", words, re.I)]
    hits += _PATH.findall(words)
    if "`" in words:
        hits.append("`")
    return list(dict.fromkeys(hits))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def _short(s: str) -> bool:
    return len(s) <= 3 or bool(re.fullmatch(r"[\d.,%$€£ -]+", s))


def missing(text: str, required: Iterable[str]) -> list[str]:
    """Required strings not in the request (case and whitespace insensitive). Short and numeric
    values must stand as whole words: "No" isn't in "know", "5" isn't in "15"."""
    t = _norm(text)

    def found(s: str) -> bool:
        n = _norm(s)
        if _short(n):
            return re.search(rf"(?<![\w.,]){re.escape(n)}(?![\w]|[.,]\d)", t) is not None
        return n in t

    return [s for s in required if not found(s)]


def grams(text: str, n: int = 5) -> frozenset[str]:
    t = _norm(text)
    return frozenset(t[i : i + n] for i in range(max(1, len(t) - n + 1)))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 1.0


class NearDup:
    """Rejects a text whose 5-gram Jaccard with any reference or earlier accepted text is ≥ `t`.
    Exhaustive comparison: fine for a few thousand rows against a few hundred references."""

    def __init__(self, references: Iterable[str], t: float):
        self.t = t
        self.refs = [grams(r) for r in references]
        self.seen: list[frozenset[str]] = []

    def check(self, text: str) -> tuple[str, float] | None:
        """('reference' | 'set', similarity) when too close, else None (and the text is kept)."""
        g = grams(text)
        best_ref = max((jaccard(g, r) for r in self.refs), default=0.0)
        if best_ref >= self.t:
            return "reference", best_ref
        best_set = max((jaccard(g, s) for s in self.seen), default=0.0)
        if best_set >= self.t:
            return "set", best_set
        self.seen.append(g)
        return None


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def copied(text: str, instruction: str, n: int = 6) -> str | None:
    """The first run of `n` consecutive words of `instruction` found in `text`, or None. Catches a
    wording model pasting its layout instruction instead of saying it in its own words."""
    hay = " " + " ".join(_words(text)) + " "
    words = _words(instruction)
    for i in range(len(words) - n + 1):
        run = " ".join(words[i : i + n])
        if f" {run} " in hay:
            return run
    return None
