"""core and mart built from the seed, asserted against tests/seed/expected.py (worked out by hand)."""

from datetime import date
from decimal import Decimal as D

import expected as E
import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from austender import mart
from austender.testing import build_test_db


def rows(conn, query, params=()):
    return conn.execute(query, params).fetchall()


# ---- raw / ops ---------------------------------------------------------------------------------


def test_raw_counts_duplicates_and_rejections(conn):
    assert rows(conn, "SELECT count(*) FROM raw.release")[0][0] == E.RAW_RELEASES
    rej = rows(conn, "SELECT release_id, existing_sha256 <> new_sha256 FROM ops.rejected_release")
    assert len(rej) == E.REJECTED and rej[0][1] is True
    # the stored row for the conflicting release id is the original (25,000), not the 99,999 payload
    (amount,) = rows(
        conn, "SELECT payload #>> '{contracts,0,value,amount}' FROM raw.release WHERE release_id = %s", (rej[0][0],)
    )[0]
    assert amount == "25000.00"


# ---- core --------------------------------------------------------------------------------------


def test_core_contracts(conn):
    got = {
        r[0]: r[1:]
        for r in rows(
            conn,
            "SELECT notice_id, publish_date, financial_year, agency_key, original_value, current_value,"
            " amendment_count, cancelled_amendment_count, is_cancelled FROM core.contract",
        )
    }
    assert set(got) == set(E.CONTRACTS)
    for cn, (pd, fy, agency, orig, cur, n, nc, _nsup, cancelled) in E.CONTRACTS.items():
        assert got[cn] == (date.fromisoformat(pd), fy, agency, orig, cur, n, nc, cancelled), cn


def test_core_amendments_are_snapshots_with_deltas(conn):
    got = {
        (r[0], r[1]): r[2:]
        for r in rows(conn, "SELECT notice_id, seq, value, value_delta, is_cancelled FROM core.amendment")
    }
    assert got == E.AMENDMENTS


def test_core_has_no_amendment_date_column(conn):
    cols = {r[0] for r in rows(conn, "SELECT column_name FROM information_schema.columns WHERE table_schema='core' AND table_name='amendment'")}
    assert not {c for c in cols if "date" in c or c.endswith("_ts")}


def test_core_suppliers(conn):
    got = {r[0]: r[1:] for r in rows(conn, "SELECT supplier_key, name, abn, is_overseas FROM core.supplier")}
    assert set(got) == set(E.SUPPLIER_NAMES)
    for key, name in E.SUPPLIER_NAMES.items():
        # S1: the majority spelling, not its latest notice's; S7: a three-way tie, so the latest notice's
        assert got[key] == (name, E.SUPPLIER_ABN[key], key == E.S6), key
    (raw_abn,) = rows(conn, "SELECT abn_raw FROM core.contract_supplier WHERE supplier_key = %s", (E.S5,))[0]
    assert raw_abn == "12345678901"  # abn_raw kept even though the checksum fails


def test_core_agency_uses_latest_name(conn):
    got = dict(rows(conn, "SELECT agency_key, name FROM core.agency"))
    assert got == {E.A1: E.A1_NAME, E.A2: E.A2_NAME}


def test_supplier_count_per_contract(conn):
    got = dict(rows(conn, "SELECT notice_id, count(*) FROM core.contract_supplier GROUP BY 1"))
    for cn, spec in E.CONTRACTS.items():
        assert got.get(cn, 0) == spec[7], cn


# ---- mart --------------------------------------------------------------------------------------


def test_mart_row_counts(conn):
    for table, n in E.MART_ROWS.items():
        assert rows(conn, f"SELECT count(*) FROM mart.{table}")[0][0] == n, table


def test_mart_contract_includes_cancelled_with_flag(conn):
    got = {r[0]: r[1:] for r in rows(conn, "SELECT notice_id, is_cancelled, current_value, value_change, supplier_count, amendment_count, cancelled_amendment_count FROM mart.contract")}
    assert got["CN9000002"][0] is True and got["CN9000002"][1] == D("500000")
    assert got["CN9000007"] == (False, D("90000"), D("30000"), 1, 3, 1)
    assert got["CN9000006"] == (False, D("40000"), D("-30000"), 1, 2, 0)  # value can go down; A2 has no value
    assert got["CN9000019"][3] == 0  # no supplier published
    assert got["CN9000020"][1:3] == (None, None)  # no value published
    assert got["CN9000008"] == (False, D("15000"), D("5000"), 1, 2, 1)  # highest k cancelled: ignored
    assert got["CN9000004"][3] == 3


