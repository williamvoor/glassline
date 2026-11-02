# Step 4: acceptance criteria (explorer/SPEC.md §11)

Checked on 2026-11-02 against the loaded AusTender database (864,611 contracts, 2013-01 to
2026-10), using `meta/muse-spark-1.3` with the default `REASONING_EFFORT` (`minimal`).

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | `uv run pytest`, `npm run build`, `npm test` pass | pass | `uv run pytest`: 304 passed. `npm run build`: built. `npm test`: 9 passed in 7 files, including the interop test. |
| 2 | Real model renders the top-10-suppliers question with a chart, a table, the SQL and the period | **pass, with a note** | `criterion-2-top-suppliers-2024.png`: 1 bar chart, 2 tables (top 10, and the agency split with 50 rows), 3 SQL panels, period stated as FY 2024-25. **Note:** the prose gives that period as "July 2024–June 2026"; it should be June 2025. The model typed this date range itself. |
| 3 | A SQL typo is corrected through the tool loop, live and in the stub test | pass | Live: the agent got `42703 column "supplier_nme" does not exist`, reran with `supplier_name` (5 rows), and told the user (`criterion-3-typo-corrected.png`). Stub: the existing tool-loop test. |
| 4 | Writes are rejected and reported as tool errors | pass (tests) | Guard and SQL execution tests |
| 5 | Switching `MODEL`/`FORMAT` needs no code change | pass (tests) | Model switch tests |
| 6 | Byte-identical prompt (test), and `cached_tokens` > 0 from the second call of a live session | pass | Test P1/P4. Live: in `effort-check/agent-low-20261101T235731Z.log` the first call has `cached=0` and later calls have `cached=7537`. The cache rate over the effort check is 95 to 96 %. |
| 7 | No model-typed number in charts, tables, metrics or `Cite` | pass by construction, **with a gap** | Macro and prose-filling tests. Gap: the model can type numbers into plain `Text` on the Surface. Since `7862868`, `Cite` placeholders there are filled and typed numbers are logged, not rejected (ADR 0013). An earlier diagnosis blamed string literals in SQL; that was wrong (see `../followups-2026-11-02.md`). |
| 8 | Reasoning effort check done and recorded | **paused** until Muse Glimmer is fine-tuned (2026-11-02); runs recorded, unscored | `../reasoning-effort-check.md` |
