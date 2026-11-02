# SPEC: names in the main query, periods as named, compact currency

Quick wins from the effort-check runs (`docs/effort-check/`), before Muse Glimmer is tuned.
Sub-spec of `explorer/SPEC.md`. Name matching is decided in ADR 0016. ADR 0001 still holds.

Evidence:
- **Names.** Q1 runs spent a model call (about 2 s) on a lookup query for Defence's `agency_key`
  before the real query. They kept one key or both, and mostly didn't say which.
- **Periods.** Acceptance criterion 2 (`docs/acceptance/step-4.md`) stated FY 2024-25 as
  "July 2024–June 2026". The model typed and converted the period itself.
- **Currency.** Answers show "$38,414.9M" for $38.4B, because only `currency` and `currency_m`
  exist.

## Files

Paths relative to `explorer/agent/` unless stated.

| File | Change |
|---|---|
| `src/explorer_agent/prompts/instructions.md` | name-matching rule (SQL section); period rule (Data rules); example placeholder uses `currency_compact` |
| `src/explorer_agent/macros.py` | `currency_compact` in `FORMATS` and `format_value`; one unit per column in `DataTable`; axis unit from the data in `_chart`/`_axis`; format lists in the `BarChart`, `LineChart`, `DataTable` and `Metric` docstrings |
| `tests/test_macros.py`, `tests/test_prompt.py` | tests below |
| `tests/snapshots/system_prompt_*.sha256` | regenerated with `UPDATE_SNAPSHOTS=1` (the prompt changes on purpose) |
| `explorer/SPEC.md` (parent) | §5 format list, `Cite` and prompt examples, review decision on currency formats (amended after review) |
| `docs/acceptance/step-4.md` (repo root) | row 7: replace the wrong "SQL string literals" diagnosis with the real one (ADR 0013) |

## Behaviour

1. **`currency_compact` for one value** (`Cite`, `Metric`, `format_value`). It picks the unit from
   the value's size: B at 1e9 or more, M at 1e6, K at 1e3, else none. It shows one decimal place
   with a unit, and none without one. The unit is chosen after rounding, so 999,960,000 is
   `$1.0B`, not `$1,000.0M`. Above 1,000B it stays in B with commas (`$1,234.5B`). Negatives are
   `-$1.5B`. The currency symbol comes from the context, as for `currency`.
2. **`currency_compact` in a `DataTable` column.** Every cell in the column uses one unit, picked
   from the largest absolute value among the rows shown. So `$3.4B`, `$1.2B`, `$0.5B`, never a mix
   of `$1.2B` and `$980.0M`. Empty cells stay `—`.
3. **`currency_compact` in `BarChart`/`LineChart`.** The unit is picked from the largest absolute
   value plotted, after colour groups are folded into "Other". For a bar chart with `color` (Vega
   stacks it), that is the largest stack total, with positive and negative parts summed
   separately (amended after review). Line charts don't stack. The values are scaled to it. The
   axis title gets ` ($B)` (or M, K, or ` ($)` with no unit). The axis format is `,.1f` with a unit
   and `,.0f` without one. With no numeric values, there is no unit.
4. **Prompt: names.** A rule in the SQL section tells the model to filter names inside the query that
   answers the question (`ILIKE` in `WHERE` or a join), not with a separate lookup query first.
   When a pattern can match more than one name, the same query returns the matched names (for
   example `string_agg(DISTINCT name, '; ')`), and the answer cites them.
5. **Prompt: periods.** The model names the period the way the user did, or by a label from the
   data, and does not convert it into a date range itself.
6. **Prompt: example placeholder** is `{r1[0].value|currency_compact}`, so the model learns the
   new format from the example.

## Criteria → tests

| # | Criterion | Test |
|---|---|---|
| 1 | `currency_compact` single values: B, M, K and plain; negative; unit chosen after rounding (999,960,000 → `$1.0B`, 999.96 → `$1.0K`); above 1,000B; `None` → `—`; a non-number raises `MacroError` | `test_macros.py::test_format_value` (new cases), `::test_currency_compact_needs_a_number` |
| 2 | `currency_compact` uses the context's currency symbol | `test_macros.py::test_currency_symbol_comes_from_the_caller_not_the_code` (extended) |
| 3 | `Cite` and `Metric` format each value on its own | `test_macros.py::test_cite_and_metric_currency_compact` |
| 4 | `DataTable`: one unit per column from the largest shown value; `None` stays `—`; other columns unaffected | `test_macros.py::test_data_table_currency_compact_uses_one_unit_per_column` |
| 5 | `DataTable`: the unit comes from shown rows only (row cap) | `test_macros.py::test_data_table_currency_compact_unit_from_shown_rows` |
| 6 | Chart: values scaled to the unit, axis title ` ($B)`, format `,.1f` | `test_macros.py::test_chart_currency_compact_picks_unit_from_data` |
| 7 | Chart: the unit is picked after folding into "Other" (the folded sum decides) | `test_macros.py::test_chart_currency_compact_unit_after_folding` |
| 8 | Chart: values under 1,000 → no unit, ` ($)`, `,.0f`, scale 1 | `test_macros.py::test_chart_currency_compact_small_values` |
| 9 | Each macro that takes a format lists `currency_compact` in the prompt | `test_prompt.py::test_prompt_lists_currency_compact` (per macro, amended after review) |
| 10 | The prompt holds the name rule, the period rule and the `currency_compact` example | `test_prompt.py::test_prompt_has_name_and_period_rules` |
| 11 | The prompt is still byte-identical across requests; snapshots updated on purpose | `test_prompt.py::test_prompt_snapshot` (db, rerun after `UPDATE_SNAPSHOTS=1`) |
| 12 | Stacked bars take the unit from the stack total; lines from single values | `test_macros.py::test_stacked_bar_currency_compact_unit_from_stack_totals` |
| 13 | Horizontal and negative charts: unit, symbol, axis and tooltip format | `test_macros.py::test_horizontal_negative_chart_currency_compact` |
| 14 | NaN and Infinity don't crash and take no unit (`$nan`, as `currency` shows them) | `test_macros.py::test_currency_compact_non_finite_values_do_not_crash` |
| 15 | A shared unit never prints a negative zero (`-$0.0B` → `$0.0B`) | `test_macros.py::test_currency_compact_shared_unit_has_no_negative_zero` |

