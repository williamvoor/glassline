"""Plausibility linter (build B) and banned-archetype matchers (build A).

A matcher is a predicate on a folded v0.9 surface. It catches a near-copy of one test
design without catching generic forms, lists or cards (SPEC 2c-1, Banned archetypes).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import yaml

from .refs import RefIndex
from .surfaces import CREATE, DELETE, fold, message_kind, pointer_segments

Comps = dict[str, dict[str, Any]]
INPUTS = ("TextField", "DateTimeInput", "ChoicePicker", "CheckBox", "Slider")


class Tree:
    def __init__(self, components: Comps, index: RefIndex):
        self.c = components
        self.index = index

    def kids(self, cid: str) -> list[str]:
        """Static children (or the single child) in order; a template contributes nothing here."""
        comp = self.c.get(cid) or {}
        ch = comp.get("children")
        if isinstance(ch, list):
            return [x for x in ch if x in self.c]
        if isinstance(comp.get("child"), str):
            return [comp["child"]] if comp["child"] in self.c else []
        return []

    def types(self, cid: str) -> list[str]:
        return [self.c[k]["component"] for k in self.kids(cid)]

    def type(self, cid: str) -> str | None:
        return (self.c.get(cid) or {}).get("component")

    def is_(self, cid: str, comp: str, kid_types: list[str] | None = None) -> bool:
        if self.type(cid) != comp:
            return False
        return kid_types is None or self.types(cid) == kid_types

    def descendants(self, cid: str) -> list[str]:
        out, stack, seen = [], [cid], set()
        while stack:
            n = stack.pop()
            if n in seen or n not in self.c:
                continue
            seen.add(n)
            out.append(n)
            stack.extend(r.target for r in self.index.refs(self.c[n]))
        return out

    def all_of(self, comp: str) -> list[str]:
        return [cid for cid, c in self.c.items() if c.get("component") == comp]

    def unwrap_card(self, cid: str) -> str:
        return self.c[cid]["child"] if self.type(cid) == "Card" and isinstance(self.c[cid].get("child"), str) else cid


def _complex_layout(t: Tree) -> bool:
    root = t.unwrap_card("root") if "root" in t.c else None
    if root is None or not t.is_(root, "Column", ["Text", "Row", "Text"]):
        return False
    row = t.kids(root)[1]
    kinds = t.types(row)
    return len(kinds) >= 2 and all(k == "TextField" for k in kinds)


def _email_compose(t: Tree) -> bool:
    for col in t.all_of("Column"):
        kids = t.kids(col)
        for i in range(len(kids)):
            j = i
            while j < len(kids) and t.is_(kids[j], "Row", ["Text", "Text"]):
                j += 1
            if j - i < 3 or j + 2 >= len(kids):
                continue
            body, buttons = kids[j + 1], kids[j + 2]
            if (
                t.type(kids[j]) == "Divider"
                and t.type(body) == "Column"
                and len(t.types(body)) >= 3
                and all(x == "Text" for x in t.types(body))
                and t.type(buttons) == "Row"
                and len(t.types(buttons)) >= 2
                and all(x == "Button" for x in t.types(buttons))
            ):
                return True
    return False


def _chat_message(t: Tree) -> bool:
    for c in t.c.values():
        ch = c.get("children")
        if not (isinstance(ch, dict) and ch.get("componentId") in t.c):
            continue
        item = ch["componentId"]
        if not t.is_(item, "Row", ["Image", "Column"]):
            continue
        col = t.kids(item)[1]
        if t.is_(col, "Column", ["Row", "Text"]) and t.is_(t.kids(col)[0], "Row", ["Text", "Text"]):
            return True
    return False


def _workout_summary(t: Tree) -> bool:
    for col in t.all_of("Column"):
        if t.types(col) != ["Row", "Divider", "Row", "Text"]:
            continue
        head, _, stats, _ = t.kids(col)
        if t.types(head) == ["Icon", "Text"] and t.types(stats) == ["Column"] * 3 and all(
            t.types(s) == ["Text", "Text"] for s in t.kids(stats)
        ):
            return True
    return False


def _restaurant_card(t: Tree) -> bool:
    # Train 29_movie-card is a near twin; it differs by a Modal beside the details and a Button in them.
    for col in t.all_of("Column"):
        if t.types(col) != ["Image", "Column"]:
            continue
        inner = t.kids(col)[1]
        kids = t.kids(inner)
        if not kids or not t.is_(kids[0], "Row", ["Text", "Text"]) or "Button" in t.types(inner):
            continue
        for k in kids:
            if t.type(k) == "Row" and t.types(k)[:1] == ["Icon"] and t.c[t.kids(k)[0]].get("name") == "star":
                return True
    return False


def _contact_card(t: Tree) -> bool:
    for col in t.all_of("Column"):
        if t.types(col) != ["Image", "Text", "Text", "Divider", "Column", "Row"]:
            continue
        details, buttons = t.kids(col)[4], t.kids(col)[5]
        rows = [k for k in t.kids(details) if t.is_(k, "Row", ["Icon", "Text"])]
        if len(rows) >= 3 and len(t.types(buttons)) >= 2 and all(x == "Button" for x in t.types(buttons)):
            return True
    return False


def _live_invitation_builder(t: Tree) -> bool:
    for row in t.all_of("Row"):
        kids = t.kids(row)
        if t.types(row) != ["Column", "Column"]:
            continue
        has_input = [any(t.type(d) in INPUTS for d in t.descendants(k)) for k in kids]
        has_card = [any(t.type(d) == "Card" for d in t.descendants(k)) for k in kids]
        if (has_input[0] and has_card[1]) or (has_input[1] and has_card[0]):
            return True
    return False


def _modal(t: Tree) -> bool:
    for col in t.all_of("Column"):
        if t.types(col) != ["Text", "Modal"]:
            continue
        modal = t.c[t.kids(col)[1]]
        content = modal.get("content")
        if t.type(content) == "Column":
            kinds = t.types(content)
            if len(kinds) <= 2 and all(k == "Text" for k in kinds):
                return True
    return False


MATCHERS: dict[str, Callable[[Tree], bool]] = {
    "complex_layout": _complex_layout,
    "email_compose": _email_compose,
    "chat_message": _chat_message,
    "workout_summary": _workout_summary,
    "restaurant_card": _restaurant_card,
    "contact_card": _contact_card,
    "live_invitation_builder": _live_invitation_builder,
    "modal": _modal,
}


def banned_matches(messages: list[dict[str, Any]], index: RefIndex, banned: dict[str, dict[str, str]]) -> list[str]:
    """Test families whose banned archetype the surface matches."""
    out = []
    for state in fold(messages).values():
        t = Tree(state.components, index)
        out.extend(fam for fam, spec in banned.items() if MATCHERS[spec["matcher"]](t))
    return sorted(set(out))


# ---------------------------------------------------------------- plausibility linter (S8)

HEADING_VARIANTS = {"h1", "h2", "h3", "h4", "h5"}
HEADING_MAX_CHARS = 60
BODY_MIN_WORDS = 3
_LOREM = re.compile(r"\b(lorem|ipsum)\b", re.IGNORECASE)
ALLOWLIST = Path(__file__).resolve().parents[1] / "data" / "synth_lint_allowlist.yaml"


def _paths_in(value: Any) -> set[str]:
    """Every data path a value references (`{"path": ...}` anywhere inside it)."""
    out: set[str] = set()
    if isinstance(value, dict):
        if isinstance(value.get("path"), str):
            out.add(value["path"])
        for v in value.values():
            out |= _paths_in(v)
    elif isinstance(value, list):
        for v in value:
            out |= _paths_in(v)
    return out


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _empty(t: Tree, cid: Any) -> bool:
    """Missing, a blank Text, or a container with nothing in it."""
    comp = t.c.get(cid) if isinstance(cid, str) else None
    if comp is None:
        return True
    if comp.get("component") == "Text":
        text = comp.get("text")
        return isinstance(text, str) and not text.strip()
    ch = comp.get("children")
    return isinstance(ch, list) and not ch


def _value_path(c: dict[str, Any]) -> str | None:
    v = c.get("value")
    return v["path"] if isinstance(v, dict) and isinstance(v.get("path"), str) else None


def _rule_button_action(t: Tree, tags: tuple[str, ...], data: Any, model: Any) -> bool:
    return any(not t.c[b].get("action") for b in t.all_of("Button"))


def _rule_form_submit(t: Tree, tags: tuple[str, ...], data: Any, model: Any) -> bool:
    inputs = [c for c in t.c.values() if c.get("component") in INPUTS]
    if "form" not in tags and len(inputs) < 2:
        return False
    # Template-relative paths can't be named in an absolute event context, so they are skipped.
    needed = {p for c in inputs if (p := _value_path(c)) and p.startswith("/")}
    def covered(sent: set[str], path: str) -> bool:  # a context path also covers everything under it
        return any(path == s or path.startswith(s.rstrip("/") + "/") for s in sent)

    for b in t.all_of("Button"):
        event = (t.c[b].get("action") or {}).get("event") or {}
        sent = _paths_in(event.get("context"))
        if event.get("name") and all(covered(sent, p) for p in needed):
            return False
    return True


def _rule_input_bound(t: Tree, tags: tuple[str, ...], data: Any, model: Any) -> bool:
    return any(_value_path(c) is None for c in t.c.values() if c.get("component") in INPUTS)


def _rule_modal(t: Tree, tags: tuple[str, ...], data: Any, model: Any) -> bool:
    return any(
        t.type(t.c[m].get("trigger")) != "Button" or _empty(t, t.c[m].get("content")) for m in t.all_of("Modal")
    )


def _rule_tabs(t: Tree, tags: tuple[str, ...], data: Any, model: Any) -> bool:
    for cid in t.all_of("Tabs"):
        tabs = t.c[cid].get("tabs")
        if not isinstance(tabs, list) or len(tabs) < 2:
            return True
        if any(not isinstance(tab, dict) or _empty(t, tab.get("child")) for tab in tabs):
            return True
    return False


def _rule_icon_name(t: Tree, tags: tuple[str, ...], data: Any, model: Any) -> bool:
    allowed = set(model.enum("Icon", "name"))
    return any(isinstance(n := t.c[i].get("name"), str) and n not in allowed for i in t.all_of("Icon"))


def _heading_text(c: dict[str, Any]) -> str | None:
    """The heading's text without Markdown marks, or None when the Text is not a heading."""
    text = c.get("text")
    if not isinstance(text, str):
        return None
    if c.get("variant") in HEADING_VARIANTS or text.lstrip().startswith("#"):
        # A Markdown document (heading mark, then more lines) is judged by its first line.
        return text.lstrip().lstrip("#").split("\n", 1)[0].strip() if text.lstrip().startswith("#") else text.strip()
    return None


