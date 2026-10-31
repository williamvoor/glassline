"""core -> mart, and the one-transaction `rebuild` of core and mart from raw.

mart tables are created by migrations (with their comments and indexes); a rebuild empties and refills
them in one transaction, so readers (which only ever see committed state) never observe a partial rebuild.
"""

from __future__ import annotations

import psycopg

from austender.normalise import rebuild_core

MART_TABLES = (
    "contract",
    "contract_supplier",
    "supplier",
    "agency",
    "supplier_fy",
    "agency_fy",
    "supplier_agency_fy",
)

# DELETE, not TRUNCATE: TRUNCATE takes an ACCESS EXCLUSIVE lock that would block the explorer's reads for
# the whole rebuild. With DELETE, readers keep seeing the previous mart until the rebuild commits.
_BUILD_MART = """
DELETE FROM mart.contract;
DELETE FROM mart.contract_supplier;
DELETE FROM mart.supplier;
DELETE FROM mart.agency;
DELETE FROM mart.supplier_fy;
DELETE FROM mart.agency_fy;
DELETE FROM mart.supplier_agency_fy;

INSERT INTO mart.contract (
    notice_id, agency_key, agency_name, publish_date, financial_year, procurement_method,
    limited_tender_reason_code, limited_tender_reason, unspsc, unspsc_segment, unspsc_family, unspsc_class,
    period_start, period_end, original_value, current_value, value_change, amendment_count,
    cancelled_amendment_count, supplier_count, is_cancelled, description)
SELECT c.notice_id, c.agency_key, a.name, c.publish_date, c.financial_year, c.procurement_method,
       c.limited_tender_reason_code, c.limited_tender_reason, c.unspsc,
       left(c.unspsc, 2), left(c.unspsc, 4), left(c.unspsc, 6),
       c.current_period_start, c.current_period_end, c.original_value, c.current_value,
       c.current_value - c.original_value, c.amendment_count, c.cancelled_amendment_count,
       coalesce(n.n, 0), c.is_cancelled, c.description
FROM core.contract c
JOIN core.agency a USING (agency_key)
LEFT JOIN (SELECT notice_id, count(*) AS n FROM core.contract_supplier GROUP BY notice_id) n USING (notice_id);

-- value_share = current value / number of suppliers, unrounded. Cancelled contracts are excluded.
INSERT INTO mart.contract_supplier (
    notice_id, ordinal, supplier_key, supplier_name, supplier_abn, is_overseas, value_share, contract_value,
    supplier_count, financial_year, publish_date, agency_key, agency_name, procurement_method, unspsc,
    unspsc_segment, unspsc_family, unspsc_class)
SELECT cs.notice_id, cs.ordinal, cs.supplier_key, s.name, s.abn, cs.is_overseas,
       c.current_value / m.supplier_count, c.current_value, m.supplier_count,
       c.financial_year, c.publish_date, c.agency_key, a.name, c.procurement_method, c.unspsc,
       left(c.unspsc, 2), left(c.unspsc, 4), left(c.unspsc, 6)
FROM core.contract_supplier cs
JOIN core.contract c USING (notice_id)
JOIN core.agency a USING (agency_key)
JOIN core.supplier s USING (supplier_key)
JOIN (SELECT notice_id, count(*)::int AS supplier_count FROM core.contract_supplier GROUP BY notice_id) m
     USING (notice_id)
WHERE NOT c.is_cancelled AND c.current_value IS NOT NULL;

INSERT INTO mart.supplier (
    supplier_key, supplier_name, supplier_abn, is_overseas, contract_count, total_value,
    first_publish_date, last_publish_date)
SELECT s.supplier_key, s.name, s.abn, s.is_overseas,
       coalesce(t.n, 0), coalesce(round(t.total, 2), 0), t.first_d, t.last_d
FROM core.supplier s
LEFT JOIN (
    SELECT supplier_key, count(DISTINCT notice_id)::int AS n, sum(value_share) AS total,
           min(publish_date) AS first_d, max(publish_date) AS last_d
    FROM mart.contract_supplier GROUP BY supplier_key
) t USING (supplier_key);

INSERT INTO mart.agency (
    agency_key, agency_name, agency_abn, contract_count, total_value, first_publish_date, last_publish_date)
SELECT a.agency_key, a.name, a.abn, coalesce(t.n, 0), coalesce(round(t.total, 2), 0), t.first_d, t.last_d
FROM core.agency a
LEFT JOIN (
    SELECT agency_key, count(*)::int AS n, sum(current_value) AS total,
           min(publish_date) AS first_d, max(publish_date) AS last_d
    FROM mart.contract
    WHERE NOT is_cancelled AND current_value IS NOT NULL
    GROUP BY agency_key
) t USING (agency_key);

INSERT INTO mart.supplier_fy (supplier_key, supplier_name, financial_year, contract_count, total_value)
SELECT supplier_key, max(supplier_name), financial_year, count(DISTINCT notice_id)::int,
       round(sum(value_share), 2)
FROM mart.contract_supplier GROUP BY supplier_key, financial_year;

INSERT INTO mart.agency_fy (agency_key, agency_name, financial_year, contract_count, total_value)
SELECT agency_key, max(agency_name), financial_year, count(*)::int, round(sum(current_value), 2)
FROM mart.contract
WHERE NOT is_cancelled AND current_value IS NOT NULL
GROUP BY agency_key, financial_year;

INSERT INTO mart.supplier_agency_fy (
    supplier_key, supplier_name, agency_key, agency_name, financial_year, contract_count, total_value)
SELECT supplier_key, max(supplier_name), agency_key, max(agency_name), financial_year,
       count(DISTINCT notice_id)::int, round(sum(value_share), 2)
FROM mart.contract_supplier GROUP BY supplier_key, agency_key, financial_year;

ANALYZE mart.contract, mart.contract_supplier, mart.supplier, mart.agency,
        mart.supplier_fy, mart.agency_fy, mart.supplier_agency_fy;
"""


def rebuild_mart(conn: psycopg.Connection) -> None:
    conn.execute(_BUILD_MART)  # type: ignore[arg-type]


def rebuild(conn: psycopg.Connection) -> dict[str, int]:
    """Rebuild core and mart from raw in a single transaction."""
    with conn.transaction():
        stats = rebuild_core(conn)
        rebuild_mart(conn)
    return stats
