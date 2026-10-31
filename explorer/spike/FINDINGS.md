# Phase 1 spike findings

Date: 2026-10-30. Versions: `a2ui-agent-sdk==0.9.0`, `@a2ui/web_core@0.14.0`, `@a2ui/lit@0.14.0`.

## Risk 1: mixed-catalog surface

**Decision: one merged catalog with its own ID, defined on both sides.**
ID: `urn:explorer:catalog:basic-vegachart:v0_9` = basic catalog v0.9 components and
functions + `VegaChart`. Agent: `Catalog.from_json(basic.to_json() + VegaChart)` with the new
ID, passed to `MacroExpander`. Client: `new Catalog(id, '0.9', [...basicCatalog.components.values(), A2uiVegaChart], [...basicCatalog.functions.values()])`
and `new MessageProcessor([merged])`.

### Evidence

Client, from source (`web_core` 0.14.0 `processing/message-processor.js` `resolveComponentCatalog`,
`universal/render-a2ui-node.js`): in v0.9 every component resolves to the surface's
default catalog (per-component `catalogId` only exists from v1.0). A type missing from that
catalog renders `nothing` plus a `console.warn`; no error, no `onError` event.

Client, in a browser (`client-probe/`, screenshot `s1_render.png`), same transport messages
(`Column[Text, Column[Text, VegaChart]]`), three setups:

| Case | Processor catalogs | `createSurface.catalogId` | Result |
|---|---|---|---|
| A | basic, vegachart | basic | Text renders; **chart silently missing** (`Component implementation not found ... VegaChart`) |
| B | basic, vegachart | vegachart | **Blank surface** (root `Column` not found) |
| C | merged | merged | Text and chart render |

The repo's `samples/vegachart` uses case A. On the published 0.14.0 packages that would drop the
charts. The repo main branch may behave differently; we depend on npm, not main.

Agent (`s1_catalog.py`): hand-written Express → `BarChart` macro → transport. `createSurface`
carries the merged ID; output types `Column`, `Text`, `VegaChart`. Prompt snippet: 20,347 chars.

❗ **Python validation is off by default.** `MessageProcessor(catalogs=[...])` accepted a
`VegaChart` with a bogus property, and a `VegaChart` on a basic-only surface. With
`MessageProcessorOptions(validation_config=STRICT_VALIDATION)` both are rejected
(`Unevaluated properties are not allowed`, `Unrecognized component type 'VegaChart'`) and the
good message passes. The app must validate transport messages with `STRICT_VALIDATION`
before sending.

### Other client notes

- Vendoring VegaChart from repo main needs import changes for 0.14.0: `A2uiLitElement` is
  not exported from `@a2ui/web_core/v1_0` in 0.14.0; import it from `@a2ui/lit/v0_9`. Drop the
  v1 type import. With that, `tsc --noEmit` passes.
- Deps that work: `vega@5.33.1`, `vega-lite@5.23.0`, `vega-embed@6.29.0`, `zod@^3.25`,
  `vite@6.4.5`, `lit@^3.3.3`; Vite `resolve.dedupe: ['lit']`, `optimizeDeps.include: ['vega-embed']`.
- `Text` renders Markdown only if a markdown renderer is provided (`@a2ui/markdown-it`
  via `Context.markdown`); otherwise it warns and shows plain text.

## Risk 3: schema context size

**Recommendation: curated summary in the prompt + a `describe_table` tool that returns
the dictionary section for one table. No embedding-based retrieval.**

Evidence (`../fti/DATA_DICTIONARY.md`):
- 184,349 bytes, 1,118 lines, one `### schema.name` section per table/view (46 sections):
  it splits cleanly per table, so "retrieval" is just a lookup by name.
- Much of the size is Markdown table padding. Collapsing runs of spaces and dashes gives
  94,241 bytes (~23k tokens); the `core` section alone is 47,303 bytes (~12k tokens).
- Biggest sections: metric functions 17.5 KB, `core.receipt` 9.3 KB, `core.donor_disclosure`
  8.9 KB, `core.contract` 7.6 KB, `core.v_contract_fact` 5.6 KB.
- `CONTEXT.md` (glossary) is 6.8 KB (~1.7k tokens): small enough to include whole.

Proposed stable prompt layer (after the A2UI snippet, ~5k tokens target):
1. `CONTEXT.md` in full.
2. The dictionary's Conventions section (money, `_raw`/`_norm`, privacy flags).
3. A generated one-line-per-table index (name + first sentence of its section).
4. The metric function signatures, with a rule to prefer them (see risk 4).

