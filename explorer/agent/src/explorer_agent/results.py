"""ResultStore: the query results of one conversation, referenced by ID from macros."""

from __future__ import annotations

import datetime as dt
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

MAX_RESULTS_PER_CONVERSATION = 50

NUMERIC_TYPES = frozenset(
    {
        "smallint", "integer", "bigint", "numeric", "real", "double precision",
        "int2", "int4", "int8", "float4", "float8", "decimal",
    }
)
INTEGER_TYPES = frozenset({"smallint", "integer", "bigint", "int2", "int4", "int8"})
TEMPORAL_TYPES = frozenset(
    {"date", "timestamp", "timestamptz", "timestamp without time zone", "timestamp with time zone"}
)


def to_json_safe(value: Any) -> Any:
    """A value json.dumps accepts. Arrays and JSON columns are converted all the way down."""
    if isinstance(value, (list, tuple)):
        return [to_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: to_json_safe(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


@dataclass(frozen=True)
class StoredResult:
    id: str
    sql: str
    purpose: str
    columns: list[str]
    column_types: list[str]
    rows: list[dict[str, Any]]
    truncated: bool
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def column_type(self, column: str) -> str:
        return self.column_types[self.columns.index(column)]

    def is_numeric(self, column: str) -> bool:
        return self.column_type(column) in NUMERIC_TYPES

    def is_temporal(self, column: str) -> bool:
        return self.column_type(column) in TEMPORAL_TYPES


class ResultStore:
    """Results in creation order. IDs are r1, r2, … and never reused; the oldest is evicted."""

    def __init__(self, max_results: int = MAX_RESULTS_PER_CONVERSATION):
        self._max = max_results
        self._results: OrderedDict[str, StoredResult] = OrderedDict()
        self._next = 1

    def add(
        self,
        *,
        sql: str,
        purpose: str,
        columns: list[str],
        column_types: list[str],
        rows: list[dict[str, Any]],
        truncated: bool,
    ) -> StoredResult:
        result = StoredResult(
            id=f"r{self._next}",
            sql=sql,
            purpose=purpose,
            columns=list(columns),
            column_types=list(column_types),
            rows=[{k: to_json_safe(v) for k, v in row.items()} for row in rows],
            truncated=truncated,
        )
        self._next += 1
        self._results[result.id] = result
        while len(self._results) > self._max:
            self._results.popitem(last=False)
        return result

    def reserve_id(self) -> str:
        """The next Result ID, for a Result kept outside the store (a Board's, SPEC §18), so the
        Conversation's one counter never issues an ID twice."""
        rid = f"r{self._next}"
        self._next += 1
        return rid

    def readd(self, result: StoredResult) -> StoredResult:
        """Put an evicted Result back under a fresh ID (an Ask on an old Visual, SPEC §15)."""
        return self.add(
            sql=result.sql, purpose=result.purpose, columns=result.columns,
            column_types=result.column_types, rows=result.rows, truncated=result.truncated,
        )

    def get(self, result_id: str) -> StoredResult | None:
        return self._results.get(result_id)

    def ids(self) -> list[str]:
        return list(self._results)