def _rule_heading_length(t: Tree, tags: tuple[str, ...], data: Any, model: Any) -> bool:
    return any((h := _heading_text(t.c[i])) is not None and len(h) > HEADING_MAX_CHARS for i in t.all_of("Text"))


def _rule_body_words(t: Tree, tags: tuple[str, ...], data: Any, model: Any) -> bool:
    # Body text is a literal paragraph under a heading: a body-variant Text whose Column or Card
    # also holds a heading. Labels, table cells and record values have no heading beside them.
    for cid in t.all_of("Column") + t.all_of("Card"):
        kids = t.kids(cid)
        if not any(t.type(k) == "Text" and _heading_text(t.c[k]) is not None for k in kids):
            continue
        for k in kids:
            c = t.c[k]
            text = c.get("text")
            if t.type(k) != "Text" or not isinstance(text, str) or _heading_text(c) is not None:
                continue
            if c.get("variant") in (None, "body") and len(text.split()) < BODY_MIN_WORDS:
                return True
    return False


def _resolve(data: Any, path: str) -> Any:
    node = data
    for seg in pointer_segments(path):
        if isinstance(node, dict):
            node = node.get(seg)
        elif isinstance(node, list) and seg.isdigit() and int(seg) < len(node):
            node = node[int(seg)]
        else:
            return None
    return node


