"""The model's tools: schemas in OpenAI function format and their dispatch (parent spec §3).

Every result is a JSON-safe dict, and nothing raises: errors are results the model can act on.
Database values in results are untrusted data.
"""

from __future__ import annotations

import difflib
import logging
from typing import Any

from explorer_agent.results import ResultStore
from explorer_agent.schema import SchemaCatalog, TableInfo
from explorer_agent.sql import SqlFail, SqlRunner

log = logging.getLogger(__name__)

PREVIEW_TEXT_LIMIT = 500
CLOSE_MATCHES = 5

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": "List the tables and views you may query, with their descriptions and estimated row counts.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_table",
            "description": "Describe one table or view: its description and its columns (name, type, nullable, description).",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Schema-qualified name, e.g. schema.table"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": (
                "Run one read-only SELECT (or WITH ... SELECT) with schema-qualified table names. "
                "Returns a result_id to reference in the answer, the columns and a preview of the rows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "A single SELECT statement"},
                    "purpose": {"type": "string", "description": "Short label for the query, e.g. 'top 10 by value, 2024'"},
                },
                "required": ["sql", "purpose"],
                "additionalProperties": False,
            },
        },
    },
]


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"error": {"message": message, **extra}}


def _cut(value: Any) -> Any:
    """Cut long text for the model's preview, also inside json objects and arrays."""
    if isinstance(value, str) and len(value) > PREVIEW_TEXT_LIMIT:
        return value[:PREVIEW_TEXT_LIMIT] + "…"
    if isinstance(value, dict):
        return {k: _cut(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_cut(v) for v in value]
    return value


def _describe(t: TableInfo) -> dict[str, Any]:
    return {
        "name": t.qualified_name,
        "kind": t.kind,
        "description": t.comment,
        "columns": [
            {"name": c.name, "type": c.type, "nullable": c.nullable, "description": c.comment} for c in t.columns
        ],
    }


class ToolBox:
    """The tools for one conversation: results land in that conversation's ResultStore."""

    def __init__(self, runner: SqlRunner, catalog: SchemaCatalog, store: ResultStore, rows_to_model: int):
        self.runner = runner
        self.catalog = catalog
        self.store = store
        self.rows_to_model = rows_to_model

    async def call(self, name: str, args: Any) -> dict[str, Any]:
        if args is None or args == "":  # some providers send a no-argument call this way
            args = {}
        if not isinstance(args, dict):
            return _error("tool arguments must be a JSON object")
        try:
            if name == "list_tables":
                return await self.list_tables()
            if name == "describe_table":
                table = args.get("name")
                if not isinstance(table, str) or not table.strip():
                    return _error("describe_table needs a 'name' string, e.g. schema.table")
                return await self.describe_table(table.strip())
            if name == "run_sql":
                sql, purpose = args.get("sql"), args.get("purpose")
                if not isinstance(sql, str) or not sql.strip():
                    return _error("run_sql needs a 'sql' string")
                if not isinstance(purpose, str) or not purpose.strip():
                    return _error("run_sql needs a short 'purpose' string")
                return await self.run_sql(sql, purpose.strip())
        except Exception:  # a tool failure must never end the conversation
            log.exception("tool %s failed", name)
            return _error(f"{name} failed unexpectedly; try again or change the request")
        return _error(f"unknown tool {name!r}; available: list_tables, describe_table, run_sql")

    async def list_tables(self) -> dict[str, Any]:
        return {
            "tables": [
                {"name": t.qualified_name, "kind": t.kind, "description": t.comment, "estimated_rows": t.est_rows}
                for t in await self.catalog.objects()
            ]
        }

    async def describe_table(self, name: str) -> dict[str, Any]:
        found = await self.catalog.describe(name) if "." in name else None
        if found is not None:
            return _describe(found)
        known = [t.qualified_name for t in await self.catalog.objects()]
        matches = difflib.get_close_matches(name, known, n=CLOSE_MATCHES, cutoff=0.5)
        return _error(f"unknown or unavailable table {name!r}", close_matches=matches)

    async def run_sql(self, sql: str, purpose: str) -> dict[str, Any]:
        log.info("run_sql (%s): %s", purpose, sql)
        outcome = await self.runner.run(sql)
        if isinstance(outcome, SqlFail):
            log.info("run_sql failed: %s %s", outcome.sqlstate, outcome.message)
            return {"error": {"sqlstate": outcome.sqlstate, "message": outcome.message, "hint": outcome.hint}}
        result = self.store.add(
            sql=sql,
            purpose=purpose,
            columns=outcome.columns,
            column_types=outcome.column_types,
            rows=outcome.rows,
            truncated=outcome.truncated,
        )
        return {
            "result_id": result.id,
            "columns": result.columns,
            "column_types": result.column_types,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "rows_preview": [{k: _cut(v) for k, v in row.items()} for row in result.rows[: self.rows_to_model]],
            "sql": sql,
            "elapsed_ms": outcome.elapsed_ms,
        }
