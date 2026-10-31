"""Spike risk 4: query timings and safety guards on the read-only login.

Run from explorer/agent:  uv run python ../spike/s4_queries.py
"""

import os
import time

import psycopg
from dotenv import load_dotenv

load_dotenv("../.env")

TIMEOUT_MS = 15_000
ROW_CAP = 5_000

FILTER_FY = "NULL, NULL, NULL, NULL, NULL, '2023-24', '2024-25'"

QUERIES = {
    "metric_top_suppliers FY23-24..24-25 (limit 10)":
        f"SELECT display_name, contracts, value, share FROM core.metric_top_suppliers({FILTER_FY}, 'current') LIMIT 10",
    "metric_value_by_agency FY23-24..24-25":
        f"SELECT agency_name, contracts, value FROM core.metric_value_by_agency({FILTER_FY}, 'current') LIMIT 10",
    "naive top suppliers on v_contract_fact, calendar 2024":
        "SELECT supplier_key, sum(current_value) v FROM core.v_contract_fact "
        "WHERE publish_date >= '2024-01-01' AND publish_date < '2025-01-01' "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
    "naive supplier x agency on v_contract_fact, calendar 2024":
        "SELECT supplier_key, agency_name, sum(current_value) v FROM core.v_contract_fact "
        "WHERE publish_date >= '2024-01-01' AND publish_date < '2025-01-01' "
        "GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 50",
    "count(*) core.contract": "SELECT count(*) FROM core.contract",
    "count(*) raw.ocds_release": "SELECT count(*) FROM raw.ocds_release",
    "abn_name ILIKE '%telstra%' (28M rows)":
        "SELECT name_raw FROM core.abn_name WHERE name_raw ILIKE '%telstra%' LIMIT 20",
    "unbounded select from core.contract (row cap)": "SELECT id, notice_id FROM core.contract",
    "DELETE": "DELETE FROM core.agency WHERE false",
    "UPDATE": "UPDATE core.agency SET name_raw = name_raw WHERE false",
    "CREATE": "CREATE TABLE core.x (i int)",
    "SET ROLE escape attempt": "SET TRANSACTION READ WRITE",
}


def run(conn, sql):
    t0 = time.perf_counter()
    try:
        with conn.transaction():
            cur = conn.cursor()
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(f"SET LOCAL statement_timeout = {TIMEOUT_MS}")
            cur.execute(sql)
            rows = cur.fetchmany(ROW_CAP + 1) if cur.description else []
        truncated = len(rows) > ROW_CAP
        return f"ok {len(rows[:ROW_CAP])} rows{' (TRUNCATED)' if truncated else ''}", time.perf_counter() - t0
    except psycopg.Error as e:
        return f"ERR {e.sqlstate} {str(e).splitlines()[0][:110]}", time.perf_counter() - t0


def main():
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        for label, sql in QUERIES.items():
            result, secs = run(conn, sql)
            print(f"{secs:7.2f}s  {label:55s} {result}")


if __name__ == "__main__":
    main()
