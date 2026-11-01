# Reasoning effort check: reference values

The reference answers for the five questions in `explorer/SPEC.md` §10. The SQL is in
[`reference.sql`](reference.sql). It was last run on 2026-11-02 as `explorer_ro` in a `READ ONLY`
transaction against the full backfill: 2013-01-02 to 2026-10-30, 864,611 contracts.

**Status:** scoring decisions agreed with the user on 2026-11-02 (see the end of this file).

## Conventions used throughout

- **Value** is `current_value`, the latest non-cancelled amendment's value. Cancelled contracts
  are excluded everywhere. The `mart.contract_supplier`, `mart.agency_fy` and `mart.supplier_fy`
  tables already exclude them; queries on `mart.contract` add `NOT is_cancelled`.
- **A contract counts** if it has a published value. This matches `mart.agency.contract_count`.
- **Financial year** is the `financial_year` column, which runs July to June and is set from
  `publish_date`. **Calendar year** filters on `publish_date`.
- **Supplier value** is the sum of `value_share`. A supplier is one `supplier_key` (an ABN,
  else a normalised name). AusTender publishes exactly one supplier per notice (all 1,171,903 raw
  releases to 2026-10), so `value_share` equals `current_value` throughout.

## Q1. Department of Defence, calendar year 2014

| Scope | Value | Contracts |
|---|---:|---:|
| Agency key `62950639680` | $38,414,946,047.02 | 24,172 |
| Agency key `68706814312` | $379,904.95 | 15 |
| **Both "Department of Defence" keys** | **$38,415,325,951.97** | **24,187** |

Either the main key or both keys is correct, provided the answer says which it used. The value
is $38.4B either way. Only the contract count tells the two apart. No Defence contract in 2014
lacked a value. Matching `ILIKE '%defence%'` gives the both-keys result: the Defence and
Veterans' Service Commission first published on 2025-10-17.

Known wrong answers (both keys):

| Mistake | Value | Contracts |
|---|---:|---:|
| Financial year 2013-14 instead of calendar 2014 | $22,705,188,409.47 | 23,724 |
| Financial year 2014-15 instead of calendar 2014 | $43,973,288,042.96 | 26,141 |
| Calendar 2014 with cancelled contracts included | $50,964,246,894.96 | 24,842 |

## Q2. Top 5 suppliers by value from limited tenders, 2022-23

| # | Supplier | Value | Contracts |
|---:|---|---:|---:|
| 1 | FMS ACCOUNT RESERVE BANK OF AUSTRALIA | $4,870,868,894.85 | 41 |
| 2 | THALES AUSTRALIA | $1,701,252,013.18 | 516 |
| 3 | IBM AUSTRALIA LTD | $1,412,828,137.67 | 37 |
| 4 | RAYTHEON AUSTRALIA PTY LTD | $1,122,727,330.65 | 24 |
| 5 | KONGSBERG DEFENCE & AEROSPACE AS | $1,102,676,098.94 | 1 |
| *6* | *BAE SYSTEMS AUSTRALIA LTD* | *$1,098,362,814.66* | *207* |

No withheld suppliers exist in 2022-23. The mart has only 11 withheld rows, all in 2026-27.

"Limited tender" here means `procurement_method = 'limited'` (32,500 contracts), as the column
comment says. Known wrong reading: filtering on `limited_tender_reason_code IS NOT NULL`
(4,933 contracts) gives Thales $1,488.6M, IBM $1,412.4M, Harris $607.8M, Naval Ship Mgmt
$452.5M, Clough $316.0M. Adding `selective` to the filter changes nothing.

## Q3. Top 10 contracts published in 2023-24 by growth through amendments

| # | Notice | Agency | Original | Current | Growth |
|---:|---|---|---:|---:|---:|
| 1 | CN4020722 | Department of Defence | $8,205,934.00 | $1,566,383,818.12 | $1,558,177,884.12 |
| 2 | CN4050683 | Department of Defence | $624,616,103.00 | $2,143,383,312.86 | $1,518,767,209.86 |
| 3 | CN4041170 | Department of Defence | $1,286,576,256.79 | $2,656,368,496.94 | $1,369,792,240.15 |
| 4 | CN4004023 | Department of Defence | $298,633,743.29 | $1,270,507,773.63 | $971,874,030.34 |
| 5 | CN3982994 | Department of Defence | $10,855,626.35 | $933,943,568.71 | $923,087,942.36 |
| 6 | CN4002456 | Department of Defence | $1,639,000.00 | $879,878,926.91 | $878,239,926.91 |
| 7 | CN4062462 | Department of Defence | $31,271,708.60 | $458,398,431.59 | $427,126,722.99 |
| 8 | CN4029827 | Department of Defence | $12,406,170.70 | $421,754,312.53 | $409,348,141.83 |
| 9 | CN3991067 | Department of Health, Disability and Ageing | $99,000,000.00 | $504,430,500.01 | $405,430,500.01 |
| 10 | CN4023271 | Department of Defence | $72,934,892.00 | $452,003,045.59 | $379,068,153.59 |

