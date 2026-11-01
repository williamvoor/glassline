import dataclasses

import psycopg
import pytest
from austender import mart

from explorer_agent.schema import SchemaCatalog, render_schema_section

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
