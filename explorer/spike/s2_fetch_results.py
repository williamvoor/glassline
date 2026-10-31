"""Fetch real query results for the risk-2 model runs and store them as JSON.

Run from explorer/agent:  uv run python ../spike/s2_fetch_results.py
"""

import json
import os
from decimal import Decimal
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv("../.env")
HERE = Path(__file__).parent
FY = "NULL, NULL, NULL, NULL, NULL, '2024-25', '2024-25'"

QUERIES = {
    "q1": f"SELECT display_name AS supplier, contracts, value FROM core.metric_top_suppliers({FY}, 'current') LIMIT 10",
    "q2": f"""SELECT t.display_name AS supplier, f.agency_name AS agency, sum(f.current_value) AS value
FROM (SELECT supplier_key, display_name FROM core.metric_top_suppliers({FY}, 'current') LIMIT 10) t
JOIN core.v_contract_fact f USING (supplier_key)
WHERE f.financial_year = '2024-25' AND NOT coalesce(f.is_cancelled, false)
GROUP BY 1, 2 ORDER BY 1, 3 DESC""",
    "q3": "SELECT label, original_value, amendment_value FROM core.metric_value_by_quarter(NULL, NULL, NULL, NULL, NULL, '2022-23', '2024-25')",
    "q4": "SELECT count(*) AS contracts, sum(current_value) AS total_value FROM core.v_contract_fact WHERE financial_year = '2024-25' AND NOT coalesce(is_cancelled, false)",
    "q5": f"SELECT coalesce(limited_tender_reason_code, '(none)') AS reason, contracts, value, value_share FROM core.metric_limited_reasons({FY}, 'current') LIMIT 10",
    "q6": f"SELECT agency_name AS agency, contracts, value, share FROM core.metric_value_by_agency({FY}, 'current') LIMIT 15",
}


def jsonable(v):
    return float(v) if isinstance(v, Decimal) else v


def main():
    out = {}
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for rid, sql in QUERIES.items():
            cur = conn.execute(sql)
            cols = [d.name for d in cur.description]
            rows = [{c: jsonable(v) for c, v in zip(cols, r)} for r in cur.fetchall()]
            out[rid] = {"sql": sql, "columns": cols, "rows": rows}
            print(rid, len(rows), "rows")
    (HERE / "s2_results.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
