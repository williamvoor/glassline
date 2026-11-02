# Follow-ups from the effort check runs (2026-11-02)

The live runs of the reasoning effort check exposed three problems in the explorer agent. None of
them depends on the evaluation, which is paused until Muse Glimmer is fine-tuned. Context is in
`docs/reasoning-effort-check.md`. The raw runs are under `docs/effort-check/`.

Repo rules apply (`CLAUDE.md`, `explorer/SPEC.md`). Before each fix, write a regression test that
fails for the reason described. Tests must never call a real model. Paths below are relative to
`explorer/agent/`. Line numbers are as of commit `de7e1e7`.

## 1. Model-typed text and numbers reach the screen through `Metric`

**Problem.** ADR 0001 says that every number on screen comes from the database. Some answers
get around this. The model writes SQL that selects string literals, then shows them with
`Metric`. Numbers inside those literals are typed by the model. They are displayed as if they
came from the data, and the bare-number check in `ui.py:157` never sees them, because it scans
only the prose.

**Evidence.** These are from `docs/effort-check/runs-20261101T235731Z.jsonl`, in the
`surface.metrics` of each record (pairs are `[label, value]`):

- Q4 minimal run 1: `['Non-cancelled contracts, current (amended) value, AUD as published. Financial years July–June.', 'Total AusTender contract value by financial year']`
- Q3 low run 2: `['FY 2023-24 by publish_date. … 9 of 10 are Department of Defence.', '…']`

In that last one, "9 of 10" is a count the model worked out and typed. The SQL behind each
example is in the same record's `sql_attempts`.

**Where.** `Metric` is at `src/explorer_agent/macros.py:384-407`. The SQL guard is `sql.py`. The
model's rules are `src/explorer_agent/prompts/instructions.md`.

**Open question for the user.** Where should the fix go?
- (a) Reject a result column that is a constant string literal in the SQL guard. This needs
  pglast analysis of the select list.
- (b) Make `Metric` reject a text value that contains digits.
- (c) Add an instruction that results are for data, not notes, and that notes belong in the prose.

Option (c) alone is the weakest. It's a decision for the user, so ask before building.

## 2. Placeholders in `Metric` labels (and titles) are not filled

**Problem.** `Cite` placeholders such as `{r1[0].total_value|currency_m}` are filled only in the
prose and in `Cite`. When the model puts one in a `Metric` label, the raw template text appears
on screen. Chart and table titles probably behave the same way: `BarChart`, `LineChart` and
`DataTable` pass `title` straight to `Text`.

**Evidence.** In `docs/effort-check/runs-20261101T235100Z.jsonl`, Q5 low run 1 has a metric
whose label contains the literal text
`Total matches mart.agency_fy total of {r1[0].total_value|currency_m} across {r1[0].contracts|int} contracts.`

**Where.**
- `Metric` label: `macros.py:402`, `Text(text=label, …)`.
- Chart titles: `macros.py:281`.
- Table titles: `macros.py:377`.
- Filling: `_fill` at `macros.py:426` and the `_PLACEHOLDER` pattern at `macros.py:416`.

**Direction.** There are two options:
- Fill placeholders in labels and titles with `_fill`. The values would come from stored
  Results, which is consistent with ADR 0001.
- Reject placeholders there as a macro error, so a Repair asks the model to fix the view.

Filling is friendlier. If a placeholder fails to fill, it must go through the existing
error-capture path, not render as raw text.

## 3. A blocked question tells the user to "try again"

**Problem.** Meta's content filter blocks some legitimate questions with
`ContentPolicyViolationError` (HTTP 400). It reacts to the question text itself. For example,
"Which 5 suppliers received the most value from limited tenders in 2022-23?" is blocked. The
agent then replies "The model service failed (ContentPolicyViolationError). Please try again." Trying again
gets the same result, so the message misleads the user.

**Evidence.** Every Q2 run in both run files fails in about 1 s. The agent logs
`docs/effort-check/agent-*-*.log` contain `model call failed: ContentPolicyViolationError status=400`.

**Where.** The constant `MODEL_FAILED_PROSE` is at `src/explorer_agent/llm.py:31`. The failure
path is `llm.py:207-212`. Check whether the model-call retry at `llm.py:304` also retries this
error. It shouldn't, because a blocked question is never transient.

**Direction.** Give content-policy errors their own message, telling the user the provider
declined the question and suggesting they rephrase it. Don't retry them. Check how LiteLLM 1.106.6
exposes the type, for example `litellm.ContentPolicyViolationError`, rather than matching on
the class name.

Also update `classify_failure` in the effort check harness (`scripts/effort_check.py`). It recognises failures by their prose and imports the prose constants. A new
constant needs a new failure type, such as `content_policy`, plus a test.