def test_mart_contract_period_and_agency_name(conn):
    got = dict(rows(conn, "SELECT notice_id, period_start FROM mart.contract"))
    for cn, start in E.PERIOD_START.items():
        assert got[cn] == date.fromisoformat(start), cn
    got = dict(rows(conn, "SELECT notice_id, period_end FROM mart.contract"))
    for cn, end in E.PERIOD_END.items():
        assert got[cn] == date.fromisoformat(end), cn
    names = {r[0] for r in rows(conn, "SELECT DISTINCT agency_name FROM mart.contract WHERE agency_key = %s", (E.A2,))}
    assert names == {E.A2_NAME}


def test_fy_boundary_in_canberra_time(conn):
    got = dict(rows(conn, "SELECT notice_id, financial_year FROM mart.contract WHERE notice_id IN ('CN9000013','CN9000014')"))
    assert got == {"CN9000013": "2025-26", "CN9000014": "2024-25"}


def test_unspsc_prefixes(conn):
    assert rows(conn, "SELECT unspsc, unspsc_segment, unspsc_family, unspsc_class FROM mart.contract WHERE notice_id='CN9000003'") == [
        ("80100000", "80", "8010", "801000")
    ]


def test_contract_supplier_excludes_cancelled_and_splits_value(conn):
    assert rows(conn, "SELECT count(*) FROM mart.contract_supplier")[0][0] == E.CONTRACT_SUPPLIER_ROWS
    assert rows(conn, "SELECT count(*) FROM mart.contract_supplier WHERE notice_id = 'CN9000002'")[0][0] == 0
    shares = {r[0]: r[1] for r in rows(conn, "SELECT notice_id, value_share FROM mart.contract_supplier WHERE notice_id IN ('CN9000003','CN9000004') ORDER BY ordinal")}
    assert shares == {"CN9000003": D("100000"), "CN9000004": D("100000")}
    # contracts with no supplier or no value have no rows here
    assert rows(conn, "SELECT count(*) FROM mart.contract_supplier WHERE notice_id IN ('CN9000019','CN9000020')")[0][0] == 0
    # summing value_share never double counts: per-contract sums equal the contract's current value
    bad = rows(
        conn,
        "SELECT cs.notice_id FROM mart.contract_supplier cs JOIN mart.contract c USING (notice_id)"
        " GROUP BY cs.notice_id, c.current_value HAVING abs(sum(cs.value_share) - c.current_value) > 0.000001",
    )
    assert bad == []
    for fy, total in E.FY_TOTALS.items():
        assert rows(conn, "SELECT round(sum(value_share), 2) FROM mart.contract_supplier WHERE financial_year=%s", (fy,))[0][0] == total


def test_summary_tables(conn):
    got = {(r[0], r[1]): (r[2], r[3]) for r in rows(conn, "SELECT supplier_key, financial_year, contract_count, total_value FROM mart.supplier_fy")}
    assert got == E.SUPPLIER_FY
    got = {(r[0], r[1]): (r[2], r[3]) for r in rows(conn, "SELECT agency_key, financial_year, contract_count, total_value FROM mart.agency_fy")}
    assert got == E.AGENCY_FY
    got = {(r[0], r[1], r[2]): (r[3], r[4]) for r in rows(conn, "SELECT supplier_key, agency_key, financial_year, contract_count, total_value FROM mart.supplier_agency_fy")}
    assert got == E.SUPPLIER_AGENCY_FY


def test_value_share_is_not_rounded(conn):
    """100,000 split three ways is 33,333.333..., not 33,333.33."""
    shares = [r[0] for r in rows(conn, "SELECT value_share FROM mart.contract_supplier WHERE notice_id = 'CN9000017'")]
    assert len(shares) == 3
    for share in shares:
        assert share != round(share, 2)
        assert abs(share * 3 - D("100000")) < D("0.000001")


def test_summary_tables_agree_with_each_other(conn):
    for fy, total in E.FY_TOTALS.items():
        for table in ("supplier_fy", "supplier_agency_fy"):
            assert rows(conn, f"SELECT sum(total_value) FROM mart.{table} WHERE financial_year = %s", (fy,))[0][0] == total, table
    for fy, total in E.AGENCY_FY_TOTALS.items():
        assert rows(conn, "SELECT sum(total_value) FROM mart.agency_fy WHERE financial_year = %s", (fy,))[0][0] == total, fy