def _rule_list_source(t: Tree, tags: tuple[str, ...], data: Any, model: Any) -> bool:
    for cid in t.all_of("List"):
        ch = t.c[cid].get("children")
        if isinstance(ch, list) and not ch:
            return True
        # Nested (relative) template paths are not resolved.
        if isinstance(ch, dict) and isinstance(ch.get("path"), str) and ch["path"].startswith("/"):
            node = _resolve(data, ch["path"])
            if not (isinstance(node, list) and node):
                return True
    return False


def _rule_lorem(t: Tree, tags: tuple[str, ...], data: Any, model: Any) -> bool:
    return any(_LOREM.search(s) for s in [*_strings(list(t.c.values())), *_strings(data)])


_HEADING_MARK = re.compile(r"^#{1,6}\s+")


def _shown_string(text: Any, data: Any) -> str | None:
    """A static Text's string: a literal (heading mark stripped) or an absolute path's string value."""
    if isinstance(text, dict) and isinstance(text.get("path"), str) and text["path"].startswith("/"):
        text = _resolve(data, text["path"])
    if not isinstance(text, str):
        return None
    s = _HEADING_MARK.sub("", text).strip().casefold()
    return s if len(s) >= 4 and any(ch.isalpha() for ch in s) else None


def _parents(t: Tree) -> dict[str, tuple[str, int]]:
    return {k: (cid, i) for cid in t.c for i, k in enumerate(t.kids(cid))}


