"""Type inference (T2): the first type in ORDER that fits every non-null value of a column."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from decimal import Decimal

from dataload.readers import Cell

ORDER = ("boolean", "bigint", "numeric", "date", "timestamp", "text")
BOOLEANS = {"true", "false", "yes", "no"}
_INT = re.compile(r"[+-]?\d{1,4000}", re.ASCII)
_NUM = re.compile(r"[+-]?(\d{1,4000}(\.\d*)?|\.\d+)([eE][+-]?\d+)?", re.ASCII)
_LEADING_ZERO = re.compile(r"[+-]?0\d", re.ASCII)  # "0800": a code, not a number (its zero would be lost)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
# No offset: a `timestamp` column would drop it, so a string with one stays text.
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?", re.ASCII)
BIGINT_MAX = 2**63 - 1


def _strip_thousands(text: str, thousands: str | None) -> str:
    return text.replace(thousands, "") if thousands else text


def fits(value: Cell, thousands: str | None = None) -> set[str]:
    """The types this one value fits (text always fits, so it isn't listed)."""
    if isinstance(value, bool):
        return {"boolean"}
    if isinstance(value, int):
        return {"bigint", "numeric"} if abs(value) <= BIGINT_MAX else {"numeric"}
    if isinstance(value, (float, Decimal)):
        return {"numeric"} if value == value and value not in (float("inf"), float("-inf")) else set()
    if isinstance(value, dt.datetime):
        return {"date", "timestamp"} if value.time() == dt.time(0) and value.tzinfo is None else {"timestamp"}
    if isinstance(value, dt.date):
        return {"date", "timestamp"}
    text = str(value)
    if text.lower() in BOOLEANS:
        return {"boolean"}
    number = _strip_thousands(text, thousands)
    if _LEADING_ZERO.match(number):
        return set()
    if _INT.fullmatch(number):
        return {"bigint", "numeric"} if abs(int(number)) <= BIGINT_MAX else {"numeric"}
    if _NUM.fullmatch(number):
        return {"numeric"}
    if _DATE.fullmatch(text):
        try:
            dt.date.fromisoformat(text)
        except ValueError:
            return set()
        return {"date", "timestamp"}
    if _STAMP.fullmatch(text):
        try:
            dt.datetime.fromisoformat(text)
        except ValueError:
            return set()
        return {"timestamp"}
    return set()


def is_null(value: Cell, nulls: frozenset[str]) -> bool:
    return value is None or (isinstance(value, str) and value in nulls)


def infer(rows: Iterable[list[Cell]], width: int, nulls: frozenset[str], thousands: str | None = None) -> list[str]:
    candidates: list[set[str] | None] = [None] * width  # None: no value seen yet
    for row in rows:
        for i, value in enumerate(row):
            if is_null(value, nulls) or candidates[i] == set():
                continue
            f = fits(value, thousands)
            candidates[i] = f if candidates[i] is None else candidates[i] & f  # type: ignore[operator]
    out = []
    for c in candidates:
        out.append("text" if not c else next(t for t in ORDER if t in c))
    return out


def to_copy(value: Cell, typ: str, nulls: frozenset[str], thousands: str | None) -> str | None:
    """The value as COPY text for its column type; None for NULL."""
    if is_null(value, nulls):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if typ == "date" else value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value)
    if typ in ("bigint", "numeric") and isinstance(value, str):
        return _strip_thousands(text, thousands)
    return text
