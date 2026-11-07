"""One load: read and infer outside the transaction, then replace the tables, run the transforms
and grant, all in one transaction (SPEC "Loading rules", L5, T4, T5, T11)."""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg import sql

import csv

from dataload.infer import infer, to_copy
from dataload.manifest import TYPES, Manifest, ManifestError
from dataload.names import column_names, header_text, normalise
from dataload.readers import ReadError, Table, tables_for


class LoadError(RuntimeError):
    """A load that failed and was rolled back. The message never holds a login."""


# What a file can raise while it is read: bad CSV, bad bytes, a bad encoding, a number too long.
FILE_ERRORS = (ReadError, csv.Error, UnicodeDecodeError, LookupError, ValueError, OSError)


def _file_error(label: str, e: Exception) -> LoadError:
    if isinstance(e, ReadError):
        return LoadError(str(e))
    if isinstance(e, UnicodeDecodeError):
        return LoadError(f"{label}: not valid {e.encoding} at byte {e.start}")
    if isinstance(e, LookupError):
        return LoadError(f"{label}: unknown encoding ({e})")
    return LoadError(f"{label}: can't be read ({type(e).__name__}: {e})")


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    header: str | None


@dataclass(frozen=True)
class Planned:
    table: Table
    name: str
    columns: tuple[Column, ...]
    nulls: frozenset[str]
    thousands: str | None


@dataclass(frozen=True)
class TableResult:
    schema: str
    table: str
    rows: int
    source: str


def plan(manifest: Manifest) -> list[Planned]:
    """Every table to load, with its columns and types. Reads each file once; no database."""
    out: list[Planned] = []
    taken: set[str] = set()
    for source in manifest.sources:
        try:
            tables = tables_for(source, manifest.base_dir)
        except ManifestError as e:
            raise LoadError(str(e)) from None
        except FILE_ERRORS as e:
            raise _file_error(source.file, e) from None
        if source.table and len(tables) > 1:
            raise LoadError(f"{source.file}: table applies to one file or sheet, and this source has {len(tables)}")
        bad = sorted(c for c, ty in source.types.items() if ty not in TYPES)
        if bad:
            raise LoadError(f"{source.file}: types.{bad[0]} must be one of {', '.join(TYPES)}")
        for t in tables:
            name = source.table if source.table and len(tables) == 1 else normalise(t.default_name)
            if not name:
                raise LoadError(f"{t.label}: can't make a table name from {t.default_name!r}; set table")
            if name in taken:
                raise LoadError(f"{t.label}: a second table named {name}; set table")
            taken.add(name)
            names = column_names(t.headers)
            nulls = frozenset(source.null_values)
            try:
                types = t.arrow_types or infer(t.rows(), len(names), nulls, source.thousands)
            except FILE_ERRORS as e:
                raise _file_error(t.label, e) from None
            unknown = sorted(set(source.types) - set(names))
            if unknown:
                raise LoadError(f"{t.label}: types names no column {unknown[0]} (columns: {', '.join(names)})")
            types = [source.types.get(n, ty) for n, ty in zip(names, types, strict=True)]
            columns = tuple(Column(n, ty, header_text(h)) for n, ty, h in zip(names, types, t.headers, strict=True))
            out.append(Planned(t, name, columns, frozenset() if t.arrow_types else nulls, source.thousands))
    return out


def _create(conn: psycopg.Connection, schema: str, p: Planned) -> int:
    target = sql.Identifier(schema, p.name)
    conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(target))
    cols = sql.SQL(", ").join(sql.SQL("{} {}").format(sql.Identifier(c.name), sql.SQL(c.type)) for c in p.columns)
    conn.execute(sql.SQL("CREATE TABLE {} ({})").format(target, cols))
    names = sql.SQL(", ").join(sql.Identifier(c.name) for c in p.columns)
    count = 0
    with conn.cursor() as cur, cur.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(target, names)) as copy:
        for row in p.table.rows():
            copy.write_row([to_copy(v, c.type, p.nulls, p.thousands) for v, c in zip(row, p.columns, strict=True)])
            count += 1
    conn.execute(sql.SQL("COMMENT ON TABLE {} IS {}").format(target, sql.Literal(f"Loaded from {p.table.label} by dataload")))
    for c in p.columns:
        if c.header is not None:
            conn.execute(sql.SQL("COMMENT ON COLUMN {}.{} IS {}").format(target, sql.Identifier(c.name), sql.Literal(c.header)))
    return count


def grant_read(conn: psycopg.Connection, role: str, schemas: tuple[str, ...]) -> None:
    for s in schemas:
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(s), sql.Identifier(role)))
        conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(sql.Identifier(s), sql.Identifier(role)))


def run(conn: psycopg.Connection, manifest: Manifest, *, role: str | None = None) -> list[TableResult]:
    """Load the manifest through `conn` (autocommit or idle). `role`: SET LOCAL ROLE first, so the
    tables belong to it (a superuser loading on behalf of the owner, as tests do)."""
    planned = plan(manifest)
    try:
        transforms = manifest.transform_files() if manifest.transforms else []
    except ManifestError as e:
        raise LoadError(str(e)) from None
    results: list[TableResult] = []
    step = "starting"
    try:
        with conn.transaction():
            if role:
                conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(manifest.staging_schema)))
            for p in planned:
                step = f"loading {p.table.label}"
                rows = _create(conn, manifest.staging_schema, p)
                results.append(TableResult(manifest.staging_schema, p.name, rows, p.table.label))
            for path in transforms:
                step = f"transform {path.name}"
                conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
            if manifest.readonly_role and manifest.grant_schemas:
                step = "granting read access"
                grant_read(conn, manifest.readonly_role, manifest.grant_schemas)
    except psycopg.Error as e:
        detail = e.diag.message_primary or type(e).__name__
        raise LoadError(f"{step}: {detail} (nothing was changed)") from None
    except FILE_ERRORS as e:
        raise LoadError(f"{_file_error(step, e)} (nothing was changed)") from None
    return results