Growth is ranked in absolute dollars (`value_change`), not percent. No cancelled contract would
make the top 10, so excluding them changes nothing. The data has no amendment dates, so an
answer that gives one invented it.

## Q4. Total contract value by financial year

| FY | Value | Contracts | Note |
|---|---:|---:|---|
| 2012-13 | $15,111,902,263.86 | 29,983 | **partial**: Jan–Jun 2013 only |
| 2013-14 | $45,531,913,595.13 | 59,259 | |
| 2014-15 | $78,758,480,092.40 | 61,553 | |
| 2015-16 | $92,305,159,113.20 | 62,330 | |
| 2016-17 | $59,877,967,772.24 | 58,060 | |
| 2017-18 | $66,095,324,036.45 | 59,662 | |
| 2018-19 | $83,381,169,684.68 | 61,623 | |
| 2019-20 | $53,691,468,807.67 | 62,763 | |
| 2020-21 | $63,549,594,821.09 | 63,625 | |
| 2021-22 | $74,641,094,920.60 | 68,808 | |
| 2022-23 | $66,758,053,350.55 | 57,913 | |
| 2023-24 | $83,312,327,993.56 | 59,936 | |
| 2024-25 | $62,257,027,767.51 | 58,201 | |
| 2025-26 | $85,557,987,109.51 | 56,173 | complete (Jul 2025–Jun 2026) |
| 2026-27 | $18,810,272,476.09 | 18,697 | **partial**: Jul–Oct 2026 |

Summing `mart.agency_fy` gives exactly the same total for every year.

## Q5. Overseas suppliers' share of 2024-25 contract value

| | Value |
|---|---:|
| Overseas suppliers | $4,106,321,710.92 |
| All suppliers | $62,257,027,767.51 |
| **Share** | **6.5958 %** (6.6 % to 1 dp) |

Both the numerator and the denominator come from `mart.contract_supplier` rows. The
denominator equals the 2024-25 total in Q4 to the cent. Every contract in the database has
exactly one supplier, so the "denominator from different rows" trap can't move the answer in
any year. A supplier is overseas when its published country on that notice isn't Australia,
AU or blank (`normalise.py:132`). A blank country counts as Australian.

Known wrong answers:

| Mistake | Share |
|---|---:|
| Supplier-level flag `mart.supplier.is_overseas` (foreign on *any* notice ever) | 11.2569 % ($7,008,220,653.03) |
| Cancelled contracts in the denominator ($65,513,403,114.97) | 6.2679 % |

## Scoring decisions (2026-11-02)

These add to the scoring rules in `explorer/SPEC.md` §10.

1. **Q1:** the main key or both keys is correct if the answer says which it used.
2. **Q2 ranks** are scored by name and order, not value. Raytheon ($1,122.7M), Kongsberg
   ($1,102.7M) and BAE ($1,098.4M) all display as "$1.1B".
3. **Q2 FMS:** "FMS ACCOUNT RESERVE BANK OF AUSTRALIA" is most likely the US Foreign Military
   Sales payment channel, not a company. Dropping it is correct **only if the answer says why**.
   The top 5 is then Thales, IBM, Raytheon, Kongsberg, BAE. Dropping it silently is wrong.
4. **Q2 corporate groups:** merging supplier keys into groups is wrong. For example, the three
   BAE keys 29008423005, 14111187270 and 62920443960 sum to $1,124.6M, which moves BAE above
   Raytheon and Kongsberg. The data has no group information.
5. **Q3** is ranked by absolute dollar growth. Ranking by percent growth is wrong.
6. **Traps the data can't exercise:** share vs whole contract value (Q2) and contracts with no
   supplier (Q5), because every notice has one supplier. Q2 is kept for its "limited" and FMS
   traps, and Q5 for its overseas-flag and cancelled-contract traps.
