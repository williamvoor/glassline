import psycopg
import pytest

from explorer_agent.config import load_dataset
from explorer_agent.prompt import dataset_context
from explorer_agent.schema import SchemaCatalog, render_schema_section

from explorer_bootstrap.report import build_report
from explorer_bootstrap.stats import open_report_pool

from conftest import AUSTENDER_DATASET, write_overlay_dataset

pytestmark = pytest.mark.db

DB = {"host": "h", "name": "d"}


def _obj(report, name):
    return next(o for o in report.objects if o.qualified_name == name)


def _col(obj, name):
    return next(c for c in obj.columns if c.name == name)


async def test_finds_every_missing_comment(ro_pool):
    report = await build_report(ro_pool, ["bs"], database=DB, minmax=False)
    assert sorted(report.missing_comments()) == sorted(
        ["bs.mixed.name", "bs.mixed.created", "bs.fresh", "bs.fresh.x", "bs.slow.n"]
    )
    s = report.summary
    assert (s.objects, s.columns) == (3, 8)
    assert (s.objects_with_comment, s.columns_with_comment) == (2, 4)
    assert s.coverage_pct == round(100 * 6 / 11, 1)


async def test_austender_full_coverage(ro_pool):
    report = await build_report(ro_pool, ["mart"], database=DB, minmax=False)
    assert report.objects
    assert report.missing_comments() == []
    assert report.summary.coverage_pct == 100.0
    # B3: no overlay, so every comment is the database's
    assert report.comment_overlay is None
    assert (report.summary.overlay_comments, report.summary.overlay_replacements) == (0, 0)
    for o in report.objects:
        assert (o.comment_source, o.database_comment) == ("database", None)
        assert all((c.comment_source, c.database_comment) == ("database", None) for c in o.columns)


async def test_context_size_matches_agent(ro_pool):
    dataset = load_dataset(AUSTENDER_DATASET)
    report = await build_report(ro_pool, dataset.allowed_schemas, database=DB, dataset=dataset, minmax=False)
    # The same calls the agent makes at startup (explorer_agent.__main__._serve).
    section = render_schema_section(await SchemaCatalog(ro_pool, dataset.allowed_schemas).objects())
    assert report.summary.context_chars == len(dataset_context(dataset, section))
    assert report.summary.context_scope == "full"
    assert report.dataset == dataset.name
    # B3 with a Dataset that has no [comments]
    assert dataset.comment_overlay is None and report.comment_overlay is None
    assert (report.summary.overlay_comments, report.summary.overlay_replacements) == (0, 0)
    assert {o.comment_source for o in report.objects} == {"database"}


async def test_schema_only_context(ro_pool):
    report = await build_report(ro_pool, ["mart"], database=DB, minmax=False)
    section = render_schema_section(await SchemaCatalog(ro_pool, ["mart"]).objects())
    assert report.summary.context_chars == len(section)
    assert report.summary.context_scope == "schema_only"


async def test_unanalysed_table_and_view(ro_pool):
    report = await build_report(ro_pool, ["bs"], database=DB, minmax=False)
    fresh = _obj(report, "bs.fresh")
    assert fresh.analysed is False
    assert fresh.est_rows is None
    x = _col(fresh, "x")
    assert (x.null_frac, x.distinct_estimate, x.most_common) == (None, None, [])
    slow = _obj(report, "bs.slow")
    assert slow.analysed is None and slow.est_rows is None
    assert _obj(report, "bs.mixed").analysed is True


async def test_column_statistics(ro_pool):
    report = await build_report(ro_pool, ["bs"], database=DB, minmax=False)
    mixed = _obj(report, "bs.mixed")
    assert mixed.est_rows == 100
    name = _col(mixed, "name")
    assert name.null_frac == pytest.approx(0.1)
    assert name.distinct_estimate == 2
    assert name.most_common == [{"value": "a", "freq": pytest.approx(0.6)}, {"value": "b", "freq": pytest.approx(0.3)}]
    assert _col(mixed, "id").distinct_estimate == 100  # n_distinct -1: all rows distinct
    grade = _col(mixed, "grade").most_common  # integer column: 7 frequent values, capped at 5
    assert [m["value"] for m in grade] == ["1", "2", "3", "4", "5"]
    assert [m["freq"] for m in grade] == pytest.approx([0.30, 0.20, 0.15, 0.12, 0.10])


async def test_non_finite_values_become_null(ro_pool):
    report = await build_report(ro_pool, ["bs"], database=DB)
    ratio = _col(_obj(report, "bs.mixed"), "ratio")
    assert (ratio.min, ratio.max, ratio.range_status) == (0.25, None, "ok")  # NaN sorts highest


