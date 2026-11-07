"""Column and table names (SPEC "Loading rules", T1)."""

from __future__ import annotations

import re

MAX_IDENT = 63  # Postgres truncates identifiers longer than this many bytes
_NOT_WORD = re.compile(r"[^a-z0-9]+")


def normalise(raw: object) -> str:
    """Lower snake_case, ASCII letters and digits only; `c_` before a leading digit. Empty if
    nothing is left (the caller picks `column_<n>`)."""
    text = "" if raw is None else str(raw).strip().lower()
    name = _NOT_WORD.sub("_", text).strip("_")
    if name and name[0].isdigit():
        name = f"c_{name}"
    return name[:MAX_IDENT].rstrip("_")


def column_names(headers: list[object]) -> list[str]:
    """One unique name per header: empties become `column_<n>` (1-based), repeats get `_2`, `_3`."""
    out: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(headers, 1):
        base = normalise(raw) or f"column_{i}"
        name, n = base, 2
        while name in seen:
            suffix = f"_{n}"
            name = base[: MAX_IDENT - len(suffix)] + suffix
            n += 1
        seen.add(name)
        out.append(name)
    return out


def header_text(raw: object) -> str | None:
    """The header as written, for COMMENT ON COLUMN; None when it was empty."""
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None
