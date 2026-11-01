import pytest

from explorer_agent.sql import GuardError, guard

ALLOWED = ("mart",)


# D4
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT supplier_name, sum(value_share) FROM mart.contract_supplier GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
        "WITH top AS (SELECT supplier_key FROM mart.supplier_fy LIMIT 10) SELECT * FROM top",
        "WITH a AS (SELECT 1 AS x), b AS (SELECT x FROM a) SELECT * FROM b JOIN mart.agency ON true",
        "SELECT date_trunc('month', publish_date), count(*), coalesce(max(x), 0) FROM mart.contract GROUP BY 1",
        "SELECT * FROM mart.contract c WHERE c.notice_id IN (SELECT notice_id FROM mart.contract_supplier)",
        "SELECT 1 UNION ALL SELECT 2",
        "VALUES (1), (2)",
        "TABLE mart.agency",
        "SELECT mart.my_fn(1)",
        "select 1;",
    ],
)
def test_accepts_read_queries(sql):
    guard(sql, ALLOWED)


# D5: each rejection says what to change
@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("SELECT 1; SELECT 2", "exactly one"),
        ("SET statement_timeout = 0", "only SELECT"),
        ("DELETE FROM mart.contract", "only SELECT"),
        ("UPDATE mart.contract SET notice_id = 'x'", "only SELECT"),
        ("CREATE TABLE mart.t (x int)", "only SELECT"),
        ("COPY mart.contract TO STDOUT", "only SELECT"),
        ("EXPLAIN SELECT 1", "only SELECT"),
        ("WITH x AS (DELETE FROM mart.contract RETURNING *) SELECT * FROM x", "no INSERT, UPDATE, DELETE"),
        ("WITH x AS (INSERT INTO mart.agency DEFAULT VALUES RETURNING *) SELECT 1", "no INSERT"),
        ("SELECT * INTO mart.copy FROM mart.contract", "INTO is not allowed"),
        ("SELECT * FROM mart.contract FOR UPDATE", "FOR UPDATE"),
        ("SELECT set_config('statement_timeout', '0', false)", "set_config"),
        ("SELECT pg_sleep(10)", "pg_sleep"),
        ("SELECT * FROM pg_sleep(1)", "pg_sleep"),
        ("SELECT pg_catalog.pg_sleep(1)", "pg_sleep"),
        ("SELECT pg_advisory_lock(1)", "pg_advisory_lock"),
        ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
        ("SELECT query_to_xml('select * from raw.release', true, true, '')", "query_to_xml"),
        ("SELECT * FROM contract", 'qualify table names with their schema: "contract" → mart.<table>'),
        ("SELECT * FROM raw.release", 'schema "raw" is not allowed; allowed: mart'),
        ("SELECT * FROM core.contract", 'schema "core" is not allowed'),
        ("SELECT * FROM pg_catalog.pg_class", "pg_catalog is not available"),
        ("SELECT * FROM information_schema.tables", "information_schema is not available"),
        ("SELECT * FROM pg_class", "qualify table names"),
        ("SELECT raw.secret_fn()", 'functions from schema "raw"'),
        ("SELECT * FROM mart.contract WHERE notice_id IN (SELECT release_id FROM raw.release)", 'schema "raw"'),
        ("selec 1", "syntax error"),
        ("", "exactly one"),
    ],
)
def test_rejects(sql, message):
    with pytest.raises(GuardError) as e:
        guard(sql, ALLOWED)
    assert message in str(e.value)


def test_cte_name_does_not_cover_a_qualified_table():
    with pytest.raises(GuardError, match='schema "raw"'):
        guard("WITH release AS (SELECT 1) SELECT * FROM raw.release", ALLOWED)


# Regression (refuter): a CTE name only covers relations in the query level that can see it.
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM (WITH pg_class AS (SELECT 1) SELECT * FROM pg_class) a, pg_class",
        "WITH a AS (SELECT rolname FROM pg_roles), pg_roles AS (SELECT 1) SELECT * FROM a",
        "WITH a AS (SELECT * FROM pg_stat_activity), pg_stat_activity AS (SELECT 1) SELECT * FROM a",
        "SELECT (WITH pg_roles AS (SELECT 1) SELECT 1), * FROM pg_roles",
    ],
)
def test_cte_name_is_scoped(sql):
    with pytest.raises(GuardError, match="qualify table names"):
        guard(sql, ALLOWED)


@pytest.mark.parametrize(
    "sql",
    [
        "WITH a AS (SELECT 1 AS x), b AS (SELECT x FROM a) SELECT * FROM b",  # later sibling sees earlier
        "WITH a AS (SELECT 1 AS x) SELECT * FROM (SELECT * FROM a) s",  # nested query sees outer CTE
        "WITH a AS (SELECT 1 AS x) SELECT * FROM a UNION ALL SELECT * FROM a",
        "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM t WHERE n < 3) SELECT * FROM t",
        "WITH a AS (SELECT 1 AS x) SELECT (SELECT max(x) FROM a)",
    ],
)
def test_cte_scoping_still_allows_visible_ctes(sql):
    guard(sql, ALLOWED)


# Regression (refuter): more functions that run SQL strings or hold session state.
@pytest.mark.parametrize(
    "fn",
    [
        "ts_stat('select 1')",
        "pg_try_advisory_lock(1)",
        "pg_stat_get_activity(NULL)",
        "cursor_to_xml('c', 1, true, true, '')",
        "table_to_xml('mart.contract', true, true, '')",
        "query_to_xmlschema('select 1', true, true, '')",
        "query_to_xml_and_xmlschema('select 1', true, true, '')",
    ],
)
def test_denied_functions(fn):
    with pytest.raises(GuardError, match="is not allowed"):
        guard(f"SELECT {fn}", ALLOWED)


def test_nul_byte_is_rejected():
    with pytest.raises(GuardError, match="NUL"):
        guard("SELECT 1\x00; DROP TABLE x", ALLOWED)


# Regression (refuter re-review): deep trees must be a GuardError, not a RecursionError.
@pytest.mark.parametrize(
    "sql",
    ["SELECT " + "+".join(["1"] * 3000), " UNION ALL ".join(["SELECT 1"] * 3000)],
)
def test_deeply_nested_query_is_a_guard_error(sql):
    with pytest.raises(GuardError, match="too deeply nested"):
        guard(sql, ALLOWED)
