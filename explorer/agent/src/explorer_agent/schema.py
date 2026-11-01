"""The dataset's schema, read from the database catalog (parent spec §3 and §6 layer 3).

The comments are the data dictionary: they are written in the dataset's migrations, so they are
trusted configuration. `render_schema_section` goes into the cached prompt prefix, so it must
not depend on the data (no row estimates) and must be deterministic.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from psycopg_pool import AsyncConnectionPool

KINDS = {"r": "table", "p": "table", "v": "view", "m": "materialized view", "f": "foreign table"}

_OBJECTS_SQL = """
SELECT n.nspname, c.relname, c.relkind::text, obj_description(c.oid, 'pg_class'), c.reltuples::bigint
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY(%(schemas)s) AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND NOT c.relispartition
  AND (%(name)s::text IS NULL OR n.nspname || '.' || c.relname = %(name)s)
ORDER BY n.nspname COLLATE "C", c.relname COLLATE "C"
"""

_COLUMNS_SQL = """
SELECT n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod), NOT a.attnotnull,
       col_description(c.oid, a.attnum)
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = ANY(%(schemas)s) AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND (%(name)s::text IS NULL OR n.nspname || '.' || c.relname = %(name)s)
ORDER BY n.nspname COLLATE "C", c.relname COLLATE "C", a.attnum
"""


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    comment: str | None


@dataclass(frozen=True)
class TableInfo:
    schema: str
    name: str
    kind: str
    comment: str | None
    est_rows: int | None
    columns: tuple[ColumnInfo, ...]

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


class SchemaCatalog:
    def __init__(self, pool: AsyncConnectionPool, allowed_schemas: Iterable[str]):
        self.pool = pool
        self.allowed_schemas = sorted(allowed_schemas)

    async def objects(self) -> list[TableInfo]:
        return await self._load(None)

    async def describe(self, qualified_name: str) -> TableInfo | None:
        found = await self._load(qualified_name)
        return found[0] if found else None

    async def _load(self, name: str | None) -> list[TableInfo]:
        params = {"schemas": self.allowed_schemas, "name": name}
        async with self.pool.connection() as conn:
            await conn.execute("BEGIN READ ONLY")
            try:
                objects = await (await conn.execute(_OBJECTS_SQL, params)).fetchall()
                columns = await (await conn.execute(_COLUMNS_SQL, params)).fetchall()
            finally:
                await conn.execute("ROLLBACK")
        by_table: dict[tuple[str, str], list[ColumnInfo]] = {}
        for schema, table, col, typ, nullable, comment in columns:
            by_table.setdefault((schema, table), []).append(ColumnInfo(col, typ, nullable, comment))
        return [
            TableInfo(
                schema=schema,
                name=table,
                kind=KINDS[kind],
                comment=comment,
                est_rows=est if est is not None and est >= 0 else None,  # -1: never analysed
                columns=tuple(by_table.get((schema, table), ())),
            )
            for schema, table, kind, comment, est in objects
        ]


def _one_line(text: str) -> str:
    return " ".join(text.split())


def render_schema_section(objects: Iterable[TableInfo]) -> str:
    """Deterministic Markdown for the prompt. Leaves out est_rows, which change with the data."""
    blocks = []
    for t in sorted(objects, key=lambda o: (o.schema, o.name)):
        lines = [f"### {t.qualified_name} ({t.kind})"]
        if t.comment:
            lines.append(_one_line(t.comment))
        for c in t.columns:
            line = f"- {c.name} {c.type}{'' if c.nullable else ' not null'}"
            if c.comment:
                line += f": {_one_line(c.comment)}"
            lines.append(line)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n" if blocks else ""
