"""ABN checksum and name normalisation."""

from __future__ import annotations

import re
import unicodedata

_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)


def clean_abn(raw: str | None) -> str | None:
    """Digits only, or None when the input is not exactly 11 digits (spaces allowed)."""
    if raw is None:
        return None
    digits = re.sub(r"\s+", "", raw)
    return digits if re.fullmatch(r"\d{11}", digits) else None


def valid_abn(raw: str | None) -> str | None:
    """The 11-digit ABN if it passes the ATO checksum, else None."""
    digits = clean_abn(raw)
    if digits is None:
        return None
    nums = [int(c) for c in digits]
    nums[0] -= 1
    return digits if sum(w * n for w, n in zip(_WEIGHTS, nums)) % 89 == 0 else None


# "trustee for" and its abbreviation "atf" only: "Public Trustee of Queensland" is a supplier's real name.
_TRUSTEE = re.compile(r"\b(?:(?:as|the)\s+)*(?:trustee\s+for|atf)\b.*$")
_SUFFIX = re.compile(r"(?:^|\s)(?:pty|ltd|limited|inc)$")


def name_norm(name: str | None) -> str:
    """Lower case, unaccented, `&` -> and, `P/L` -> pty ltd, no punctuation, trailing legal suffixes
    and `... trustee for ...` clauses removed. Falls back to keeping the clause, then to no stripping at all,
    if stripping empties it."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"\bp\s*/\s*l\b", " pty ltd ", s)
    s = s.replace("&", " and ")
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"[^\w\s]|_", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _strip_suffixes(_TRUSTEE.sub("", s).strip()) or _strip_suffixes(s) or s


def _strip_suffixes(s: str) -> str:
    while True:
        t = _SUFFIX.sub("", s).strip()
        if t == s:
            return s
        s = t
