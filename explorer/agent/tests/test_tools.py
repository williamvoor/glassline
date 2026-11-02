from decimal import Decimal
from pathlib import Path

from austender import mart

import pytest

from explorer_agent.config import CommentOverlay, OverlayEntry
from explorer_agent.results import ResultStore
from explorer_agent.schema import SchemaCatalog
from explorer_agent.sql import SqlRunner
from explorer_agent.tools import PREVIEW_TEXT_LIMIT, TOOL_SCHEMAS, ToolBox

from .conftest import load_seed_expected

E = load_seed_expected()

ACCEPTANCE_SQL = """
    WITH top AS (
        SELECT supplier_key FROM mart.supplier_fy
        WHERE financial_year = '2024-25' ORDER BY total_value DESC LIMIT 10)
    SELECT s.supplier_key, s.agency_key, s.total_value
    FROM mart.supplier_agency_fy s JOIN top USING (supplier_key)
    WHERE s.financial_year = '2024-25'
    ORDER BY s.supplier_key, s.agency_key"""


@pytest.fixture
def toolbox(ro_pool):
    return ToolBox(
        SqlRunner(ro_pool, ("mart",), 5000, 100),
        SchemaCatalog(ro_pool, ["mart"]),
        ResultStore(),
        rows_to_model=3,
    )


def test_tool_schemas_are_generic():
    assert [t["function"]["name"] for t in TOOL_SCHEMAS] == ["list_tables", "describe_table", "run_sql"]
    text = str(TOOL_SCHEMAS).lower()
    for word in ("austender", "mart.", "supplier", "agency", "contract"):
        assert word not in text


# D14
@pytest.mark.db
async def test_list_tables_matches_schema(toolbox):
    out = await toolbox.call("list_tables", {})
    names = [t["name"] for t in out["tables"]]
    assert names == [f"mart.{t}" for t in sorted(mart.MART_TABLES)]
    assert all(t["description"] and t["kind"] == "table" for t in out["tables"])
    assert all(isinstance(t["estimated_rows"], int | None) for t in out["tables"])


@pytest.mark.db
async def test_describe_table(toolbox):
    out = await toolbox.call("describe_table", {"name": "mart.contract"})
    assert out["name"] == "mart.contract"
    cols = {c["name"]: c for c in out["columns"]}
    assert cols["notice_id"]["nullable"] is False
    assert cols["notice_id"]["description"]


@pytest.mark.db
async def test_describe_table_typo_suggests_close_matches(toolbox):
    out = await toolbox.call("describe_table", {"name": "mart.contrat"})
    assert "mart.contract" in out["error"]["close_matches"]


@pytest.mark.db
@pytest.mark.parametrize("name", ["raw.release", "contract", "core.contract"])
async def test_describe_table_outside_allowed_is_an_error(toolbox, name):
    out = await toolbox.call("describe_table", {"name": name})
    assert "error" in out
    assert all(m.startswith("mart.") for m in out["error"]["close_matches"])


# D15
@pytest.mark.db
async def test_run_sql_stores_results_and_previews(toolbox):
    out = await toolbox.call("run_sql", {"sql": "SELECT notice_id FROM mart.contract ORDER BY 1", "purpose": "all"})
    assert out["result_id"] == "r1"
    assert out["row_count"] == E.MART_ROWS["contract"]
    assert len(out["rows_preview"]) == 3
    assert out["column_types"] == ["text"]
    assert toolbox.store.get("r1").row_count == E.MART_ROWS["contract"]

    again = await toolbox.call("run_sql", {"sql": "SELECT 1 AS one", "purpose": "one"})
    assert again["result_id"] == "r2"


@pytest.mark.db
async def test_run_sql_cuts_long_text_in_preview_only(toolbox):
    out = await toolbox.call("run_sql", {"sql": "SELECT repeat('x', 600) AS long", "purpose": "long"})
    preview = out["rows_preview"][0]["long"]
    assert len(preview) == PREVIEW_TEXT_LIMIT + 1 and preview.endswith("…")
    assert len(toolbox.store.get(out["result_id"]).rows[0]["long"]) == 600


