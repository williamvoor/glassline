import time

import pytest

from explorer_agent.sql import POOL_MAX_SIZE, SqlFail, SqlOk, SqlRunner, StartupError, open_pool

pytestmark = pytest.mark.db

ALLOWED = ("mart",)


def runner(pool, *, guard_enabled=True, timeout_ms=5000, row_cap=100):
    return SqlRunner(pool, ALLOWED, timeout_ms, row_cap, guard_enabled=guard_enabled)


# D6
async def test_select_returns_typed_json_safe_rows(ro_pool):
    out = await runner(ro_pool).run(
        "SELECT notice_id, publish_date, current_value, amendment_count, is_cancelled "
        "FROM mart.contract ORDER BY notice_id LIMIT 3"
    )
    assert isinstance(out, SqlOk), out
    assert out.columns == ["notice_id", "publish_date", "current_value", "amendment_count", "is_cancelled"]
    assert out.column_types[0] == "text"
    assert out.column_types[1] == "date"
    assert out.column_types[2] == "numeric"  # numeric(18,2) column: no typmod in the name
    assert out.column_types[4] == "boolean"
    first = out.rows[0]
    assert isinstance(first["publish_date"], str)  # ISO date
    assert isinstance(first["current_value"], float)
    assert out.truncated is False
    assert out.elapsed_ms >= 0


async def test_row_cap_reports_truncation(ro_pool):
    out = await runner(ro_pool, row_cap=2).run("SELECT notice_id FROM mart.contract")
    assert isinstance(out, SqlOk)
    assert len(out.rows) == 2
    assert out.truncated is True


async def test_exactly_row_cap_rows_is_not_truncated(ro_pool):
    out = await runner(ro_pool, row_cap=2).run("SELECT notice_id FROM mart.contract LIMIT 2")
    assert isinstance(out, SqlOk)
    assert (len(out.rows), out.truncated) == (2, False)


async def test_guard_rejection_is_a_failure_not_an_exception(ro_pool):
    out = await runner(ro_pool).run("SELECT * FROM contract")
    assert isinstance(out, SqlFail)
    assert out.sqlstate is None
    assert "qualify table names" in out.message


async def test_sql_error_carries_sqlstate_and_message(ro_pool):
    out = await runner(ro_pool).run("SELECT valu FROM mart.contract")
    assert isinstance(out, SqlFail)
    assert out.sqlstate == "42703"
    assert "valu" in out.message


async def test_duplicate_column_names_are_rejected(ro_pool):
    out = await runner(ro_pool).run("SELECT a.agency_key, b.agency_key FROM mart.agency a JOIN mart.agency b USING (agency_key)")
    assert isinstance(out, SqlFail)
    assert "unique alias" in out.message


# D7: writes past the guard are stopped by the database
@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM mart.contract",
        "UPDATE mart.contract SET notice_id = notice_id",
        "CREATE TABLE mart.t (x int)",
    ],
)
async def test_writes_fail_in_the_database(ro_pool, sql):
    out = await runner(ro_pool, guard_enabled=False).run(sql)
    assert isinstance(out, SqlFail)
    assert out.sqlstate in {"25006", "42501"}


async def test_transaction_is_read_only(ro_pool):
    out = await runner(ro_pool, guard_enabled=False).run("SELECT current_setting('transaction_read_only') AS ro")
    assert isinstance(out, SqlOk)
    assert out.rows == [{"ro": "on"}]


# D8
async def test_statement_timeout(ro_pool):
    r = runner(ro_pool, guard_enabled=False, timeout_ms=100)
    out = await r.run("SELECT pg_sleep(1)")
    assert isinstance(out, SqlFail)
    assert out.sqlstate == "57014"
    after = await r.run("SELECT 1 AS one")
    assert isinstance(after, SqlOk)
    assert after.rows == [{"one": 1}]


# D9
async def test_multiple_statements_fail_on_the_extended_protocol(ro_pool):
    out = await runner(ro_pool, guard_enabled=False).run("SELECT 1; SELECT 2")
    assert isinstance(out, SqlFail)
    assert out.sqlstate == "42601"


# D10 (regression, refuter): a session advisory lock survives ROLLBACK; only the pool's
# DISCARD ALL removes it before the connection is reused.
async def test_session_state_does_not_leak_between_runs(ro_pool):
    r = runner(ro_pool, guard_enabled=False)
    took = await r.run("SELECT pg_try_advisory_lock(42) AS took")
    assert isinstance(took, SqlOk) and took.rows == [{"took": True}]
    for _ in range(POOL_MAX_SIZE + 1):  # every pooled connection, including the one that took it
        out = await r.run("SELECT count(*) AS held FROM pg_locks WHERE locktype = 'advisory'")
        assert isinstance(out, SqlOk), out
        assert out.rows == [{"held": 0}]


# Regression (refuter): the row cap bounds what the agent fetches, not only what it keeps.
async def test_row_cap_bounds_the_fetch(ro_pool):
    r = runner(ro_pool, timeout_ms=60_000, row_cap=2)
    start = time.perf_counter()
    out = await r.run("SELECT g, repeat('x', 200) AS pad FROM generate_series(1, 20000000) g")
    elapsed = time.perf_counter() - start
    assert isinstance(out, SqlOk), out
    assert (len(out.rows), out.truncated) == (2, True)
    assert elapsed < 3, f"fetching 2 rows took {elapsed:.1f}s: the whole result was read"
    after = await r.run("SELECT 1 AS one")  # the connection is usable after stopping early
    assert isinstance(after, SqlOk)


# D11
async def test_privilege_check_passes_for_read_only_role(ro_pool):
    await runner(ro_pool).check_privileges()


async def test_privilege_check_fails_for_loader(loader_pool):
    # the loader is the container's superuser in austender's compose file, but not here
    with pytest.raises(StartupError, match="can write to allowed tables"):
        await runner(loader_pool).check_privileges()


async def test_privilege_check_fails_for_superuser(pg):
    pool = open_pool(pg.superuser_dsn)
    await pool.open(wait=True)
    try:
        with pytest.raises(StartupError, match="superuser"):
            await runner(pool).check_privileges()
    finally:
        await pool.close()


# Regression (refuter): a malformed DSN must not reach psycopg.pool's log with its password.
def test_malformed_dsn_is_rejected_without_the_secret(caplog):
    with pytest.raises(ValueError) as e:
        open_pool("explorer_ro:s3cretpw@localhost/db")
    assert "s3cretpw" not in str(e.value)
    assert "s3cretpw" not in caplog.text


async def test_privilege_check_fails_for_empty_schema(ro_pool):
    r = SqlRunner(ro_pool, ("mart", "nothing_here"), 5000, 10)
    with pytest.raises(StartupError, match="nothing_here"):
        await r.check_privileges()


# Regression (scenario build 2026-11-02): psycopg prepares a statement after it runs 5 times on a
# connection, and the pool's DISCARD ALL reset drops it on the server only, so the next run failed
# with 'prepared statement "_pg3_0" does not exist'. Many queries in a row on one connection did it.
async def test_many_queries_on_one_connection(ro_pool):
    r = runner(ro_pool)
    results = [await r.run("SELECT count(*) AS n FROM mart.contract") for _ in range(15)]
    assert all(isinstance(x, SqlOk) for x in results), [x for x in results if isinstance(x, SqlFail)][:1]
