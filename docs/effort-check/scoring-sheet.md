# Reasoning effort check: scoring sheet

Runs: `runs-20261101T235731Z.jsonl`. Reference: [`reference-values.md`](reference-values.md). Score each run by hand; the harness never scores.

## Summary (scored runs; warm-ups left out)

| Level | Runs | Median s | Max s | Model calls | Prompt tokens | Cached tokens | Cache % | Failures |
|---|---|---|---|---|---|---|---|---|
| minimal | 15 | 3.5 | 7.3 | 29 | 229713 | 219981 | 95.8 | 3 |
| low | 15 | 5.5 | 12.6 | 31 | 249476 | 237359 | 95.1 | 3 |

## Q1. How much contract value did the Department of Defence award in calendar year 2014, and across how many contracts?

Reference: [Q1](reference-values.md#q1)

### Q1 · low · run 1

Failure: `none` · 12.6 s · 5 model calls · 0 repairs · bare numbers: 2014, 1, 31, 2014

**Prose:**

> In calendar year 2014 (1 Jan – 31 Dec 2014, by `publish_date`), the Department of Defence awarded \$38,415\.3M across 24,187 non-cancelled contracts with a published value. Values are current contract values as published, in AUD.

**Metrics:** Total contract value (calendar 2014): $38,415.3M; Contracts awarded: 24,187

**Table: Breakdown by agency key**

| Agency key | Agency name | Contract count | Total value |
|---|---|---|---|
| 68706814312 | Department of Defence | 15 | $0.4M |
| 62950639680 | Department of Defence | 24,172 | $38,414.9M |

<details><summary>SQL: Defence calendar 2014 combined total</summary>

```sql
SELECT COUNT(*) AS contract_count, SUM(current_value) AS total_value FROM mart.contract WHERE NOT is_cancelled AND current_value IS NOT NULL AND publish_date >= DATE '2014-01-01' AND publish_date < DATE '2015-01-01' AND agency_key IN ('62950639680','68706814312')
```
</details>

<details><summary>SQL: Defence calendar 2014 combined total</summary>

```sql
SELECT COUNT(*) AS contract_count, SUM(current_value) AS total_value FROM mart.contract WHERE NOT is_cancelled AND current_value IS NOT NULL AND publish_date >= DATE '2014-01-01' AND publish_date < DATE '2015-01-01' AND agency_key IN ('62950639680','68706814312')
```
</details>

<details><summary>SQL: Defence calendar 2014 totals by agency key</summary>

```sql
SELECT agency_key, agency_name, COUNT(*) AS contract_count, SUM(current_value) AS total_value FROM mart.contract WHERE NOT is_cancelled AND current_value IS NOT NULL AND publish_date >= DATE '2014-01-01' AND publish_date < DATE '2015-01-01' AND agency_key IN ('62950639680','68706814312') GROUP BY agency_key, agency_name
```
</details>

Score: 

### Q1 · low · run 2

Failure: `none` · 11.5 s · 3 model calls · 0 repairs · bare numbers: 2014, 2014, 01, 01, 2014, 12, 31, 62950639680

**Prose:**

> In calendar year 2014 (publish_date 2014-01-01 to 2014-12-31), current contract values, non-cancelled contracts, Department of Defence (agency ABN 62950639680):
> 
> The Department awarded \$38,414,946,047 across 24,172 contracts.
> 
> Value basis is contract values as published at award/amendment, not payments made.

**Metrics:** Total contract value: $38,414,946,047; Contracts awarded: 24,172; Non-cancelled contracts by publish_date, 1 Jan – 31 Dec 2014. Current values (AUD as published).: Department of Defence — calendar year 2014

<details><summary>SQL: Defence calendar 2014 contracts via contract table</summary>

```sql
SELECT COUNT(*) AS contract_count, SUM(current_value) AS total_value FROM mart.contract WHERE NOT is_cancelled AND agency_key = '62950639680' AND publish_date >= DATE '2014-01-01' AND publish_date < DATE '2015-01-01'
```
</details>

<details><summary>SQL: Defence calendar 2014 contracts via contract table</summary>

```sql
SELECT COUNT(*) AS contract_count, SUM(current_value) AS total_value FROM mart.contract WHERE NOT is_cancelled AND agency_key = '62950639680' AND publish_date >= DATE '2014-01-01' AND publish_date < DATE '2015-01-01'
```
</details>

Score: 

### Q1 · low · run 3

Failure: `none` · 6.7 s · 3 model calls · 0 repairs · bare numbers: 2014, 2014, 01, 01, 2014, 12, 31, 62950639680

**Prose:**

> In calendar year 2014 (publish_date 2014-01-01 to 2014-12-31), Department of Defence (agency_key 62950639680), non-cancelled contracts, current (amended) values in AUD as published:
> 
> * Value \$38,414,946,047 across 24,172 contracts.

**Metrics:** Contract value: $38,414,946,047; Contracts: 24,172

<details><summary>SQL: Defence calendar 2014 by agency key</summary>

```sql
SELECT COUNT(*) AS contracts, SUM(current_value) AS total_value FROM mart.contract WHERE agency_key = '62950639680' AND publish_date >= DATE '2014-01-01' AND publish_date < DATE '2015-01-01' AND NOT is_cancelled AND current_value IS NOT NULL
```
</details>

<details><summary>SQL: Defence calendar 2014 by agency key</summary>

```sql
SELECT COUNT(*) AS contracts, SUM(current_value) AS total_value FROM mart.contract WHERE agency_key = '62950639680' AND publish_date >= DATE '2014-01-01' AND publish_date < DATE '2015-01-01' AND NOT is_cancelled AND current_value IS NOT NULL
```
</details>

Score: 

### Q1 · minimal · run 1

Failure: `none` · 7.3 s · 3 model calls · 0 repairs · bare numbers: 2014, 1, 31, 2014

**Prose:**

> In calendar year 2014, the Department of Defence awarded substantial contract value across many contracts (non-cancelled contracts with a published value, publish date 1 Jan – 31 Dec 2014; current values as published at award/amendment).

**Metrics:** Contract value: $38,415,325,952; Contracts: 24,187

<details><summary>SQL: Defence calendar 2014 total</summary>

```sql
SELECT SUM(current_value) AS value, COUNT(*) AS contracts FROM mart.contract WHERE agency_key IN ('62950639680','68706814312') AND publish_date >= DATE '2014-01-01' AND publish_date < DATE '2015-01-01' AND NOT is_cancelled AND current_value IS NOT NULL
```
</details>

<details><summary>SQL: Defence calendar 2014 total</summary>

```sql
SELECT SUM(current_value) AS value, COUNT(*) AS contracts FROM mart.contract WHERE agency_key IN ('62950639680','68706814312') AND publish_date >= DATE '2014-01-01' AND publish_date < DATE '2015-01-01' AND NOT is_cancelled AND current_value IS NOT NULL
```
</details>

Score: 

### Q1 · minimal · run 2

Failure: `none` · 5.4 s · 3 model calls · 0 repairs · bare numbers: 2014, 1, 31, 2014

**Prose:**

> In calendar year 2014 (1 Jan – 31 Dec 2014, by `publish_date`), the Department of Defence awarded 24,172 contracts worth \$38,414\.9M. Non-cancelled contracts only; current values as published.

**Metrics:** Total contract value (calendar 2014): $38,414.9M; Contracts awarded: 24,172

<details><summary>SQL: Defence calendar 2014 via contract table incl no-supplier</summary>

```sql
SELECT COUNT(*) AS contracts, SUM(current_value) AS total_value FROM mart.contract WHERE agency_key='62950639680' AND NOT is_cancelled AND publish_date >= DATE '2014-01-01' AND publish_date < DATE '2015-01-01'
```
</details>

<details><summary>SQL: Defence calendar 2014 via contract table incl no-supplier</summary>

```sql
SELECT COUNT(*) AS contracts, SUM(current_value) AS total_value FROM mart.contract WHERE agency_key='62950639680' AND NOT is_cancelled AND publish_date >= DATE '2014-01-01' AND publish_date < DATE '2015-01-01'
```
</details>

Score: 

### Q1 · minimal · run 3

Failure: `none` · 4.1 s · 3 model calls · 0 repairs · bare numbers: 2014, 1, 31, 2014

**Prose:**

> In calendar year 2014 (1 Jan – 31 Dec 2014, by publish date), the Department of Defence awarded 24,172 contracts worth \$38,414\.9M.
> 
> Basis: non-cancelled contracts with a published value, summed at current (amended) value.

**Metrics:** Contract value: $38,414.9M; Contracts: 24,172

<details><summary>SQL: Defence calendar 2014 total</summary>

```sql
SELECT COUNT(*) AS contracts, SUM(current_value) AS total_value FROM mart.contract WHERE agency_key='62950639680' AND NOT is_cancelled AND current_value IS NOT NULL AND publish_date >= DATE '2014-01-01' AND publish_date < DATE '2015-01-01'
```
</details>

<details><summary>SQL: Defence calendar 2014 total</summary>

```sql
SELECT COUNT(*) AS contracts, SUM(current_value) AS total_value FROM mart.contract WHERE agency_key='62950639680' AND NOT is_cancelled AND current_value IS NOT NULL AND publish_date >= DATE '2014-01-01' AND publish_date < DATE '2015-01-01'
```
</details>

Score: 

## Q2. Which 5 suppliers received the most value from limited tenders in 2022-23?

Reference: [Q2](reference-values.md#q2)

### Q2 · low · run 1

Failure: `model_failed` · 2.9 s · 0 model calls · 0 repairs · bare numbers: none

**Prose:**

> The model service failed (ContentPolicyViolationError). Please try again.

Score: 

### Q2 · low · run 2

Failure: `model_failed` · 1.4 s · 0 model calls · 0 repairs · bare numbers: none

**Prose:**

> The model service failed (ContentPolicyViolationError). Please try again.

Score: 

### Q2 · low · run 3

Failure: `model_failed` · 1.2 s · 0 model calls · 0 repairs · bare numbers: none

**Prose:**

> The model service failed (ContentPolicyViolationError). Please try again.

Score: 

### Q2 · minimal · run 1

Failure: `model_failed` · 0.9 s · 0 model calls · 0 repairs · bare numbers: none

**Prose:**

> The model service failed (ContentPolicyViolationError). Please try again.

Score: 

### Q2 · minimal · run 2

Failure: `model_failed` · 1.3 s · 0 model calls · 0 repairs · bare numbers: none

**Prose:**

> The model service failed (ContentPolicyViolationError). Please try again.

Score: 

### Q2 · minimal · run 3

Failure: `model_failed` · 1.0 s · 0 model calls · 0 repairs · bare numbers: none

**Prose:**

> The model service failed (ContentPolicyViolationError). Please try again.

Score: 

## Q3. Which 10 contracts published in 2023-24 grew the most through amendments, from what to what?

Reference: [Q3](reference-values.md#q3)

### Q3 · low · run 1

Failure: `none` · 6.9 s · 2 model calls · 0 repairs · bare numbers: 2023, 24, 1, 2023, 30, 2024

**Prose:**

> Largest growers among non-cancelled contracts published in FY 2023-24 (1 Jul 2023 – 30 Jun 2024), ranked by value_change = current_value minus original_value, in AUD as published.
> 
> Top grower CN4020722 (Maintenance Services, Department of Defence) grew from \$8\.2M to \$1,566\.4M, a rise of \$1,558\.2M. Next were CN4050683 up by \$1,518\.8M and CN4041170 up by \$1,369\.8M.

**Table: Top 10 growers published in 2023-24, from original to current**

| Notice id | Agency name | Description | Original value | Current value | Value change | Amendment count |
|---|---|---|---|---|---|---|
| CN4020722 | Department of Defence | Maintenance Services | $8.2M | $1,566.4M | $1,558.2M | 7 |
| CN4050683 | Department of Defence | Joint Air Battle Management System | $624.6M | $2,143.4M | $1,518.8M | 2 |
| CN4041170 | Department of Defence | Aircraft | $1,286.6M | $2,656.4M | $1,369.8M | 3 |
| CN4004023 | Department of Defence | Relocation Services | $298.6M | $1,270.5M | $971.9M | 3 |
| CN3982994 | Department of Defence | Strategic Management Services | $10.9M | $933.9M | $923.1M | 30 |
| CN4002456 | Department of Defence | Construction Support Services | $1.6M | $879.9M | $878.2M | 7 |
| CN4062462 | Department of Defence | Managing Contractor Services | $31.3M | $458.4M | $427.1M | 6 |
| CN4029827 | Department of Defence | Building and Construction Services | $12.4M | $421.8M | $409.3M | 7 |
| CN3991067 | Department of Health, Disability and Ageing | Supply of Essential Vaccines | $99.0M | $504.4M | $405.4M | 3 |
| CN4023271 | Department of Defence | Electronic Equipment | $72.9M | $452.0M | $379.1M | 5 |

**Chart: Growth through amendments (current minus original)**

| notice_id | value_change |
|---|---|
| CN4020722 | 1558.1778841199998 |
| CN4050683 | 1518.7672098599999 |
| CN4041170 | 1369.79224015 |
| CN4004023 | 971.87403034 |
| CN3982994 | 923.0879423599999 |
| CN4002456 | 878.2399269099999 |
| CN4062462 | 427.12672298999996 |
| CN4029827 | 409.34814183 |
| CN3991067 | 405.43050000999995 |
| CN4023271 | 379.06815358999995 |

<details><summary>SQL: top 10 growers FY 2023-24</summary>

```sql
SELECT notice_id, agency_name, description, publish_date, original_value, current_value, value_change, amendment_count, supplier_count FROM mart.contract WHERE financial_year='2023-24' AND NOT is_cancelled AND original_value IS NOT NULL AND current_value IS NOT NULL AND value_change IS NOT NULL ORDER BY value_change DESC LIMIT 10
```
</details>

<details><summary>SQL: top 10 growers FY 2023-24</summary>

```sql
SELECT notice_id, agency_name, description, publish_date, original_value, current_value, value_change, amendment_count, supplier_count FROM mart.contract WHERE financial_year='2023-24' AND NOT is_cancelled AND original_value IS NOT NULL AND current_value IS NOT NULL AND value_change IS NOT NULL ORDER BY value_change DESC LIMIT 10
```
</details>

Score: 

### Q3 · low · run 2

Failure: `none` · 4.5 s · 2 model calls · 0 repairs · bare numbers: 10, 2023, 24, 1, 2023, 30, 2024

**Prose:**

> Top 10 contracts published in FY 2023-24 (1 July 2023 – 30 June 2024) ranked by growth through amendments (`current_value − original_value`, AUD as published, non-cancelled).
> 
> The largest uplift was \$1,558\.2M, from \$8\.2M to \$1,566\.4M on CN4020722 — Maintenance Services.

**Metrics:** FY 2023-24 by publish_date. Values are current contract values as published at award/amendment, not payments. 9 of 10 are Department of Defence.: Contracts published 2023-24 with largest amendment growth

**Table: Top 10 contracts by amendment growth, published 2023-24**

| Notice id | Agency name | Description | Original value | Current value | Value change | Amendment count |
|---|---|---|---|---|---|---|
| CN4020722 | Department of Defence | Maintenance Services | $8.2M | $1,566.4M | $1,558.2M | 7 |
| CN4050683 | Department of Defence | Joint Air Battle Management System | $624.6M | $2,143.4M | $1,518.8M | 2 |
| CN4041170 | Department of Defence | Aircraft | $1,286.6M | $2,656.4M | $1,369.8M | 3 |
| CN4004023 | Department of Defence | Relocation Services | $298.6M | $1,270.5M | $971.9M | 3 |
| CN3982994 | Department of Defence | Strategic Management Services | $10.9M | $933.9M | $923.1M | 30 |
| CN4002456 | Department of Defence | Construction Support Services | $1.6M | $879.9M | $878.2M | 7 |
| CN4062462 | Department of Defence | Managing Contractor Services | $31.3M | $458.4M | $427.1M | 6 |
| CN4029827 | Department of Defence | Building and Construction Services | $12.4M | $421.8M | $409.3M | 7 |
| CN3991067 | Department of Health, Disability and Ageing | Supply of Essential Vaccines | $99.0M | $504.4M | $405.4M | 3 |
| CN4023271 | Department of Defence | Electronic Equipment | $72.9M | $452.0M | $379.1M | 5 |

**Chart: Value growth (current minus original)**

| notice_id | value_change |
|---|---|
| CN4020722 | 1558.1778841199998 |
| CN4050683 | 1518.7672098599999 |
| CN4041170 | 1369.79224015 |
| CN4004023 | 971.87403034 |
| CN3982994 | 923.0879423599999 |
| CN4002456 | 878.2399269099999 |
| CN4062462 | 427.12672298999996 |
| CN4029827 | 409.34814183 |
| CN3991067 | 405.43050000999995 |
| CN4023271 | 379.06815358999995 |

<details><summary>SQL: top 10 growth contracts 2023-24</summary>

```sql
SELECT notice_id, agency_name, description, original_value, current_value, value_change, amendment_count, procurement_method FROM mart.contract WHERE financial_year='2023-24' AND NOT is_cancelled AND original_value IS NOT NULL AND current_value IS NOT NULL AND value_change > 0 ORDER BY value_change DESC LIMIT 10
```
</details>

<details><summary>SQL: top 10 growth contracts 2023-24</summary>

```sql
SELECT notice_id, agency_name, description, original_value, current_value, value_change, amendment_count, procurement_method FROM mart.contract WHERE financial_year='2023-24' AND NOT is_cancelled AND original_value IS NOT NULL AND current_value IS NOT NULL AND value_change > 0 ORDER BY value_change DESC LIMIT 10
```
</details>

Score: 

### Q3 · low · run 3

Failure: `none` · 5.0 s · 2 model calls · 0 repairs · bare numbers: 10, 2023, 24, 1, 2023, 30, 2024, 1.5, 8, 1.57

**Prose:**

> The 10 contracts **published in FY 2023-24** (1 Jul 2023 – 30 Jun 2024) with the largest amendment growth, non-cancelled only. Values are AUD current vs original published values, not payments.
> 
> Top grower grew by over $1.5bn, from ~$8m to ~$1.57bn.

**Table: Top 10 contracts by value growth through amendments, FY 2023-24**

| Notice id | Agency name | Descr | Original value | Current value | Value change | Amendment count |
|---|---|---|---|---|---|---|
| CN4020722 | Department of Defence | Maintenance Services | $8.2M | $1,566.4M | $1,558.2M | 7 |
| CN4050683 | Department of Defence | Joint Air Battle Management System | $624.6M | $2,143.4M | $1,518.8M | 2 |
| CN4041170 | Department of Defence | Aircraft | $1,286.6M | $2,656.4M | $1,369.8M | 3 |
| CN4004023 | Department of Defence | Relocation Services | $298.6M | $1,270.5M | $971.9M | 3 |
| CN3982994 | Department of Defence | Strategic Management Services | $10.9M | $933.9M | $923.1M | 30 |
| CN4002456 | Department of Defence | Construction Support Services | $1.6M | $879.9M | $878.2M | 7 |
| CN4062462 | Department of Defence | Managing Contractor Services | $31.3M | $458.4M | $427.1M | 6 |
| CN4029827 | Department of Defence | Building and Construction Services | $12.4M | $421.8M | $409.3M | 7 |
| CN3991067 | Department of Health, Disability and Ageing | Supply of Essential Vaccines | $99.0M | $504.4M | $405.4M | 3 |
| CN4023271 | Department of Defence | Electronic Equipment | $72.9M | $452.0M | $379.1M | 5 |

<details><summary>SQL: Top 10 contracts 2023-24 by amendment growth</summary>

```sql
SELECT notice_id, agency_name, LEFT(description, 120) AS descr, original_value, current_value, value_change, amendment_count FROM mart.contract WHERE financial_year = '2023-24' AND NOT is_cancelled AND value_change > 0 ORDER BY value_change DESC LIMIT 10
```
</details>

Score: 

### Q3 · minimal · run 1

Failure: `none` · 3.5 s · 2 model calls · 0 repairs · bare numbers: 10, 2023, 24

**Prose:**

> Top 10 contracts published in FY 2023-24 ranked by amendment growth (`current_value - original_value`, non-cancelled).
> 
> The largest uplift was CN4020722 at \$1,558\.2M, growing from \$8\.2M to \$1,566\.4M.

**Metrics:** Largest growth: $1,558.2M; Its current value: $1,566.4M; Its original value: $8.2M; FY 2023-24 publish date, non-cancelled. Growth = current value minus original value (AUD as published).: Largest amendment growth, contracts published 2023-24

**Table: Top 10 by amendment growth**

| Notice id | Agency name | Description | Original value | Current value | Value change | Amendment count |
|---|---|---|---|---|---|---|
| CN4020722 | Department of Defence | Maintenance Services | $8.2M | $1,566.4M | $1,558.2M | 7 |
| CN4050683 | Department of Defence | Joint Air Battle Management System | $624.6M | $2,143.4M | $1,518.8M | 2 |
| CN4041170 | Department of Defence | Aircraft | $1,286.6M | $2,656.4M | $1,369.8M | 3 |
| CN4004023 | Department of Defence | Relocation Services | $298.6M | $1,270.5M | $971.9M | 3 |
| CN3982994 | Department of Defence | Strategic Management Services | $10.9M | $933.9M | $923.1M | 30 |
| CN4002456 | Department of Defence | Construction Support Services | $1.6M | $879.9M | $878.2M | 7 |
| CN4062462 | Department of Defence | Managing Contractor Services | $31.3M | $458.4M | $427.1M | 6 |
| CN4029827 | Department of Defence | Building and Construction Services | $12.4M | $421.8M | $409.3M | 7 |
| CN3991067 | Department of Health, Disability and Ageing | Supply of Essential Vaccines | $99.0M | $504.4M | $405.4M | 3 |
| CN4023271 | Department of Defence | Electronic Equipment | $72.9M | $452.0M | $379.1M | 5 |

**Chart: Growth by contract (AUD)**

| notice_id | value_change |
|---|---|
| CN4020722 | 1558.1778841199998 |
| CN4050683 | 1518.7672098599999 |
| CN4041170 | 1369.79224015 |
| CN4004023 | 971.87403034 |
| CN3982994 | 923.0879423599999 |
| CN4002456 | 878.2399269099999 |
| CN4062462 | 427.12672298999996 |
| CN4029827 | 409.34814183 |
| CN3991067 | 405.43050000999995 |
| CN4023271 | 379.06815358999995 |

<details><summary>SQL: top 10 growth contracts 2023-24</summary>

```sql
SELECT notice_id, agency_name, description, original_value, current_value, value_change, amendment_count FROM mart.contract WHERE financial_year='2023-24' AND NOT is_cancelled AND original_value IS NOT NULL AND current_value IS NOT NULL ORDER BY value_change DESC LIMIT 10
```
</details>

<details><summary>SQL: top 10 growth contracts 2023-24</summary>

```sql
SELECT notice_id, agency_name, description, original_value, current_value, value_change, amendment_count FROM mart.contract WHERE financial_year='2023-24' AND NOT is_cancelled AND original_value IS NOT NULL AND current_value IS NOT NULL ORDER BY value_change DESC LIMIT 10
```
</details>

<details><summary>SQL: top 10 growth contracts 2023-24</summary>

```sql
SELECT notice_id, agency_name, description, original_value, current_value, value_change, amendment_count FROM mart.contract WHERE financial_year='2023-24' AND NOT is_cancelled AND original_value IS NOT NULL AND current_value IS NOT NULL ORDER BY value_change DESC LIMIT 10
```
</details>

<details><summary>SQL: top 10 growth contracts 2023-24</summary>

```sql
SELECT notice_id, agency_name, description, original_value, current_value, value_change, amendment_count FROM mart.contract WHERE financial_year='2023-24' AND NOT is_cancelled AND original_value IS NOT NULL AND current_value IS NOT NULL ORDER BY value_change DESC LIMIT 10
```
</details>

<details><summary>SQL: top 10 growth contracts 2023-24</summary>

```sql
SELECT notice_id, agency_name, description, original_value, current_value, value_change, amendment_count FROM mart.contract WHERE financial_year='2023-24' AND NOT is_cancelled AND original_value IS NOT NULL AND current_value IS NOT NULL ORDER BY value_change DESC LIMIT 10
```
</details>

Score: 

### Q3 · minimal · run 2

Failure: `none` · 4.0 s · 2 model calls · 0 repairs · bare numbers: 2023, 24, 1, 2023, 30, 2024

**Prose:**

> Largest growth through amendments for contracts published in FY 2023-24 (1 Jul 2023 – 30 Jun 2024, by `publish_date`). Growth = `current_value` minus `original_value` for non-cancelled contracts.
> 
> Top grower `CN4020722` (Maintenance Services) grew by `\$1,558\.2M` from `\$8\.2M` to `\$1,566\.4M`.

**Metrics:** Largest growth (value_change): $1,558.2M; FY 2023-24 (1 Jul 2023 – 30 Jun 2024). Growth = current value minus original value, non-cancelled contracts.: Top 10 contracts by amendment growth, published 2023-24

**Table: Top 10 contracts by amendment growth**

| Notice id | Agency name | Description | Original value | Current value | Value change | Amendment count |
|---|---|---|---|---|---|---|
| CN4020722 | Department of Defence | Maintenance Services | $8.2M | $1,566.4M | $1,558.2M | 7 |
| CN4050683 | Department of Defence | Joint Air Battle Management System | $624.6M | $2,143.4M | $1,518.8M | 2 |
| CN4041170 | Department of Defence | Aircraft | $1,286.6M | $2,656.4M | $1,369.8M | 3 |
| CN4004023 | Department of Defence | Relocation Services | $298.6M | $1,270.5M | $971.9M | 3 |
| CN3982994 | Department of Defence | Strategic Management Services | $10.9M | $933.9M | $923.1M | 30 |
| CN4002456 | Department of Defence | Construction Support Services | $1.6M | $879.9M | $878.2M | 7 |
| CN4062462 | Department of Defence | Managing Contractor Services | $31.3M | $458.4M | $427.1M | 6 |
| CN4029827 | Department of Defence | Building and Construction Services | $12.4M | $421.8M | $409.3M | 7 |
| CN3991067 | Department of Health, Disability and Ageing | Supply of Essential Vaccines | $99.0M | $504.4M | $405.4M | 3 |
| CN4023271 | Department of Defence | Electronic Equipment | $72.9M | $452.0M | $379.1M | 5 |

<details><summary>SQL: top 10 growth contracts 2023-24</summary>

```sql
SELECT notice_id, agency_name, description, original_value, current_value, value_change, amendment_count FROM mart.contract WHERE financial_year='2023-24' AND NOT is_cancelled AND original_value IS NOT NULL AND current_value IS NOT NULL AND value_change > 0 ORDER BY value_change DESC LIMIT 10
```
</details>

<details><summary>SQL: top 10 growth contracts 2023-24</summary>

```sql
SELECT notice_id, agency_name, description, original_value, current_value, value_change, amendment_count FROM mart.contract WHERE financial_year='2023-24' AND NOT is_cancelled AND original_value IS NOT NULL AND current_value IS NOT NULL AND value_change > 0 ORDER BY value_change DESC LIMIT 10
```
</details>

Score: 

### Q3 · minimal · run 3

Failure: `none` · 2.9 s · 2 model calls · 0 repairs · bare numbers: 10, 2023, 24, 1, 2023, 30, 2024

**Prose:**

> Top 10 contracts published in FY 2023-24 (1 Jul 2023–30 Jun 2024) ranked by amendment growth (`current_value − original_value`, non-cancelled only).
> 
> The largest increase was CN4020722 at \$1,558\.2M, from \$8\.2M to \$1,566\.4M.

**Table: Top 10 contracts by value growth, FY 2023-24**

| Notice id | Agency name | Description | Original value | Current value | Value change | Amendment count |
|---|---|---|---|---|---|---|
| CN4020722 | Department of Defence | Maintenance Services | $8.2M | $1,566.4M | $1,558.2M | 7 |
| CN4050683 | Department of Defence | Joint Air Battle Management System | $624.6M | $2,143.4M | $1,518.8M | 2 |
| CN4041170 | Department of Defence | Aircraft | $1,286.6M | $2,656.4M | $1,369.8M | 3 |
| CN4004023 | Department of Defence | Relocation Services | $298.6M | $1,270.5M | $971.9M | 3 |
| CN3982994 | Department of Defence | Strategic Management Services | $10.9M | $933.9M | $923.1M | 30 |
| CN4002456 | Department of Defence | Construction Support Services | $1.6M | $879.9M | $878.2M | 7 |
| CN4062462 | Department of Defence | Managing Contractor Services | $31.3M | $458.4M | $427.1M | 6 |
| CN4029827 | Department of Defence | Building and Construction Services | $12.4M | $421.8M | $409.3M | 7 |
| CN3991067 | Department of Health, Disability and Ageing | Supply of Essential Vaccines | $99.0M | $504.4M | $405.4M | 3 |
| CN4023271 | Department of Defence | Electronic Equipment | $72.9M | $452.0M | $379.1M | 5 |

**Chart: Growth through amendments (AUD)**

| notice_id | value_change |
|---|---|
| CN4020722 | 1558.1778841199998 |
| CN4050683 | 1518.7672098599999 |
| CN4041170 | 1369.79224015 |
| CN4004023 | 971.87403034 |
| CN3982994 | 923.0879423599999 |
| CN4002456 | 878.2399269099999 |
| CN4062462 | 427.12672298999996 |
| CN4029827 | 409.34814183 |
| CN3991067 | 405.43050000999995 |
| CN4023271 | 379.06815358999995 |

<details><summary>SQL: top 10 growth contracts 2023-24</summary>

```sql
SELECT notice_id, agency_name, description, original_value, current_value, value_change, amendment_count FROM mart.contract WHERE financial_year='2023-24' AND NOT is_cancelled AND original_value IS NOT NULL AND current_value IS NOT NULL AND value_change > 0 ORDER BY value_change DESC LIMIT 10
```
</details>

<details><summary>SQL: top 10 growth contracts 2023-24</summary>

```sql
SELECT notice_id, agency_name, description, original_value, current_value, value_change, amendment_count FROM mart.contract WHERE financial_year='2023-24' AND NOT is_cancelled AND original_value IS NOT NULL AND current_value IS NOT NULL AND value_change > 0 ORDER BY value_change DESC LIMIT 10
```
</details>

Score: 

## Q4. How has total contract value changed by financial year since the data starts?

Reference: [Q4](reference-values.md#q4)

### Q4 · low · run 1

Failure: `none` · 4.9 s · 2 model calls · 0 repairs · bare numbers: 2012, 13, 2015, 16, 2012, 13, 2013

**Prose:**

> Total contract value (current values as published, excluding cancelled, by financial year July–June) rose sharply after the partial year `2012-13`, peaked in `2015-16`, then fluctuated.
> 
> Peak was \$92,305\.2M in 2015\-16 with 62,330 contracts.
> 
> Note: `2012-13` covers only Jan–Jun 2013, and the latest months may be incomplete.

**Metrics:** 2015-16 peak value: $92,305.2M; 2024-25 value: $62,257.0M; 2024-25 contracts: 58,201

**Table: Yearly totals**

| Financial year | Total value | Contract count |
|---|---|---|
| 2012-13 | $15,111.9M | 29,983 |
| 2013-14 | $45,531.9M | 59,259 |
| 2014-15 | $78,758.5M | 61,553 |
| 2015-16 | $92,305.2M | 62,330 |
| 2016-17 | $59,878.0M | 58,060 |
| 2017-18 | $66,095.3M | 59,662 |
| 2018-19 | $83,381.2M | 61,623 |
| 2019-20 | $53,691.5M | 62,763 |
| 2020-21 | $63,549.6M | 63,625 |
| 2021-22 | $74,641.1M | 68,808 |
| 2022-23 | $66,758.1M | 57,913 |
| 2023-24 | $83,312.3M | 59,936 |
| 2024-25 | $62,257.0M | 58,201 |
| 2025-26 | $85,558.0M | 56,173 |
| 2026-27 | $18,810.3M | 18,697 |

**Chart: Total contract value by financial year**

| financial_year | total_value |
|---|---|
| 2012-13 | 15111.90226386 |
| 2013-14 | 45531.91359513 |
| 2014-15 | 78758.48009239999 |
| 2015-16 | 92305.15911319999 |
| 2016-17 | 59877.967772239994 |
| 2017-18 | 66095.32403645 |
| 2018-19 | 83381.16968467999 |
| 2019-20 | 53691.46880767 |
| 2020-21 | 63549.594821089995 |
| 2021-22 | 74641.09492060001 |
| 2022-23 | 66758.05335055 |
| 2023-24 | 83312.32799356 |
| 2024-25 | 62257.02776751 |
| 2025-26 | 85557.98710951 |
| 2026-27 | 18810.27247609 |

<details><summary>SQL: total value by financial year</summary>

```sql
SELECT financial_year, SUM(total_value) AS total_value, SUM(contract_count) AS contract_count FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

<details><summary>SQL: total value by financial year</summary>

```sql
SELECT financial_year, SUM(total_value) AS total_value, SUM(contract_count) AS contract_count FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

<details><summary>SQL: total value by financial year</summary>

```sql
SELECT financial_year, SUM(total_value) AS total_value, SUM(contract_count) AS contract_count FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

<details><summary>SQL: total value by financial year</summary>

```sql
SELECT financial_year, SUM(total_value) AS total_value, SUM(contract_count) AS contract_count FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

<details><summary>SQL: total value by financial year</summary>

```sql
SELECT financial_year, SUM(total_value) AS total_value, SUM(contract_count) AS contract_count FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

Score: 

### Q4 · low · run 2

Failure: `none` · 8.5 s · 3 model calls · 0 repairs · bare numbers: 2010, 2019, 20

**Prose:**

> Total contract value by Australian financial year (July–June), current values as published, non-cancelled contracts from `mart.agency_fy`.
> 
> Trend is cyclical with a peak in the mid-2010s, a dip around 2019-20, then recovery.

**Metrics:** Peak (2015-16): $92,305.2M; 2025-26 (last complete): $85,558.0M

**Table: Total value by financial year**

| Financial year | Total value |
|---|---|
| 2012-13 | $15,111.9M |
| 2013-14 | $45,531.9M |
| 2014-15 | $78,758.5M |
| 2015-16 | $92,305.2M |
| 2016-17 | $59,878.0M |
| 2017-18 | $66,095.3M |
| 2018-19 | $83,381.2M |
| 2019-20 | $53,691.5M |
| 2020-21 | $63,549.6M |
| 2021-22 | $74,641.1M |
| 2022-23 | $66,758.1M |
| 2023-24 | $83,312.3M |
| 2024-25 | $62,257.0M |
| 2025-26 | $85,558.0M |
| 2026-27 | $18,810.3M |

**Chart: Total value by financial year**

| financial_year | total_value |
|---|---|
| 2012-13 | 15111.90226386 |
| 2013-14 | 45531.91359513 |
| 2014-15 | 78758.48009239999 |
| 2015-16 | 92305.15911319999 |
| 2016-17 | 59877.967772239994 |
| 2017-18 | 66095.32403645 |
| 2018-19 | 83381.16968467999 |
| 2019-20 | 53691.46880767 |
| 2020-21 | 63549.594821089995 |
| 2021-22 | 74641.09492060001 |
| 2022-23 | 66758.05335055 |
| 2023-24 | 83312.32799356 |
| 2024-25 | 62257.02776751 |
| 2025-26 | 85557.98710951 |
| 2026-27 | 18810.27247609 |

<details><summary>SQL: total value by financial year retry</summary>

```sql
SELECT financial_year, SUM(total_value) AS total_value FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

<details><summary>SQL: total value by financial year retry</summary>

```sql
SELECT financial_year, SUM(total_value) AS total_value FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

<details><summary>SQL: total value by financial year retry</summary>

```sql
SELECT financial_year, SUM(total_value) AS total_value FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

<details><summary>SQL: total value by financial year retry</summary>

```sql
SELECT financial_year, SUM(total_value) AS total_value FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

Score: 

### Q4 · low · run 3

Failure: `none` · 5.0 s · 2 model calls · 0 repairs · bare numbers: 2012, 13, 2015, 16

**Prose:**

> Total Commonwealth contract value has fluctuated markedly since 2012-13, using financial years (July–June) and current contract values as published, excluding cancelled contracts (source: `mart.agency_fy`).
> 
> Values rose sharply from the partial starting year to a peak in 2015-16, then fell and have cycled since; the latest years may be incomplete.

**Table: Yearly totals**

| Financial year | Total value | Contract count |
|---|---|---|
| 2012-13 | $15,111.9M | 29,983 |
| 2013-14 | $45,531.9M | 59,259 |
| 2014-15 | $78,758.5M | 61,553 |
| 2015-16 | $92,305.2M | 62,330 |
| 2016-17 | $59,878.0M | 58,060 |
| 2017-18 | $66,095.3M | 59,662 |
| 2018-19 | $83,381.2M | 61,623 |
| 2019-20 | $53,691.5M | 62,763 |
| 2020-21 | $63,549.6M | 63,625 |
| 2021-22 | $74,641.1M | 68,808 |
| 2022-23 | $66,758.1M | 57,913 |
| 2023-24 | $83,312.3M | 59,936 |
| 2024-25 | $62,257.0M | 58,201 |
| 2025-26 | $85,558.0M | 56,173 |
| 2026-27 | $18,810.3M | 18,697 |

**Chart: Total contract value by financial year**

| financial_year | total_value |
|---|---|
| 2012-13 | 15111.90226386 |
| 2013-14 | 45531.91359513 |
| 2014-15 | 78758.48009239999 |
| 2015-16 | 92305.15911319999 |
| 2016-17 | 59877.967772239994 |
| 2017-18 | 66095.32403645 |
| 2018-19 | 83381.16968467999 |
| 2019-20 | 53691.46880767 |
| 2020-21 | 63549.594821089995 |
| 2021-22 | 74641.09492060001 |
| 2022-23 | 66758.05335055 |
| 2023-24 | 83312.32799356 |
| 2024-25 | 62257.02776751 |
| 2025-26 | 85557.98710951 |
| 2026-27 | 18810.27247609 |

<details><summary>SQL: total value by financial year</summary>

```sql
SELECT financial_year, SUM(total_value) AS total_value, SUM(contract_count) AS contract_count FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

<details><summary>SQL: total value by financial year</summary>

```sql
SELECT financial_year, SUM(total_value) AS total_value, SUM(contract_count) AS contract_count FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

Score: 

### Q4 · minimal · run 1

Failure: `none` · 3.1 s · 2 model calls · 0 repairs · bare numbers: 2012, 13, 2015, 16, 2025, 26

**Prose:**

> Total contract value (current value of non-cancelled contracts) by Australian financial year, July–June, from 2012-13 onwards.
> 
> Peak spend in the series was in 2015-16, with a second high in 2025-26 which is still incomplete and may include forward-dated notices.

**Metrics:** Non-cancelled contracts, current (amended) value, AUD as published. Financial years July–June.: Total AusTender contract value by financial year

**Table: Yearly totals**

| Financial year | Contracts | Total value |
|---|---|---|
| 2012-13 | 29,983 | $15,111.9M |
| 2013-14 | 59,259 | $45,531.9M |
| 2014-15 | 61,553 | $78,758.5M |
| 2015-16 | 62,330 | $92,305.2M |
| 2016-17 | 58,060 | $59,878.0M |
| 2017-18 | 59,662 | $66,095.3M |
| 2018-19 | 61,623 | $83,381.2M |
| 2019-20 | 62,763 | $53,691.5M |
| 2020-21 | 63,625 | $63,549.6M |
| 2021-22 | 68,808 | $74,641.1M |
| 2022-23 | 57,913 | $66,758.1M |
| 2023-24 | 59,936 | $83,312.3M |
| 2024-25 | 58,201 | $62,257.0M |
| 2025-26 | 56,173 | $85,558.0M |
| 2026-27 | 18,697 | $18,810.3M |

**Chart: Total value by financial year**

| financial_year | total_value |
|---|---|
| 2012-13 | 15111.90226386 |
| 2013-14 | 45531.91359513 |
| 2014-15 | 78758.48009239999 |
| 2015-16 | 92305.15911319999 |
| 2016-17 | 59877.967772239994 |
| 2017-18 | 66095.32403645 |
| 2018-19 | 83381.16968467999 |
| 2019-20 | 53691.46880767 |
| 2020-21 | 63549.594821089995 |
| 2021-22 | 74641.09492060001 |
| 2022-23 | 66758.05335055 |
| 2023-24 | 83312.32799356 |
| 2024-25 | 62257.02776751 |
| 2025-26 | 85557.98710951 |
| 2026-27 | 18810.27247609 |

**Chart: Contract count by financial year**

| financial_year | contracts |
|---|---|
| 2012-13 | 29983.0 |
| 2013-14 | 59259.0 |
| 2014-15 | 61553.0 |
| 2015-16 | 62330.0 |
| 2016-17 | 58060.0 |
| 2017-18 | 59662.0 |
| 2018-19 | 61623.0 |
| 2019-20 | 62763.0 |
| 2020-21 | 63625.0 |
| 2021-22 | 68808.0 |
| 2022-23 | 57913.0 |
| 2023-24 | 59936.0 |
| 2024-25 | 58201.0 |
| 2025-26 | 56173.0 |
| 2026-27 | 18697.0 |

<details><summary>SQL: total value by FY</summary>

```sql
SELECT financial_year, COUNT(*) AS contracts, SUM(current_value) AS total_value FROM mart.contract WHERE NOT is_cancelled AND current_value IS NOT NULL GROUP BY financial_year ORDER BY financial_year
```
</details>

<details><summary>SQL: total value by FY</summary>

```sql
SELECT financial_year, COUNT(*) AS contracts, SUM(current_value) AS total_value FROM mart.contract WHERE NOT is_cancelled AND current_value IS NOT NULL GROUP BY financial_year ORDER BY financial_year
```
</details>

<details><summary>SQL: total value by FY</summary>

```sql
SELECT financial_year, COUNT(*) AS contracts, SUM(current_value) AS total_value FROM mart.contract WHERE NOT is_cancelled AND current_value IS NOT NULL GROUP BY financial_year ORDER BY financial_year
```
</details>

Score: 

### Q4 · minimal · run 2

Failure: `none` · 3.4 s · 2 model calls · 0 repairs · bare numbers: 2012, 13, 2013, 2026, 27, 2015, 16, 2023, 24

**Prose:**

> Total published contract value (current values, non-cancelled) by Australian financial year (July–June), from `mart.agency_fy`. Note: `2012-13` is partial (data starts Jan 2013) and `2026-27` is incomplete.
> 
> Peak full year in this series was `\$92,305\.2M` in `2015-16`, with a second high of `\$83,312\.3M` in `2023-24`.

**Table: Value and contract count by FY**

| Financial year | Value | Contracts |
|---|---|---|
| 2012-13 | 15,111,902,263.86 | 29,983 |
| 2013-14 | 45,531,913,595.13 | 59,259 |
| 2014-15 | 78,758,480,092.40 | 61,553 |
| 2015-16 | 92,305,159,113.20 | 62,330 |
| 2016-17 | 59,877,967,772.24 | 58,060 |
| 2017-18 | 66,095,324,036.45 | 59,662 |
| 2018-19 | 83,381,169,684.68 | 61,623 |
| 2019-20 | 53,691,468,807.67 | 62,763 |
| 2020-21 | 63,549,594,821.09 | 63,625 |
| 2021-22 | 74,641,094,920.60 | 68,808 |
| 2022-23 | 66,758,053,350.55 | 57,913 |
| 2023-24 | 83,312,327,993.56 | 59,936 |
| 2024-25 | 62,257,027,767.51 | 58,201 |
| 2025-26 | 85,557,987,109.51 | 56,173 |
| 2026-27 | 18,810,272,476.09 | 18,697 |

**Chart: Total value by FY**

| financial_year | value |
|---|---|
| 2012-13 | 15111.90226386 |
| 2013-14 | 45531.91359513 |
| 2014-15 | 78758.48009239999 |
| 2015-16 | 92305.15911319999 |
| 2016-17 | 59877.967772239994 |
| 2017-18 | 66095.32403645 |
| 2018-19 | 83381.16968467999 |
| 2019-20 | 53691.46880767 |
| 2020-21 | 63549.594821089995 |
| 2021-22 | 74641.09492060001 |
| 2022-23 | 66758.05335055 |
| 2023-24 | 83312.32799356 |
| 2024-25 | 62257.02776751 |
| 2025-26 | 85557.98710951 |
| 2026-27 | 18810.27247609 |

<details><summary>SQL: total value by FY</summary>

```sql
SELECT financial_year, SUM(total_value) AS value, SUM(contract_count) AS contracts FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

<details><summary>SQL: total value by FY</summary>

```sql
SELECT financial_year, SUM(total_value) AS value, SUM(contract_count) AS contracts FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

Score: 

### Q4 · minimal · run 3

Failure: `none` · 3.3 s · 2 model calls · 0 repairs · bare numbers: 2012, 13, 2013, 2025, 26, 2026, 27

**Prose:**

> Total contract value by Australian financial year (July–June), from `mart.agency_fy` excluding cancelled contracts. Values are award/amendment values as published, not payments.
> 
> Note: `2012-13` is partial (data starts Jan 2013) and `2025-26` / `2026-27` are incomplete / current.

**Metrics:** Peak value (2015-16): $92,305.2M; 2024-25 value: $62,257.0M; 2024-25 contracts: 58,201

**Table: Yearly totals**

| Financial year | Value | Contracts |
|---|---|---|
| 2012-13 | $15,111.9M | 29,983 |
| 2013-14 | $45,531.9M | 59,259 |
| 2014-15 | $78,758.5M | 61,553 |
| 2015-16 | $92,305.2M | 62,330 |
| 2016-17 | $59,878.0M | 58,060 |
| 2017-18 | $66,095.3M | 59,662 |
| 2018-19 | $83,381.2M | 61,623 |
| 2019-20 | $53,691.5M | 62,763 |
| 2020-21 | $63,549.6M | 63,625 |
| 2021-22 | $74,641.1M | 68,808 |
| 2022-23 | $66,758.1M | 57,913 |
| 2023-24 | $83,312.3M | 59,936 |
| 2024-25 | $62,257.0M | 58,201 |
| 2025-26 | $85,558.0M | 56,173 |
| 2026-27 | $18,810.3M | 18,697 |

**Chart: Total value by financial year**

| financial_year | value |
|---|---|
| 2012-13 | 15111.90226386 |
| 2013-14 | 45531.91359513 |
| 2014-15 | 78758.48009239999 |
| 2015-16 | 92305.15911319999 |
| 2016-17 | 59877.967772239994 |
| 2017-18 | 66095.32403645 |
| 2018-19 | 83381.16968467999 |
| 2019-20 | 53691.46880767 |
| 2020-21 | 63549.594821089995 |
| 2021-22 | 74641.09492060001 |
| 2022-23 | 66758.05335055 |
| 2023-24 | 83312.32799356 |
| 2024-25 | 62257.02776751 |
| 2025-26 | 85557.98710951 |
| 2026-27 | 18810.27247609 |

<details><summary>SQL: total value by FY</summary>

```sql
SELECT financial_year, SUM(total_value) AS value, SUM(contract_count) AS contracts FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

<details><summary>SQL: total value by FY</summary>

```sql
SELECT financial_year, SUM(total_value) AS value, SUM(contract_count) AS contracts FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

<details><summary>SQL: total value by FY</summary>

```sql
SELECT financial_year, SUM(total_value) AS value, SUM(contract_count) AS contracts FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

<details><summary>SQL: total value by FY</summary>

```sql
SELECT financial_year, SUM(total_value) AS value, SUM(contract_count) AS contracts FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

<details><summary>SQL: total value by FY</summary>

```sql
SELECT financial_year, SUM(total_value) AS value, SUM(contract_count) AS contracts FROM mart.agency_fy GROUP BY financial_year ORDER BY financial_year
```
</details>

Score: 

## Q5. What share of 2024-25 contract value went to overseas suppliers?

Reference: [Q5](reference-values.md#q5)

### Q5 · low · run 1

Failure: `none` · 5.5 s · 2 model calls · 0 repairs · bare numbers: 2024, 25, 1, 2024, 30, 2025, 2024, 25

**Prose:**

> In 2024-25 (financial year 1 Jul 2024 to 30 Jun 2025), overseas suppliers received a small share of Commonwealth contract value.
> 
> Basis: `mart.contract_supplier` `value_share` summed for `financial_year='2024-25'`; excludes cancelled contracts and contracts with no published value; multi-supplier values split equally.

**Metrics:** Total contract value 2024-25: $62,257.0M; Overseas supplier value: $4,106.3M; Overseas share: 6.6%; FY 2024-25, July-June. Value basis: current published value split by value_share. Excludes cancelled contracts.: Overseas share of 2024-25 contract value

**Table: Overseas vs domestic value 2024-25**

| Contracts | Total value | Overseas value | Domestic value | Overseas share |
|---|---|---|---|---|
| 58,201 | 62,257,027,767.51 | 4,106,321,710.92 | 58,150,706,056.59 | 0.07 |

<details><summary>SQL: overseas share 2024-25</summary>

```sql
SELECT COUNT(*) AS contracts, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(CASE WHEN NOT is_overseas THEN value_share ELSE 0 END) AS domestic_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

<details><summary>SQL: overseas share 2024-25</summary>

```sql
SELECT COUNT(*) AS contracts, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(CASE WHEN NOT is_overseas THEN value_share ELSE 0 END) AS domestic_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

<details><summary>SQL: overseas share 2024-25</summary>

```sql
SELECT COUNT(*) AS contracts, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(CASE WHEN NOT is_overseas THEN value_share ELSE 0 END) AS domestic_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

<details><summary>SQL: overseas share 2024-25</summary>

```sql
SELECT COUNT(*) AS contracts, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(CASE WHEN NOT is_overseas THEN value_share ELSE 0 END) AS domestic_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

Score: 

### Q5 · low · run 2

Failure: `none` · 7.5 s · 3 model calls · 0 repairs · bare numbers: 2024, 25, 2024, 25, 1, 2024, 30, 2025

**Prose:**

> Overseas suppliers received 6\.6% of 2024-25 contract value — \$4,106\.3M of \$62,257\.0M.
> 
> Basis: Australian financial year 2024-25 (1 Jul 2024–30 Jun 2025), sum of `value_share` in `mart.contract_supplier` (current contract values, multi-supplier values split, cancelled excluded, contracts with no supplier excluded).

**Metrics:** Overseas value: $4,106.3M; Total value (with suppliers): $62,257.0M; Overseas share: 6.6%

**Table: 2024-25 value by supplier location**

| Is overseas | Contracts rows | Contracts | Total value |
|---|---|---|---|
| false | 54,030 | 54,030 | 58,150,706,056.59 |
| true | 4,171 | 4,171 | 4,106,321,710.92 |

**Chart: 2024-25 value by supplier location**

| is_overseas | total_value |
|---|---|
| False | 58150706056.59 |
| True | 4106321710.92 |

<details><summary>SQL: overseas share pct calc</summary>

```sql
SELECT SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share, COUNT(DISTINCT CASE WHEN is_overseas THEN notice_id END) AS overseas_contracts, COUNT(DISTINCT notice_id) AS total_contracts FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

<details><summary>SQL: overseas share pct calc</summary>

```sql
SELECT SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share, COUNT(DISTINCT CASE WHEN is_overseas THEN notice_id END) AS overseas_contracts, COUNT(DISTINCT notice_id) AS total_contracts FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

<details><summary>SQL: overseas share pct calc</summary>

```sql
SELECT SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share, COUNT(DISTINCT CASE WHEN is_overseas THEN notice_id END) AS overseas_contracts, COUNT(DISTINCT notice_id) AS total_contracts FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

<details><summary>SQL: overseas share 2024-25</summary>

```sql
SELECT is_overseas, COUNT(*) AS contracts_rows, COUNT(DISTINCT notice_id) AS contracts, SUM(value_share) AS total_value FROM mart.contract_supplier WHERE financial_year='2024-25' GROUP BY is_overseas ORDER BY is_overseas
```
</details>

<details><summary>SQL: overseas share 2024-25</summary>

```sql
SELECT is_overseas, COUNT(*) AS contracts_rows, COUNT(DISTINCT notice_id) AS contracts, SUM(value_share) AS total_value FROM mart.contract_supplier WHERE financial_year='2024-25' GROUP BY is_overseas ORDER BY is_overseas
```
</details>

Score: 

### Q5 · low · run 3

Failure: `none` · 5.9 s · 2 model calls · 0 repairs · bare numbers: 6.6, 2024, 25, 2024, 25, 1, 2024, 30, 2025

**Prose:**

> Overseas suppliers received about 6.6% of 2024-25 value — measured on Financial Year 2024-25 (1 Jul 2024–30 Jun 2025), sum of `value_share` in `mart.contract_supplier` (current contract values, multi-supplier splits, cancelled excluded).

**Metrics:** Overseas share of value: 6.6%; Overseas value: $4,106.3M; Total 2024-25 value: $62,257.0M; Domestic value: $58,150.7M; Basis: FY 2024-25 sum of value_share (current values as published). Domestic = supplier country in Australia.: Overseas share of contract value — 2024-25

**Table: Domestic vs overseas — 2024-25**

| Supplier origin | Contracts | Total value |
|---|---|---|
| Domestic | 54,030 | $58,150.7M |
| Overseas | 4,171 | $4,106.3M |

**Chart: 2024-25 value by supplier origin**

| supplier_origin | total_value |
|---|---|
| Domestic | 58150.706056589996 |
| Overseas | 4106.3217109199995 |

<details><summary>SQL: overseas share FY 2024-25 totals</summary>

```sql
SELECT SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(CASE WHEN NOT is_overseas THEN value_share ELSE 0 END) AS domestic_value, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

<details><summary>SQL: overseas share FY 2024-25 totals</summary>

```sql
SELECT SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(CASE WHEN NOT is_overseas THEN value_share ELSE 0 END) AS domestic_value, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

<details><summary>SQL: overseas share FY 2024-25 totals</summary>

```sql
SELECT SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(CASE WHEN NOT is_overseas THEN value_share ELSE 0 END) AS domestic_value, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

<details><summary>SQL: overseas share FY 2024-25 totals</summary>

```sql
SELECT SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(CASE WHEN NOT is_overseas THEN value_share ELSE 0 END) AS domestic_value, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

<details><summary>SQL: domestic vs overseas breakdown FY24-25</summary>

```sql
SELECT CASE WHEN is_overseas THEN 'Overseas' ELSE 'Domestic' END AS supplier_origin, COUNT(*) AS rows, COUNT(DISTINCT notice_id) AS contracts, SUM(value_share) AS total_value FROM mart.contract_supplier WHERE financial_year='2024-25' GROUP BY 1 ORDER BY 1
```
</details>

<details><summary>SQL: domestic vs overseas breakdown FY24-25</summary>

```sql
SELECT CASE WHEN is_overseas THEN 'Overseas' ELSE 'Domestic' END AS supplier_origin, COUNT(*) AS rows, COUNT(DISTINCT notice_id) AS contracts, SUM(value_share) AS total_value FROM mart.contract_supplier WHERE financial_year='2024-25' GROUP BY 1 ORDER BY 1
```
</details>

Score: 

### Q5 · minimal · run 1

Failure: `none` · 4.1 s · 2 model calls · 0 repairs · bare numbers: 2024, 25, 2024, 25, 1, 2024, 30, 2025

**Prose:**

> Overseas suppliers received a small share of 2024-25 Commonwealth contract value.
> 
> Basis: FY 2024-25 (1 Jul 2024–30 Jun 2025), `mart.contract_supplier` value_share (excludes cancelled, splits multi-supplier).

**Metrics:** Overseas share of value: 0.1%; Overseas value: $4,106.3M; Total contract value: $62,257.0M

**Table: Overseas vs total, 2024-25**

| Overseas value | Total value | Overseas share | Contracts | Overseas contracts |
|---|---|---|---|---|
| $4,106.3M | $62,257.0M | 0.1% | 58,201 | 4,171 |

<details><summary>SQL: overseas share 2024-25</summary>

```sql
SELECT SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share, COUNT(DISTINCT notice_id) AS contracts, COUNT(DISTINCT CASE WHEN is_overseas THEN notice_id END) AS overseas_contracts FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

<details><summary>SQL: overseas share 2024-25</summary>

```sql
SELECT SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share, COUNT(DISTINCT notice_id) AS contracts, COUNT(DISTINCT CASE WHEN is_overseas THEN notice_id END) AS overseas_contracts FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

<details><summary>SQL: overseas share 2024-25</summary>

```sql
SELECT SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share, COUNT(DISTINCT notice_id) AS contracts, COUNT(DISTINCT CASE WHEN is_overseas THEN notice_id END) AS overseas_contracts FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

<details><summary>SQL: overseas share 2024-25</summary>

```sql
SELECT SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share, COUNT(DISTINCT notice_id) AS contracts, COUNT(DISTINCT CASE WHEN is_overseas THEN notice_id END) AS overseas_contracts FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

Score: 

### Q5 · minimal · run 2

Failure: `none` · 4.6 s · 3 model calls · 0 repairs · bare numbers: none

**Prose:**

> 

**Metrics:** Overseas value: $4,106.3M; Total value (with supplier listed): $62,257.0M

<details><summary>SQL: overseas vs domestic share FY24-25</summary>

```sql
SELECT SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(CASE WHEN NOT is_overseas THEN value_share ELSE 0 END) AS domestic_value, SUM(value_share) AS total_value FROM mart.contract_supplier WHERE financial_year = '2024-25'
```
</details>

<details><summary>SQL: overseas vs domestic share FY24-25</summary>

```sql
SELECT SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(CASE WHEN NOT is_overseas THEN value_share ELSE 0 END) AS domestic_value, SUM(value_share) AS total_value FROM mart.contract_supplier WHERE financial_year = '2024-25'
```
</details>

Score: 

### Q5 · minimal · run 3

Failure: `none` · 5.5 s · 3 model calls · 0 repairs · bare numbers: 2024, 25, 6.6

**Prose:**

> In 2024-25 (financial year, July-June), overseas suppliers received about 6.6% of contract value, on a value-share basis excluding cancelled contracts.

**Metrics:** Overseas share of value: 6.6%; Overseas value: $4,106.3M; Total contract value: $62,257.0M

**Table: Value by supplier location 2024-25**

| Is overseas | Contracts rows | Value |
|---|---|---|
| false | 54,030 | $58,150.7M |
| true | 4,171 | $4,106.3M |

**Chart: Value by supplier location 2024-25**

| is_overseas | value |
|---|---|
| False | 58150.706056589996 |
| True | 4106.3217109199995 |

<details><summary>SQL: overseas share pct calc</summary>

```sql
SELECT SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

<details><summary>SQL: overseas share pct calc</summary>

```sql
SELECT SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

<details><summary>SQL: overseas share pct calc</summary>

```sql
SELECT SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END) AS overseas_value, SUM(value_share) AS total_value, SUM(CASE WHEN is_overseas THEN value_share ELSE 0 END)/NULLIF(SUM(value_share),0) AS overseas_share FROM mart.contract_supplier WHERE financial_year='2024-25'
```
</details>

<details><summary>SQL: overseas share 2024-25</summary>

```sql
SELECT is_overseas, COUNT(*) AS contracts_rows, SUM(value_share) AS value FROM mart.contract_supplier WHERE financial_year='2024-25' GROUP BY is_overseas ORDER BY is_overseas
```
</details>

<details><summary>SQL: overseas share 2024-25</summary>

```sql
SELECT is_overseas, COUNT(*) AS contracts_rows, SUM(value_share) AS value FROM mart.contract_supplier WHERE financial_year='2024-25' GROUP BY is_overseas ORDER BY is_overseas
```
</details>

Score: 