def test_supplier_and_agency_tables(conn):
    got = {r[0]: r[1:] for r in rows(conn, "SELECT supplier_key, supplier_name, supplier_abn, contract_count, total_value, first_publish_date, last_publish_date FROM mart.supplier")}
    assert got[E.S1] == (E.SUPPLIER_NAMES[E.S1], E.S1, 6, D("333400"), date(2024, 10, 1), date(2025, 10, 2))
    assert got[E.S4][1:3] == (None, 1)  # no ABN; C20 has no value, so only C9 counts
    assert got[E.S5][1] is None  # invalid ABN is not stored
    assert got[E.S2][2:] == (7, D("373400"), date(2024, 8, 15), date(2025, 10, 2))
    assert got[E.S6][2:4] == (1, D("15000"))  # listed twice on one contract: counted once
    agency = {r[0]: r[1:] for r in rows(conn, "SELECT agency_key, agency_name, contract_count, total_value FROM mart.agency")}
    assert agency[E.A1] == (E.A1_NAME, 10, D("720000"))  # includes C19 (no supplier), not C20 (no value)
    assert agency[E.A2] == (E.A2_NAME, 8, D("545200"))


def test_acceptance_query_top_suppliers_and_agency_split(conn):
    q = """
        WITH top AS (
            SELECT supplier_key FROM mart.supplier_fy
            WHERE financial_year = '2024-25' ORDER BY total_value DESC LIMIT 10)
        SELECT s.supplier_key, s.agency_key, s.total_value
        FROM mart.supplier_agency_fy s JOIN top USING (supplier_key)
        WHERE s.financial_year = '2024-25'
        ORDER BY s.supplier_key, s.agency_key"""
    got = rows(conn, q)
    assert {r[0] for r in got} == set(E.TOP_SUPPLIERS_FY2425)
    assert sum(r[2] for r in got) == E.FY_TOTALS["2024-25"]


# ---- determinism and builders ------------------------------------------------------------------


def table_hashes(conn):
    out = {}
    for schema, tables in (("core", ("contract", "amendment", "contract_supplier", "agency", "supplier")), ("mart", mart.MART_TABLES)):
        for t in tables:
            out[f"{schema}.{t}"] = conn.execute(
                f"SELECT md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) FROM {schema}.{t} x"
            ).fetchone()[0]
    return out


def test_rebuild_twice_gives_identical_tables(conn):
    before = table_hashes(conn)
    mart.rebuild(conn)
    assert table_hashes(conn) == before
    mart.rebuild(conn)
    assert table_hashes(conn) == before
    assert rows(conn, "SELECT count(*) FROM mart.contract")[0][0] == E.MART_ROWS["contract"]


def test_rebuild_is_a_function_of_raw_only(new_db):
    """The same raw rows stored in a different physical order, with other first_seen_at, give the same core and mart."""
    a, b = new_db(), new_db()
    build_test_db(a)
    build_test_db(b)
    with psycopg.connect(b, autocommit=True) as cb:
        with cb.transaction():
            cb.execute("CREATE TEMP TABLE r AS SELECT * FROM raw.release")
            cb.execute("DELETE FROM raw.release")
            cb.execute(
                "INSERT INTO raw.release SELECT release_id, ocid, payload, payload_sha256, ingest_run_id,"
                " first_seen_at - interval '1 day' FROM r ORDER BY release_id DESC"
            )
        assert [r[0] for r in rows(cb, "SELECT release_id FROM raw.release LIMIT 2")] == [
            r[0] for r in rows(cb, "SELECT release_id FROM raw.release ORDER BY release_id DESC LIMIT 2")
        ]
        mart.rebuild(cb)
        hb = table_hashes(cb)
    with psycopg.connect(a, autocommit=True) as ca:
        assert table_hashes(ca) == hb


def test_readers_see_the_old_mart_during_a_rebuild(new_db, monkeypatch):
    """A rebuild must not lock readers out: until it commits they read the previous mart, without waiting."""
    dsn = new_db()
    build_test_db(dsn)
    seen = {}
    original = mart.rebuild_mart

    def rebuild_then_read(conn):
        original(conn)
        with psycopg.connect(dsn, autocommit=True) as reader:
            reader.execute("SET lock_timeout = '2s'")
            seen["contracts"] = reader.execute("SELECT count(*) FROM mart.contract").fetchone()[0]

    monkeypatch.setattr(mart, "rebuild_mart", rebuild_then_read)
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("DELETE FROM raw.release WHERE release_id = 'prod-seed0001-00000000000000000000000000000001'")
        mart.rebuild(c)
        assert seen["contracts"] == E.MART_ROWS["contract"]
        assert c.execute("SELECT count(*) FROM mart.contract").fetchone()[0] == E.MART_ROWS["contract"] - 1


def test_a_failed_rebuild_leaves_the_previous_mart(new_db, monkeypatch):
    dsn = new_db()
    build_test_db(dsn)
    original = mart.rebuild_mart

    def rebuild_then_fail(conn):
        original(conn)
        raise RuntimeError("boom")

    monkeypatch.setattr(mart, "rebuild_mart", rebuild_then_fail)
    with psycopg.connect(dsn, autocommit=True) as c:
        before = table_hashes(c)
        c.execute("DELETE FROM raw.release WHERE release_id = 'prod-seed0001-00000000000000000000000000000001'")
        with pytest.raises(RuntimeError):
            mart.rebuild(c)
        assert table_hashes(c) == before


