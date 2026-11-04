"""Spark Open turns a content spec into a user's request (SPEC Phase 2 "Wording").

The style directive (length and register) is picked by seeded code per row, so the batch varies
in a controlled way instead of collapsing into one model voice.
"""

from __future__ import annotations

import json
import random
import re
from typing import Any

STYLES = (
    "short and terse, like a quick chat message",
    "chatty and informal, one or two sentences of context first",
    "a plain, polite request in full sentences",
    "a short intro line, then the details as a bulleted list",
    "a request with the data pasted as a small plain-text table",
    "matter-of-fact, as a busy professional would write to a colleague",
)

# How the message starts, seeded per row (SPEC Phase 2 completion E1). Without it, 48% of the
# first 532 rows opened with "Hey" or "Hi".
OPENERS = (
    "a short greeting",
    "going straight to what they want, with no greeting",
    "a few words of context about their work or situation",
    "asking a question",
    "naming the thing they want, as a plain noun phrase",
)

SYSTEM = """\
You write requests that a real person types to an assistant that builds app screens for them. \
You are given a structured spec of what the person wants. Write the person's request.

Rules:
1. Say what they want on screen in everyday words ("a table of...", "a card for...", "side by \
side", "at the top"). Never use UI-toolkit or developer words: no component names (Column, Row, \
Card, Text, List...), no layout props (justify, align, weight, variant, stretch), no heading \
levels (h1, h2), no data paths, field names in code form, event names, or icon names. A person \
may describe an icon in words ("a little warning sign").
2. Include every label, heading, value and option in the spec, written exactly as given \
(same spelling, capitals, units and punctuation). Quote labels and button texts where a person \
naturally would.
3. Don't add data, columns or buttons that aren't in the spec. You may add brief everyday \
context about why they want it.
4. If a layout is given, ask for that arrangement in your own everyday words, the way this \
person would say it. Never copy the layout text.
5. Write in the requested style. The style describes how the person writes the message; it is \
never something to put on the screen.

Reply with only the request inside <intent>...</intent>."""

# `{plural}` is the bank's word for the records, so the request can name them naturally
# instead of quoting a spec key (SPEC Phase 2 fix 3).
KIND_HINTS = {
    "grid": "They want a table of their {plural}.",
    "list": "They want a simple list of their {plural}.",
    "stats": "They want these summary figures about their {plural} at a glance.",
    "detail": "They want a detail view of one of their {plural}, with these buttons.",
    "dashboard": "They want an overview of their {plural}: these summary figures, then a short table.",
    "filter": "They want a table of their {plural} with a way to narrow it down by the given field and"
              " options (and a search box if `search` is true).",
    "form": "They want a form to enter a new one of their {plural} with these fields (and this button"
            " text if `submit` is given). The field types say what kind of input fits.",
    "state": "They want these {plural}, with the ones whose `highlight.column` is `highlight.value`"
             " shown so they stand out from the rest. Say how in everyday terms or leave it to the"
             " assistant, but name the value.",
    "update": "They already have a screen of their {plural} from an earlier request, and now want this"
              " change to it (`op`). Write only the follow-up message, as if continuing a conversation.",
    "text_only": "They ask a question about these fields of their {plural} that is best answered"
                 " in words, with no screen needed (for example what the fields mean, how they"
                 " differ, or how to use them).",
}
HIDDEN = ("kind", "domain", "domain_name", "bank_provenance", "entity", "plural", "layout", "parent", "parent_index", "parent_kind")

_INTENT = re.compile(r"<intent>(.*?)</intent>", re.S)


def spec_for_model(content: dict[str, Any]) -> dict[str, Any]:
    """The spec without bookkeeping keys; the record name and layout go into the prose instead."""
    return {k: v for k, v in content.items() if k not in HIDDEN}


def messages(content: dict[str, Any], style: str, opener: str | None = None) -> list[dict[str, str]]:
    hint = KIND_HINTS[content["kind"]].format(plural=content["plural"])
    layout = f"\n\nLayout: {content['layout']}" if content.get("layout") else ""
    context = f"Context: {content['domain_name']}.\n" if content.get("domain_name") else ""
    user = (
        f"{context}{hint}{layout}\n\nSpec:\n{json.dumps(spec_for_model(content), ensure_ascii=False, indent=1)}"
        f"\n\nStyle of the message itself (how the person writes, not what goes on the screen): {style}."
        + (f" Open the message by {opener}." if opener else "")
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def pick_style(rng: random.Random) -> str:
    return rng.choice(STYLES)


def pick_opener(rng: random.Random) -> str:
    return rng.choice(OPENERS)


def parse(reply: str) -> str | None:
    """The request text, or None when the reply has no single <intent> block."""
    found = _INTENT.findall(reply or "")
    if len(found) != 1 or not found[0].strip():
        return None
    return found[0].strip()