`describe_table(name)`: the compacted dictionary section, plus live columns from
`information_schema` so drift between the doc and the database shows. Both files are
config (`DOMAIN_FILES`), so nothing is AusTender-specific in code; the section parser only
assumes `### schema.name` headings.

## Risk 4: slow queries and SQL safety

Timings on the read-only login, `READ ONLY` transaction, 15 s timeout (`s4_queries.py`):

| Query | Time | Result |
|---|---|---|
| `core.metric_top_suppliers(FY23-24..24-25)` LIMIT 10 | 5.52 s | 10 rows |
| `core.metric_value_by_agency(FY23-24..24-25)` | 2.84 s | 10 rows |
| naive top suppliers on `v_contract_fact`, 2024 | 1.26 s | 10 rows |
| naive supplier × agency on `v_contract_fact`, 2024 | 1.20 s | 50 rows |
| `count(*)` `core.contract` / `raw.ocds_release` | 0.05 / 0.10 s | |
| `core.abn_name ILIKE '%telstra%'` LIMIT 20 (28M rows) | 0.99 s | early hit |
| `core.abn_name ILIKE` no match (full scan) | 5.79 s | 0 rows |
| unbounded `SELECT` from `core.contract`, `fetchmany(5001)` | 0.32 s | truncated at 5,000 |
| `DELETE` / `UPDATE` / `CREATE TABLE` | 0.00 s | 25006 read-only transaction |

Speed is not the main problem: the worst case found is ~6 s, inside 15 s. **Correctness is.**
FY2024-25 top suppliers, naive `sum(current_value)` on `v_contract_fact` vs
`core.metric_top_suppliers`: the naive query puts Secure Journeys first at $4,686.1M; the
metric function gives $2,343.0M (second), because it excludes cancelled contracts. The
metric functions also split a contract's value across its suppliers (no contract has more
than one supplier row today: 0 of 863,758, so this doesn't change current numbers yet).
So the prompt should steer the model to the metric functions first, and to
`v_contract_fact` with `NOT is_cancelled` otherwise.

Also: the domain uses **financial years** (`2024-25` = Jul 2024 to Jun 2025). A question about
"2024" is ambiguous; the agent should say which it used.

### ❗ Multi-statement escape

psycopg sends a query without parameters over the simple protocol, which accepts several
statements in one string. With `SET TRANSACTION READ ONLY` and `SET LOCAL statement_timeout`
already applied:

| Model SQL | Result |
|---|---|
| `SET TRANSACTION READ WRITE; DELETE FROM core.agency WHERE false` | 42501 permission denied (grants stop it, not read-only) |
| `COMMIT; DELETE FROM core.agency WHERE false` | 42501 permission denied |
| `SET TRANSACTION READ WRITE; CREATE TEMP TABLE spike_t (i int)` | **succeeded** |
| `SET LOCAL statement_timeout = 0; SELECT 1` | **succeeded**: the timeout can be switched off |

Fix, verified: `cursor.execute(sql, prepare=True)` uses the extended protocol, and the server
rejects multiple statements (42601 `cannot insert multiple commands into a prepared statement`).
`execute(sql)`, `execute(sql, ())` and `prepare=False` all still accept them. Plan: run model
SQL with `prepare=True`, keep `READ ONLY` + `SET LOCAL statement_timeout` as separate
statements before it, and keep the grants as the last line of defence. The role is
`fti_mcp` ∈ `fti_reader` (verified with `pg_has_role`).

## Risk 2: Muse Spark writing Express

**Decision: Express by default, with `reasoning_effort: "minimal"` (configurable). Keep
Direct JSON behind the config switch. It works, but needs a retry about half the time.**

Setup (`s2_model.py`): `meta/muse-spark-1.3` via `litellm.acompletion`. Five questions
(including the acceptance question) with real stored results (`s2_results.json`), passed
in the user message as result IDs, columns, row counts and 3 sample rows. Macros: `BarChart`,
`LineChart`, `DataTable`, `Metric`, plus passthrough `Column, Row, Card, Text, Divider, Tabs`.
Valid = parses, all macros expand, transport passes `STRICT_VALIDATION`. On failure the
error goes back to the model, max 2 retries. 1 warm-up + 5 questions × 2 reps per format.
Concurrency 3. Raw replies are in `s2_runs.jsonl` / `s2_runs_minimal.jsonl`.

