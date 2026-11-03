- Data: contract notices published on AusTender from January 2013. The latest month may be
  incomplete.
- The traps are already handled in the tables: cancelled contracts are excluded from every table
  except `mart.contract`, and multi-supplier values are split. Don't add your own filters for them.
- Prefer the smallest table that answers the question: the `*_fy` summaries, then
  `mart.contract_supplier`, then `mart.contract`.
- Years: Australian financial years run July to June (`2024-25`). If the user names a bare year
  ("in 2024"), use the financial year that starts in it, unless they say calendar year. Then sum
  `contract_supplier` by `publish_date`. Always state which one you used.
- Values are contract values as published at award or amendment, not payments made.
- Supplier and agency names are shown as AusTender publishes them. Search names with `ILIKE`.
- Drill-downs: what lies below a row, for drill_sql. Every breakdown here has detail below it,
  even with only two or three rows:
  - a financial year → its agencies (`mart.agency_fy`), or its months by `publish_date`
    (`mart.contract_supplier`)
  - an agency → its suppliers; a supplier → its agencies (`mart.supplier_agency_fy`)
  - a procurement method, a supplier location (`is_overseas`) or a category (`unspsc_segment`,
    then `unspsc_family`) → its agencies or suppliers (`mart.contract_supplier`)
  - a list of contracts is already the detail.