def test_build_test_db_on_empty_database(new_db):
    dsn = new_db()
    build_test_db(dsn)
    with psycopg.connect(dsn) as c:
        assert c.execute("SELECT count(*) FROM mart.contract").fetchone()[0] == E.MART_ROWS["contract"]


def test_build_test_db_twice_is_harmless(new_db):
    dsn = new_db()
    build_test_db(dsn)
    build_test_db(dsn)
    with psycopg.connect(dsn) as c:
        assert c.execute("SELECT count(*) FROM raw.release").fetchone()[0] == E.RAW_RELEASES
        assert c.execute("SELECT count(*) FROM mart.contract").fetchone()[0] == E.MART_ROWS["contract"]


# ---- roles -------------------------------------------------------------------------------------


def test_explorer_ro_reads_mart(ro_conn):
    assert ro_conn.execute("SELECT count(*) FROM mart.contract").fetchone()[0] == E.MART_ROWS["contract"]
    assert ro_conn.execute("SHOW default_transaction_read_only").fetchone()[0] == "on"


@pytest.mark.parametrize("table", ["raw.release", "core.contract", "core.supplier", "ops.checkpoint", "ops.rejected_release"])
def test_explorer_ro_cannot_read_other_schemas(ro_conn, table):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ro_conn.execute(f"SELECT 1 FROM {table} LIMIT 1")


@pytest.mark.parametrize(
    "stmt",
    [
        "INSERT INTO mart.agency VALUES ('x','x',NULL,0,0,NULL,NULL)",
        "UPDATE mart.contract SET is_cancelled = true",
        "DELETE FROM mart.supplier",
        "TRUNCATE mart.contract",
        "CREATE TABLE mart.x (a int)",
        "CREATE TABLE public.x (a int)",
        "CREATE TEMP TABLE x (a int)",
    ],
)
def test_explorer_ro_cannot_write(ro_conn, stmt):
    with pytest.raises(psycopg.Error):
        ro_conn.execute(stmt)


@pytest.mark.parametrize("table", mart.MART_TABLES)
def test_explorer_ro_has_no_write_privilege_on_mart(conn, table):
    """The real write barrier: default_transaction_read_only is only a session default a client can override."""
    for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert rows(conn, "SELECT has_table_privilege('explorer_ro', %s, %s)", (f"mart.{table}", priv))[0][0] is False, priv


def test_explorer_ro_has_no_temp_or_public_schema_access(conn):
    """explorer_ro gets SELECT on mart and nothing else, not even PUBLIC's default TEMP and public-schema rights."""
    assert rows(conn, "SELECT has_database_privilege('explorer_ro', current_database(), 'TEMPORARY')")[0][0] is False
    for priv in ("USAGE", "CREATE"):
        assert rows(conn, "SELECT has_schema_privilege('explorer_ro', 'public', %s)", (priv,))[0][0] is False, priv


def test_explorer_ro_cannot_write_even_in_a_read_write_transaction(ro_conn):
    ro_conn.execute("BEGIN READ WRITE")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ro_conn.execute("DELETE FROM mart.supplier")
    finally:
        ro_conn.execute("ROLLBACK")


def test_explorer_ro_sees_future_mart_tables(new_db):
    dsn = new_db()
    build_test_db(dsn)
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute("SET ROLE austender_loader")
        admin.execute("CREATE TABLE mart.future_table (a int)")
    ro = make_conninfo(dsn, user="explorer_ro", password="explorer_ro")
    with psycopg.connect(ro) as c:
        assert c.execute("SELECT count(*) FROM mart.future_table").fetchone()[0] == 0


# ---- comments ----------------------------------------------------------------------------------

COMMENT_LIMIT = 12_000


def test_every_mart_table_and_column_has_a_comment(conn):
    missing = rows(
        conn,
        """SELECT c.relname, NULL::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'mart' AND c.relkind = 'r' AND coalesce(obj_description(c.oid, 'pg_class'), '') = ''
           UNION ALL
           SELECT c.relname, a.attname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
           WHERE n.nspname = 'mart' AND c.relkind = 'r' AND coalesce(col_description(c.oid, a.attnum), '') = ''""",
    )
    assert missing == []


def test_mart_comments_stay_under_the_limit(conn):
    (total,) = rows(
        conn,
        """SELECT sum(length(d.description)) FROM pg_description d JOIN pg_class c ON c.oid = d.objoid
           JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'mart' AND c.relkind = 'r'""",
    )[0]
    assert 0 < total < COMMENT_LIMIT, total
