# Follow-ups from the effort check runs (2026-11-02)

The live runs of the reasoning effort check exposed three problems in the explorer agent. None of
them depends on the evaluation, which is paused until Muse Glimmer is fine-tuned. Context is in
`docs/reasoning-effort-check.md`. The raw runs are under `docs/effort-check/`.

Repo rules apply (`CLAUDE.md`, `explorer/SPEC.md`). Before each fix, write a regression test that
fails for the reason described. Tests must never call a real model. Paths below are relative to
`explorer/agent/`. All three are fixed in `7862868` (ADR 0013, `explorer/specs/surface-text/SPEC.md`).
Code is named by symbol, not line number, because line numbers go stale.

## 1. Model-typed numbers reach the screen through plain `Text` on the Surface

**Problem.** ADR 0001 says that every number on screen comes from the database. Some answers
put a heading and a caption note straight on the Surface as plain `Text`, with numbers the
model typed itself. The bare-number check in `UiPipeline.build` (`ui.py`) scanned only the
prose, so it never saw them.

**Correction.** This section first said the model selected string literals in SQL and showed
them with `Metric`. It doesn't: none of the SQL in the runs selects a literal. The harness's
`surface_summary` (`scripts/effort_check.py`) counted any `Column` holding a caption
and an h2 `Text` as a metric, so a model-written note next to a heading looked like one.

**Evidence.** In `docs/effort-check/runs-20261101T235731Z.jsonl`, Q3 low run 2 has a caption
`Text` reading "FY 2023-24 by publish_date. … 9 of 10 are Department of Defence." "9 of 10" is a
count the model worked out and typed.

**Decision.** Surface Text follows the prose rules: placeholders are filled, and typed numbers
are logged, not rejected (ADR 0013). The harness finds metrics by `Metric`'s `Card`.

## 2. Placeholders in Surface Text (and Macro titles and labels) are not filled

**Problem.** `Cite` placeholders such as `{r1[0].total_value|currency_m}` were filled only in
the prose and in `Cite`. When the model put one in a plain `Text`, the raw template text
appeared on screen. Macro titles and labels (`BarChart`, `LineChart`, `DataTable` `title`,
`Metric` `label`) had the same gap, though no run hit it.

**Evidence.** In `docs/effort-check/runs-20261101T235100Z.jsonl`, Q5 low run 1 has a caption
`Text` (component `note`, first reported as a metric label) with the literal text
`Total matches mart.agency_fy total of {r1[0].total_value|currency_m} across {r1[0].contracts|int} contracts.`

**Decision.** Fill placeholders in model-written Text and in Macro titles and labels. A failed
fill is a UI error, so a Repair follows (ADR 0013).

## 3. A blocked question tells the user to "try again"

**Problem.** Meta's content filter blocks some legitimate questions with
`ContentPolicyViolationError` (HTTP 400). It reacts to the question text itself. For example,
"Which 5 suppliers received the most value from limited tenders in 2022-23?" is blocked. The
agent then replies "The model service failed (ContentPolicyViolationError). Please try again." Trying again
gets the same result, so the message misleads the user.

**Evidence.** Every Q2 run in both run files fails in about 1 s. The agent logs
`docs/effort-check/agent-*-*.log` contain `model call failed: ContentPolicyViolationError status=400`.

**Where.** The constant `MODEL_FAILED_PROSE` is in `src/explorer_agent/llm.py`. The failure
path is the `_ModelFailed` handler there. Check whether the model-call retry (`_retryable`)
also retries this error. It shouldn't, because a blocked question is never transient.

**Direction.** Give content-policy errors their own message, telling the user the provider
declined the question and suggesting they rephrase it. Don't retry them. Check how LiteLLM 1.106.6
exposes the type, for example `litellm.ContentPolicyViolationError`, rather than matching on
the class name.

Also update `classify_failure` in the effort check harness (`scripts/effort_check.py`). It recognises failures by their prose and imports the prose constants. A new
constant needs a new failure type, such as `content_policy`, plus a test.
