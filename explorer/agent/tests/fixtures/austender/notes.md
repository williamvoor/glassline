- Prefer `mart.agency_fy` for agency spend by year, `mart.supplier_fy` for supplier rankings by year, and `mart.supplier_agency_fy` for supplier-by-agency spend by year; these precomputed tables exclude cancelled contracts.
- Use `mart.contract_supplier` for spend by supplier, category or other contract dimensions: sum `value_share`, never `contract_value` (the full contract value is repeated on each supplier row). Contracts with no supplier listed are absent there, so use `mart.agency_fy` for agency totals.
- Use `mart.contract` for contract-level detail (description, period, amendments, cancellation). It includes cancelled contracts, which keep their full value, so add `WHERE NOT is_cancelled` to any spend total from it. About 3.01% of its 864,611 rows are cancelled.
- Financial year (`financial_year`) is Australian July-June, stored as text like '2024-25'; values run from '2012-13' to '2026-27' (15 years). Prefer financial years for year questions; if a plain calendar year is asked, say which financial year you used unless a calendar year is explicitly requested, in which case filter on `publish_date`.
- Group agencies and suppliers by `agency_key` / `supplier_key`, not by name: names are the latest or most common published spelling and change over time. Search names and `mart.contract.description` with `ILIKE` (e.g. `%legal%`).
- `supplier_key` values starting `withheld:` are one unknown supplier per notice, never a single entity; do not aggregate them as one supplier. Keys starting `name:` mean no valid ABN was published.
- `mart.supplier` includes 622 suppliers with `contract_count = 0` (no non-cancelled, valued contracts); exclude them when ranking suppliers.
- In the current data `supplier_count` is at most 1 on every contract, but still sum `value_share` rather than `contract_value` in `mart.contract_supplier` so multi-supplier contracts would not double count.
- Watch outlier dates in `mart.contract`: 12 rows have a contract period starting before 1900 or ending after 2100 (sentinel-like dates such as 2999-12-31), so sanity-check period dates before using them.
- Money is AUD as published. `original_value` is the value when first published; `current_value` reflects the highest-numbered non-cancelled amendment; `value_change = current_value - original_value`.
- `unspsc` is an 8-digit code only (no category titles in this database); use `unspsc_segment` (2 digits), `unspsc_family` (4) or `unspsc_class` (6) for broader groupings. NULL means not published.
- `limited_tender_reason_code` / `limited_tender_reason` are only populated for some limited tenders; NULL mainly means non-limited or not published, not an error.
- Drill-downs: what lies below a row, for drill_sql. Every breakdown here has detail below it,
  even with only two or three rows:
  - a financial year → its agencies (`mart.agency_fy`), or its months by `publish_date`
    (`mart.contract_supplier`)
  - an agency → its suppliers; a supplier → its agencies (`mart.supplier_agency_fy`)
  - a procurement method, a supplier location (`is_overseas`) or a category (`unspsc_segment`,
    then `unspsc_family`) → its agencies or suppliers (`mart.contract_supplier`)
  - a list of contracts is already the detail.