| | Express, default reasoning | Direct JSON, default | Express, `minimal` | Direct JSON, `minimal` |
|---|---|---|---|---|
| Sessions valid in the end | 11/11 | 11/11 | 11/11 | 11/11 |
| Valid on first try | **11/11** | 5/11 | **10/11** | 5/11 |
| Model calls | 11 | 17 | 12 | 17 |
| Latency per call, median (min–max) | 11.6 s (7.0–17.4) | 16.2 s (8.9–22.5) | **2.9 s (1.3–5.2)** | 3.1 s (1.6–8.1) |
| Session time, median | 11.6 s | 26.2 s | **3.7 s** | 5.0 s |
| System prompt | 14,076 chars | 28,372 chars | same | same |
| Prompt tokens (first call), median | 3,648 | 6,857 | 3,648 | 6,857 |
| Completion tokens, median (max) | 2,775 (3,710) | 4,080 (5,129) | 414 (790) | 705 (1,284) |
| Reasoning tokens per call | 1,387–3,451 | 1,923–4,756 | 61–577 | 49–907 |

- **Failures.** Every Direct JSON failure was the same: the model left out `"component": "BarChart"`
  (or `Metric`) on macro nodes, giving `Cannot create component chart1 without a type`. The one
  Express failure (`minimal`) was `Component 'card' is not reachable from 'root'`. One retry
  fixed every failure.
- **Reasoning.** `reasoning_effort` reaches the Meta API through LiteLLM. Accepted values:
  `minimal, low, medium, high, xhigh, max`; `none` gives a 400. `minimal` cut latency ~4× and
  completion tokens ~6× with no loss of validity in this sample.
- **Prompt caching works.** With `prompt_cache_key` set, every call after the first hit
  the cache: 3,441–3,697 of ~3,648 prompt tokens for Express, 6,641–6,897 for Direct JSON.
  That is the whole stable prefix.
- **Data integrity held.** No data value was retyped in a `Text` component or in the prose.
  The only numbers the check flagged were years in titles ("2024-25"). Every chart, table and
  metric went through a macro.
- **Real reply renders** (`client-probe/model.html`, `s2_model_render.png`): Express reply →
  macros → merged catalog → Lit 0.14.0, with two charts, two tables and the SQL captions.

### ❗ Problems found, for the spec

1. **`MacroExpander` swallows macro exceptions.** It logs them and leaves the macro node
   unexpanded, so validation then says `Unrecognized component type 'BarChart'` and the real
   cause (`Unknown result_id 'q9'`) is lost. The app must collect macro errors itself and
   return them to the model. The spike captures the expander's logger. A cleaner option is
   for each macro to record errors in a per-request list.
2. **The prose answers are empty of findings.** Because the rule says "never type numbers",
   the model writes "the chart shows the leading suppliers" instead of "Ventia leads with
   $3.44B". Proposal: a `Value(result_id, row, column)` macro (or a template in Text) so prose
   can quote a stored number safely.
3. **Period confusion.** Asked about "2024" and given FY2024-25 data, one reply titled it
   "Calendar year 2024", which is wrong. In the real tool loop the model writes the SQL, so it
   knows the filter. The result metadata given back to the model should include the SQL, and
   the prompt should require stating the period used.
4. **`DataTable` as `Row`/`Text` is not a table.** The columns don't line up, and `**bold**`
   headers show as literal asterisks without a markdown renderer. Options: a fixed-width `Row`
   with `weight` per cell, a Vega-Lite text table, or a small custom `Table` component in the
   merged catalog (we already ship a custom catalog, so this is cheap). I recommend the custom
   component. Also add `@a2ui/markdown-it` on the client.
5. **Charts.** Stacked bars by agency get a 30-entry legend (`Symbol legend count exceeds limit`)
   and lose the result's sort order. The macros need sensible defaults: sort by `y`, cap colour
   categories (top N + "Other").

Sample size is small (22 sessions per setting, one model). Treat the percentages as rough.

## Summary of decisions for the spec

| Risk | Decision |
|---|---|
| 1 Mixed catalog | Own merged catalog ID (basic + VegaChart [+ Table]) on agent and client; `STRICT_VALIDATION` on the agent |
| 2 Express on Muse | Express default, `reasoning_effort=minimal`, 2 retries with the error fed back; macro errors surfaced explicitly |
| 3 Schema context | CONTEXT.md + conventions + table index + metric function signatures in the prompt; `describe_table` for sections |
| 4 Slow / wrong queries | Prefer metric functions; `prepare=True` to block multi-statement SQL; `READ ONLY` + `SET LOCAL statement_timeout` + 5,000-row cap; grants as backstop |