def _template_items(t: Tree) -> set[str]:
    """Every component inside a template item (rendered once per record)."""
    out: set[str] = set()
    stack = [c["children"]["componentId"] for c in t.c.values() if isinstance(c.get("children"), dict) and isinstance(c["children"].get("componentId"), str)]
    while stack:
        n = stack.pop()
        if n in out or n not in t.c:
            continue
        out.add(n)
        stack.extend(t.kids(n))
    return out


def _chain(parents: dict[str, tuple[str, int]], cid: str) -> list[tuple[str, int]]:
    """(parent, index) steps from `cid` up to the root."""
    out, seen = [], {cid}
    while cid in parents and parents[cid][0] not in seen:
        out.append(parents[cid])
        cid = parents[cid][0]
        seen.add(cid)
    return out


def _same_slot_in_siblings(t: Tree, parents: dict[str, tuple[str, int]], a: str, b: str) -> bool:
    """True when `a` and `b` sit at the same place in two sibling subtrees of one parent: a cell of a
    static list repeated across its rows ("Pending", "Pending") rather than a value shown twice."""
    ca, cb = _chain(parents, a), _chain(parents, b)
    ups_a = {p: i for i, (p, _) in enumerate(ca)}
    for j, (p, _) in enumerate(cb):
        if p in ups_a:
            i = ups_a[p]
            below_a = [x for _, x in ca[:i + 1]]
            below_b = [x for _, x in cb[:j + 1]]
            # The rows must be containers (i, j > 0): two Texts that are themselves siblings aren't cells.
            if i == 0 or j == 0 or below_a[-1] == below_b[-1] or below_a[:-1] != below_b[:-1]:
                return False
            return t.type(ca[i - 1][0]) == t.type(cb[j - 1][0])
    return False