async def test_min_max(ro_pool):
    report = await build_report(ro_pool, ["bs"], database=DB)
    mixed = _obj(report, "bs.mixed")
    assert (_col(mixed, "id").min, _col(mixed, "id").max) == (1, 100)
    assert (_col(mixed, "amount").min, _col(mixed, "amount").max) == (1.5, 150.0)
    created = _col(mixed, "created")
    assert (created.min, created.max, created.range_status) == ("2024-01-02", "2024-04-10", "ok")
    assert _col(mixed, "name").range_status == "n/a"
    off = await build_report(ro_pool, ["bs"], database=DB, minmax=False)
    assert _col(_obj(off, "bs.mixed"), "id").range_status == "off"


async def test_slow_range_is_skipped(pg):
    pool = open_report_pool(pg.ro_dsn, 1000)  # the view sleeps 3 s; catalog queries take milliseconds
    await pool.open(wait=True)
    try:
        report = await build_report(pool, ["bs"], database=DB, timeout_ms=1000)
    finally:
        await pool.close()
    n = _col(_obj(report, "bs.slow"), "n")
    assert (n.range_status, n.min, n.max) == ("skipped", None, None)
    assert _col(_obj(report, "bs.mixed"), "id").range_status == "ok"


async def test_can_comment_by_login(ro_pool, loader_pool):
    ro = await build_report(ro_pool, ["mart"], database=DB, minmax=False)
    owner = await build_report(loader_pool, ["mart"], database=DB, minmax=False)
    assert not any(o.can_comment for o in ro.objects) and ro.summary.can_comment is False
    assert all(o.can_comment for o in owner.objects) and owner.summary.can_comment is True


async def test_pool_is_read_only_with_timeout(pg):
    pool = open_report_pool(pg.loader_dsn, 1234)  # the owner: not read-only by default
    await pool.open(wait=True)
    try:
        async with pool.connection() as conn:
            assert (await (await conn.execute("SHOW statement_timeout")).fetchone())[0] == "1234ms"
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                await conn.execute("CREATE TABLE mart.should_not_exist (x int)")
    finally:
        await pool.close()


async def test_empty_schema_is_listed(ro_pool):
    report = await build_report(ro_pool, ["bs", "nope"], database=DB, minmax=False)
    assert report.empty_schemas == ["nope"]


# Phase 2a: the Comment overlay
def _sources(obj):
    return (obj.comment_source, obj.database_comment)


# B1
async def test_overlay_sources(ro_pool, tmp_path):
    dataset = load_dataset(write_overlay_dataset(tmp_path))
    report = await build_report(ro_pool, dataset.allowed_schemas, database=DB, dataset=dataset, minmax=False)
    assert report.comment_overlay == "comments.toml"
    mixed, fresh, slow = _obj(report, "bs.mixed"), _obj(report, "bs.fresh"), _obj(report, "bs.slow")
    assert mixed.comment == "OVERLAY mixed"
    assert _sources(mixed) == ("overlay", "A table with some comments.")  # replaced
    assert _sources(fresh) == ("overlay", None)  # filled
    assert _sources(slow) == ("database", None)  # untouched
    assert _sources(_col(mixed, "id")) == ("overlay", "Row id.")
    assert _sources(_col(mixed, "name")) == ("overlay", None)
    assert _sources(_col(mixed, "amount")) == ("database", None)
    assert _sources(_col(mixed, "created")) == (None, None)
    assert _sources(_col(slow, "n")) == ("overlay", None)  # a view's column
    assert sorted(report.missing_comments()) == ["bs.fresh.x", "bs.mixed.created"]
    s = report.summary
    assert (s.overlay_comments, s.overlay_replacements) == (5, 2)
    assert (s.objects_with_comment, s.columns_with_comment) == (3, 6)


# B2
async def test_context_size_matches_agent_with_overlay(ro_pool, tmp_path):
    dataset = load_dataset(write_overlay_dataset(tmp_path))
    report = await build_report(ro_pool, dataset.allowed_schemas, database=DB, dataset=dataset, minmax=False)
    # The same calls the agent makes at startup, with the overlay.
    catalog = SchemaCatalog(ro_pool, dataset.allowed_schemas, dataset.comment_overlay)
    section = render_schema_section(await catalog.objects())
    assert "OVERLAY mixed" in section
    assert report.summary.context_chars == len(dataset_context(dataset, section))
