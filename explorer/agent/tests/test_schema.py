import dataclasses
from pathlib import Path

import psycopg
import pytest
from austender import mart

from explorer_agent.config import CommentOverlay, OverlayEntry
from explorer_agent.schema import (
    ColumnInfo,
    SchemaCatalog,
    TableInfo,
    apply_overlay,
    overlay_problems,
    render_schema_section,
)

pytestmark = pytest.mark.db

MART_TABLES = sorted(mart.MART_TABLES)


# D12
async def test_schema_section_lists_every_mart_table_with_comments(ro_pool):
    objects = await SchemaCatalog(ro_pool, ["mart"]).objects()
    assert [o.qualified_name for o in objects] == [f"mart.{t}" for t in MART_TABLES]
    assert all(o.comment for o in objects)
    assert all(c.comment for o in objects for c in o.columns)

    section = render_schema_section(objects)
    headers = [line for line in section.splitlines() if line.startswith("### ")]
    assert headers == [f"### mart.{t} (table)" for t in MART_TABLES]
    assert "- notice_id text not null: " in section
    for schema in ("raw.", "core.", "ops."):
        assert f"### {schema}" not in section
    assert "None" not in section
    assert section.endswith("\n") and not section.endswith("\n\n")


async def test_schema_section_has_no_row_counts(ro_pool):
    objects = await SchemaCatalog(ro_pool, ["mart"]).objects()
    assert any(o.est_rows for o in objects)  # the estimates are read ...
    changed = [dataclasses.replace(o, est_rows=(o.est_rows or 0) * 7 + 12345) for o in objects]
    assert render_schema_section(changed) == render_schema_section(objects)  # ... but never rendered


# D13
async def test_schema_section_does_not_depend_on_the_data(pg, ro_pool):
    catalog = SchemaCatalog(ro_pool, ["mart"])
    before = render_schema_section(await catalog.objects())
    with psycopg.connect(pg.loader_dsn) as conn:
        mart.rebuild(conn)
        conn.commit()
    with psycopg.connect(pg.loader_dsn, autocommit=True) as conn:
        conn.execute("ANALYZE")
    assert render_schema_section(await catalog.objects()) == before


async def test_describe_unknown_is_none(ro_pool):
    catalog = SchemaCatalog(ro_pool, ["mart"])
    assert await catalog.describe("mart.nope") is None
    assert await catalog.describe("raw.release") is None


# Comment overlay (SPEC §13)
def _t(name, comment, cols):
    schema, table = name.split(".")
    return TableInfo(schema, table, "table", comment, None, tuple(ColumnInfo(c, "text", True, cc) for c, cc in cols))


OBJECTS = [
    _t("s.a", "A from db", [("x", "x from db"), ("y", None), ("z", "z from db")]),
    _t("s.b", None, [("x", None)]),
    _t("s.c", "C from db", [("x", "cx from db")]),
]


def _overlay(entries):
    return CommentOverlay(Path("comments.toml"), {k: OverlayEntry(c, cols) for k, (c, cols) in entries.items()})


# O4
def test_apply_overlay():
    overlay = _overlay({"s.a": (None, {"x": "x over", "y": "y over"}), "s.b": ("B over", {"x": "bx over"})})
    out = apply_overlay(OBJECTS, overlay)
    assert [t.qualified_name for t in out] == ["s.a", "s.b", "s.c"]
    a, b, c = out
    assert a.comment == "A from db"  # no entry comment: database comment kept
    assert [col.comment for col in a.columns] == ["x over", "y over", "z from db"]
    assert b.comment == "B over" and b.columns[0].comment == "bx over"  # added where the db had none
    assert c == OBJECTS[2]  # untouched
    assert apply_overlay(OBJECTS, None) == OBJECTS


# O5
def test_overlay_problems():
    good = _overlay({"s.a": ("A", {"x": "x"}), "s.c": (None, {"x": "x"})})
    assert overlay_problems(OBJECTS, good) == []
    assert overlay_problems(OBJECTS, None) == []
    bad = _overlay({"s.zz": ("Z", {}), "s.a": (None, {"x": "ok", "nope": "n", "gone": "g"})})
    assert overlay_problems(OBJECTS, bad) == ["unknown column s.a.gone", "unknown column s.a.nope", "unknown table s.zz"]


# O6
async def test_catalog_applies_overlay(ro_pool):
    plain = SchemaCatalog(ro_pool, ["mart"])
    overlay = _overlay({"mart.contract": ("OVERLAY contract", {"notice_id": "OVERLAY notice"})})
    over = SchemaCatalog(ro_pool, ["mart"], overlay)
    before, after = await plain.objects(), await over.objects()
    assert overlay_problems(after, overlay) == []
    changed = [(o, n) for o, n in zip(before, after, strict=True) if o != n]
    assert [n.qualified_name for _, n in changed] == ["mart.contract"]
    (old, new), = changed
    assert new.comment == "OVERLAY contract" and old.comment != new.comment
    diff = [(oc.name, nc.comment) for oc, nc in zip(old.columns, new.columns, strict=True) if oc != nc]
    assert diff == [("notice_id", "OVERLAY notice")]
    section = render_schema_section(after)
    assert "### mart.contract (table)\nOVERLAY contract\n" in section
    assert "- notice_id text not null: OVERLAY notice\n" in section
    described = await over.describe("mart.contract")
    assert described == new