@pytest.mark.db
async def test_run_sql_failure_is_reported_and_not_stored(toolbox):
    out = await toolbox.call("run_sql", {"sql": "SELECT valu FROM mart.contract", "purpose": "typo"})
    assert out["error"]["sqlstate"] == "42703"
    assert "valu" in out["error"]["message"]
    assert "hint" in out["error"]
    assert toolbox.store.ids() == []


# D16
@pytest.mark.db
async def test_acceptance_query_gives_seed_totals(toolbox):
    out = await toolbox.call("run_sql", {"sql": ACCEPTANCE_SQL, "purpose": "top suppliers FY2024-25 by agency"})
    rows = toolbox.store.get(out["result_id"]).rows
    assert {r["supplier_key"] for r in rows} == set(E.TOP_SUPPLIERS_FY2425)
    total = sum(Decimal(str(r["total_value"])) for r in rows)
    assert total == E.FY_TOTALS["2024-25"]
    got = {(r["supplier_key"], r["agency_key"]): Decimal(str(r["total_value"])) for r in rows}
    want = {(s, a): v for (s, a, fy), (_, v) in E.SUPPLIER_AGENCY_FY.items() if fy == "2024-25"}
    assert got == want


# D17
@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("drop_tables", {}),
        ("run_sql", {"purpose": "x"}),
        ("run_sql", {"sql": "SELECT 1"}),
        ("describe_table", {}),
        ("run_sql", "not a dict"),
        ("run_sql", None),
    ],
)
async def test_bad_calls_return_errors(name, args):
    box = ToolBox(runner=None, catalog=None, store=ResultStore(), rows_to_model=3)
    out = await box.call(name, args)
    assert "error" in out


# Regression (refuter): providers send a no-argument call as None or "".
@pytest.mark.db
@pytest.mark.parametrize("args", [None, "", {}])
async def test_list_tables_accepts_empty_arguments(toolbox, args):
    assert "tables" in await toolbox.call("list_tables", args)


# Regression (refuter): text nested in json or arrays is cut in the preview too.
@pytest.mark.db
async def test_preview_cuts_nested_text(toolbox):
    out = await toolbox.call(
        "run_sql",
        {"sql": "SELECT jsonb_build_object('k', repeat('x', 600)) AS j, ARRAY[repeat('y', 600)] AS a", "purpose": "nested"},
    )
    row = out["rows_preview"][0]
    assert len(row["j"]["k"]) == PREVIEW_TEXT_LIMIT + 1
    assert len(row["a"][0]) == PREVIEW_TEXT_LIMIT + 1


# O6: the overlay reaches list_tables and describe_table
@pytest.mark.db
async def test_tools_see_overlay(ro_pool):
    overlay = CommentOverlay(
        Path("comments.toml"), {"mart.contract": OverlayEntry("OVERLAY contract", {"notice_id": "OVERLAY notice"})}
    )
    box = ToolBox(SqlRunner(ro_pool, ("mart",), 5000, 100), SchemaCatalog(ro_pool, ["mart"], overlay), ResultStore(), 3)
    listed = {t["name"]: t["description"] for t in (await box.call("list_tables", {}))["tables"]}
    assert listed["mart.contract"] == "OVERLAY contract"
    assert listed["mart.agency"] and listed["mart.agency"] != "OVERLAY contract"
    out = await box.call("describe_table", {"name": "mart.contract"})
    assert out["description"] == "OVERLAY contract"
    cols = {c["name"]: c["description"] for c in out["columns"]}
    assert cols["notice_id"] == "OVERLAY notice"
    assert sum(1 for d in cols.values() if d and d.startswith("OVERLAY")) == 1