def _rule_repeated_value(t: Tree, tags: tuple[str, ...], data: Any, model: Any) -> bool:
    """SPEC "Sample review fixes D3-D7", D3: one string shown by two static Texts. Texts inside a
    template item, and a cell repeated across a static list's rows, don't count."""
    parents, templated = _parents(t), _template_items(t)
    seen: dict[str, list[str]] = {}
    for cid, c in t.c.items():
        if c.get("component") != "Text" or cid in templated:
            continue
        s = _shown_string(c.get("text"), data)
        if s is None:
            continue
        if any(not _same_slot_in_siblings(t, parents, other, cid) for other in seen.get(s, [])):
            return True
        seen.setdefault(s, []).append(cid)
    return False


_RULES: dict[str, Callable[[Tree, tuple[str, ...], Any, Any], bool]] = {
    "button_action": _rule_button_action,
    "form_submit": _rule_form_submit,
    "input_bound": _rule_input_bound,
    "modal": _rule_modal,
    "tabs": _rule_tabs,
    "icon_name": _rule_icon_name,
    "heading_length": _rule_heading_length,
    "body_words": _rule_body_words,
    "list_source": _rule_list_source,
    "lorem": _rule_lorem,
    "repeated_value": _rule_repeated_value,
}
RULES = tuple(_RULES)


def lint_surface(messages: list[dict[str, Any]], model: Any, tags: Iterable[str] = ()) -> list[str]:
    """Ids of the S8 rules the folded v0.9 surface fails, sorted. Empty means plausible.

    `model` is a `CatalogModel` (for the Icon enum); `tags` are the skeleton's tags.
    A message list that folds to several surfaces fails a rule if any surface does.
    """
    tags = tuple(tags)
    failed: set[str] = set()
    for state in fold(messages).values():
        t = Tree(state.components, None)  # type: ignore[arg-type]  # these rules need no ref index
        failed |= {rule for rule, fails in _RULES.items() if fails(t, tags, state.data, model)}
    return sorted(failed)


def lint_rows(rows: Iterable[dict[str, Any]], model: Any, skeleton_tags: dict[str, Iterable[str]]) -> dict[str, list[str]]:
    """Synthetic rows that fail a rule, by row id. Tags come from the row's skeleton."""
    out = {}
    for r in rows:
        failed = lint_surface(r["reference"], model, skeleton_tags.get(r.get("skeleton"), ()))
        if failed:
            out[r["id"]] = failed
    return out


def is_single_turn(messages: list[dict[str, Any]]) -> bool:
    kinds = [message_kind(m)[0] for m in messages]
    return kinds.count(CREATE) == 1 and DELETE not in kinds


def load_gallery_allowlist(path: Path = ALLOWLIST) -> dict[str, dict[str, str]]:
    doc = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
    return {stem: dict(rules) for stem, rules in doc.items()}


def lint_gallery(seeds: dict[str, Any], model: Any, allowlist: dict[str, dict[str, str]]) -> dict[str, list[str]]:
    """Single-turn seeds that fail a rule that is not allowlisted, by seed stem."""
    out = {}
    for stem, seed in seeds.items():
        if not is_single_turn(seed.messages):
            continue
        failed = [r for r in lint_surface(seed.messages, model) if r not in allowlist.get(stem, {})]
        if failed:
            out[stem] = failed
    return out


def stale_allowlist(seeds: dict[str, Any], model: Any, allowlist: dict[str, dict[str, str]]) -> list[str]:
    """`stem:rule` entries naming an unknown or multi-turn seed, or a rule the seed no longer fails."""
    stale = []
    for stem, rules in allowlist.items():
        seed = seeds.get(stem)
        failed = set(lint_surface(seed.messages, model)) if seed and is_single_turn(seed.messages) else set()
        stale += [f"{stem}:{r}" for r in rules if r not in failed]
    return stale
