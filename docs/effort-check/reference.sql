-- Reference SQL for the reasoning effort check (explorer/SPEC.md §10).
-- Run read-only as explorer_ro:
--   docker exec -i austender-postgres psql -U explorer_ro -d austender < docs/effort-check/reference.sql
-- Values are recorded in docs/effort-check/reference-values.md.

\pset footer off
BEGIN TRANSACTION READ ONLY;

-- ── Q1 ── Department of Defence, calendar year 2014: value and contract count.
-- Calendar year = publish_date in [2014-01-01, 2015-01-01). Cancelled excluded.
-- A contract counts if it has a published value (matches mart.agency.contract_count).
-- Two agencies share the name "Department of Defence"; give each key and both.
\echo Q1
SELECT CASE WHEN GROUPING(agency_key) = 1 THEN 'both (by name)' ELSE agency_key END AS scope,
       sum(current_value)            AS total_value,
       count(current_value)          AS contracts_with_value,
       count(*) - count(current_value) AS contracts_without_value
FROM mart.contract
WHERE agency_name = 'Department of Defence'
  AND NOT is_cancelled
  AND publish_date >= DATE '2014-01-01' AND publish_date < DATE '2015-01-01'
GROUP BY ROLLUP (agency_key)
ORDER BY scope;

-- Q1 known wrong answers (both keys, by name).
\echo Q1-wrong
SELECT 'FY 2013-14' AS mistake, sum(current_value) AS total_value, count(current_value) AS contracts
FROM mart.contract
WHERE agency_name = 'Department of Defence' AND NOT is_cancelled AND financial_year = '2013-14'
UNION ALL
SELECT 'FY 2014-15', sum(current_value), count(current_value)
FROM mart.contract
WHERE agency_name = 'Department of Defence' AND NOT is_cancelled AND financial_year = '2014-15'
UNION ALL
SELECT 'CY 2014 incl. cancelled', sum(current_value), count(current_value)
FROM mart.contract
WHERE agency_name = 'Department of Defence'
  AND publish_date >= DATE '2014-01-01' AND publish_date < DATE '2015-01-01'
UNION ALL
SELECT 'CY 2014 ILIKE %defence%', sum(current_value), count(current_value)
FROM mart.contract
WHERE agency_name ILIKE '%defence%' AND NOT is_cancelled
  AND publish_date >= DATE '2014-01-01' AND publish_date < DATE '2015-01-01';

-- ── Q2 ── Top 5 suppliers by value from limited tenders, FY 2022-23.
-- Each supplier's equal share (value_share), never the whole contract value.
-- 'withheld:' keys are one unknown supplier per notice; shown with a flag, not filtered.
\echo Q2
SELECT supplier_key, max(supplier_name) AS supplier_name,
       round(sum(value_share), 2)   AS value,
       count(DISTINCT notice_id)    AS contracts,
       supplier_key LIKE 'withheld:%' AS withheld
FROM mart.contract_supplier
WHERE financial_year = '2022-23' AND procurement_method = 'limited'
GROUP BY supplier_key
ORDER BY value DESC
LIMIT 7;

-- Q2 known wrong reading: "limited tender" as having a limited-tender reason code.
\echo Q2-wrong-reason-code
SELECT cs.supplier_key, max(cs.supplier_name) AS supplier_name, round(sum(cs.value_share), 2) AS value
FROM mart.contract_supplier cs
JOIN mart.contract c USING (notice_id)
WHERE cs.financial_year = '2022-23' AND c.limited_tender_reason_code IS NOT NULL
GROUP BY cs.supplier_key
ORDER BY value DESC
LIMIT 5;

-- ── Q3 ── Top 10 contracts published in FY 2023-24 by growth through amendments.
-- Growth = current_value - original_value (value_change). Cancelled contracts excluded.
-- There is no amendment date in the data.
\echo Q3
SELECT notice_id, agency_name, original_value, current_value, value_change,
       amendment_count - cancelled_amendment_count AS effective_amendments
FROM mart.contract
WHERE financial_year = '2023-24' AND NOT is_cancelled AND value_change IS NOT NULL
ORDER BY value_change DESC
LIMIT 10;

-- Q3 sensitivity: would including cancelled contracts change the top 10?
\echo Q3-cancelled-in-top10
SELECT notice_id, value_change
FROM (SELECT notice_id, value_change, is_cancelled
      FROM mart.contract
      WHERE financial_year = '2023-24' AND value_change IS NOT NULL
      ORDER BY value_change DESC LIMIT 10) t
WHERE is_cancelled;

-- ── Q4 ── Total contract value by financial year since the data starts.
-- Cancelled excluded. First FY (2012-13: Jan–Jun 2013 only) and last (2026-27: from July 2026) are partial.
\echo Q4
SELECT financial_year, total, contracts, first_publish, last_publish
FROM (SELECT financial_year,
             sum(current_value)   AS total,
             count(current_value) AS contracts,
             min(publish_date)    AS first_publish,
             max(publish_date)    AS last_publish
      FROM mart.contract
      WHERE NOT is_cancelled
      GROUP BY financial_year) t
ORDER BY financial_year;

-- Cross-check: agency_fy should give the same totals.
\echo Q4-crosscheck-mismatches
SELECT a.financial_year, a.total AS agency_fy_total, c.total AS contract_total
FROM (SELECT financial_year, sum(total_value) AS total FROM mart.agency_fy GROUP BY 1) a
FULL JOIN (SELECT financial_year, sum(current_value) AS total
           FROM mart.contract WHERE NOT is_cancelled GROUP BY 1) c USING (financial_year)
WHERE a.total IS DISTINCT FROM c.total;

-- ── Q5 ── Share of FY 2024-25 contract value that went to overseas suppliers.
-- Numerator and denominator from the same rows (contract_supplier, cancelled already excluded).
-- is_overseas is per notice: published country not Australia/AU/blank, or region "outside australia".
\echo Q5
SELECT round(sum(value_share) FILTER (WHERE is_overseas), 2) AS overseas_value,
       round(sum(value_share), 2)                            AS total_value,
       round(100 * sum(value_share) FILTER (WHERE is_overseas) / sum(value_share), 4) AS overseas_pct
FROM mart.contract_supplier
WHERE financial_year = '2024-25';

-- Q5 known wrong reading: supplier-level flag (foreign on any notice ever).
\echo Q5-wrong-supplier-flag
SELECT round(sum(cs.value_share) FILTER (WHERE s.is_overseas), 2) AS overseas_value,
       round(100 * sum(cs.value_share) FILTER (WHERE s.is_overseas) / sum(cs.value_share), 4) AS overseas_pct
FROM mart.contract_supplier cs
JOIN mart.supplier s USING (supplier_key)
WHERE cs.financial_year = '2024-25';

-- Q5 known wrong reading: cancelled contracts in the denominator.
\echo Q5-wrong-cancelled-in-denominator
SELECT sum(current_value) AS denominator,
       round(100 * 4106321710.92 / sum(current_value), 4) AS overseas_pct
FROM mart.contract
WHERE financial_year = '2024-25';

-- Q5 variant: denominator including contracts with no published supplier (agency_fy).
\echo Q5-variant-denominator-all-contracts
SELECT sum(total_value) AS total_value_all_contracts
FROM mart.agency_fy
WHERE financial_year = '2024-25';

ROLLBACK;