The prompt rules (4, 5) change model behaviour, which tests can't call. They are checked by a
**live check after the build**, run only with the user's go-ahead because it bills model calls.
Q1 and Q5 of the effort check, 3 runs each at `minimal`, plus the criterion 2 question once.
Expected: Q1 runs make one query, with no lookup, and cite the matched agency names. No answer
spells out a converted date range. Results are recorded in this spec.

### Live check result (2026-11-02)

Run at `minimal` with `meta/muse-spark-1.3`. The raw runs and agent logs are in
`docs/live-checks/names-periods-currency/`. Q1 and Q5 ran 3 times each, and the criterion 2
question ran once. All runs succeeded with no Repairs.

| Check | Result |
|---|---|
| Q1 skips the key lookup | **Yes, 3/3.** Model calls 2, 3, 2 (before: 2–4). Run 2's extra query was a cross-check, not a lookup. |
| Q1 answers agree | **Yes, 3/3:** $38.4B across 24,187 contracts (both Defence keys, the reference's both-keys answer). Before, runs were split between 24,172 and 24,187. |
| Matched names are cited | **No, 0/3.** Two of the three queries returned `string_agg` of the names, but no answer cited them. Run 1's prose had no number at all ("substantial contract value"); the values were in its Metrics. |
| No converted date ranges | **Partly.** No range was wrong. But 3 of 4 answers still spelled one out ("1 Jan – 31 Dec 2014"; "financial year 2024-25, 1 Jul 2024 – 30 Jun 2025"), so the rule isn't followed literally. |
| `currency_compact` used | **Yes.** Metrics and prose show `$38.4B`, `$62.3B`, `$4.1B`, `$3.4B`. |
| Speed | **Q1 not faster:** wall 6.7, 9.9, 8.3 s against 4.1–7.3 s before at `minimal` (mean 8.3 s vs 5.6 s), despite fewer calls. Per-call latency rose: Q1's first call used 179–353 reasoning tokens (before 50–220) and took 3.0–4.5 s. Q5 unchanged (4.2–5.0 s vs 4.1–5.9 s). Three runs can't separate provider latency noise from longer reasoning about the new rules. |

**Rerun of Q1 (10 runs, same day).** The 3-run speed result was noise.

- **Speed:** of 9 successful runs, the mean was 5.5 s and the median 5.1 s, against a mean of
  5.6 s before. Every run made exactly 2 model calls (before: 3–4 with the lookup). The first
  call reasons longer (155–421 reasoning tokens), which uses up most of the saved call. **Net: no
  speed change, but consistent answers.**
- **Answers:** all 9 gave $38.4B across 24,187 contracts.
- **Matched names:** cited in the Surface in only 1 of 9 runs.
- **Run 3 failed:** its reply failed the UI checks, and the Repair request got `BadRequestError`
  (HTTP 400) from the provider. The log doesn't record which UI check failed. It's a separate
  bug, not caused by this change, and it was never seen before because no earlier live run needed
  a Repair.

## Decisions

- **`currency_m` stays.** The stub reply, tests and earlier conversations use it. It's still a
  valid format, only no longer the prompt's example.
- **One unit per table column and per chart; one per value in `Cite` and `Metric`.** Mixed units
  in a column make comparisons easy to misread.
- **Units stop at B.** No T: the data's largest yearly totals are in the tens of billions.
- **Precision is one decimal place.** The exact value is still in the table and the SQL. In a
  column or chart with a shared unit, values under 0.05 of it show as `$0.0B`. That's accepted
  (review note).
- **Tooltips show the scaled number without the unit**, as `currency_m` does today; the axis
  title carries the unit.
- **`auto` is unchanged.** It can't tell a currency column from any other number.
- **The rules go in `instructions.md`, not the dataset notes.** They hold for any database (ADR 0016).
- **The name rule sits in the SQL section** (amended during build). In Workflow it split the
  "write SQL… Then answer" sequence.
- **Non-goals:** answering in the same call as the SQL (a separate spike); streaming;
  end-to-end scenarios (a separate spec); Tabs or other components.

## Test command

```bash
cd explorer/agent && uv run pytest tests/test_macros.py tests/test_prompt.py -q
UPDATE_SNAPSHOTS=1 uv run pytest tests/test_prompt.py -q   # once, after the prompt change (needs Docker)
uv run pytest -q   # full suite; db tests need Docker, live_db tests stay deselected
```
