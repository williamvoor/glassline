# Reasoning effort check

The check from `explorer/SPEC.md` §10. It asks whether `REASONING_EFFORT=minimal` should stay the
default or change to `low`. The harness is in `explorer/specs/effort-check/SPEC.md`.

**Status: awaiting the user's scores.** The decision follows from them (rule below). The harness
never scores, and the notes under "First read" are not scores.

## Runs

The model was `meta/muse-spark-1.3` with the full tool loop. Only `REASONING_EFFORT` changed between
levels. Each level had 1 warm-up plus 3 runs of each of the 5 questions, every run in a fresh
Conversation.

| Run file | What | Scored? |
|---|---|---|
| `effort-check/runs-20261101T235100Z.jsonl` | first run, before the fix below | no |
| `effort-check/runs-20261101T235731Z.jsonl` | re-run of all questions at both levels after the fix | **yes**, see `effort-check/scoring-sheet.md` |

The agent logs for each level are next to the run files. The harness found no secrets in any
output file.

### Speed and caching (scored run, warm-ups left out)

| Level | Median Turn | Max Turn | Model calls | Cache rate | Failures |
|---|---:|---:|---:|---:|---:|
| minimal | 3.5 s | 7.3 s | 29 | 95.8 % | 3 (all Q2) |
| low | 5.5 s | 12.6 s | 31 | 95.1 % | 3 (all Q2) |

`low` takes about 60 % longer per Turn. Speed is the top requirement, so `low` should become the
default only if it also gets more answers right.

## Questions that failed at both levels

### Q2: the provider's content filter blocks the question (known gap)

Every Q2 run at both levels failed in about 1 s with `ContentPolicyViolationError` (HTTP 400,
"filtered due to the prompt triggering…"). Direct calls to Spark with only the user message and
no system prompt show that the filter reacts to the question text itself:

| Question text | Result |
|---|---|
| Which 5 suppliers received the most value from limited tenders in 2022-23? | blocked |
| Which 5 suppliers received the most value from limited tender contracts in 2022-23? | blocked |
| Which 5 suppliers received the most value in 2022-23 where the procurement method was limited? | blocked |
| Which 5 suppliers received the most contract value in 2022-23? | ok |
| Which suppliers won the most from limited tenders? | ok |

No prompt or data dictionary change can fix this, so it is recorded as a **known gap**. Two
consequences:

- Q2 can't be scored as written. Rewording it is the user's decision, since the user approved
  the questions.
- In the product, a blocked question gets "The model service failed
  (ContentPolicyViolationError). Please try again." Trying again won't help, so that message
  misleads the user.

### Q5: a fraction shown with the `pct` format (fixed)

In the first run, 5 of 6 Q5 answers showed "Overseas share: 0.1%". The SQL returned the share as
a fraction (0.066), and the model displayed it with `pct`, which expects a value already in
percent. The macro docs listed both `pct` and `fraction_pct` but never said how they differ.

**Generic fix:** every format parameter in the macro docs (BarChart, LineChart, DataTable, Metric,
Cite) now says "pct = value already in percent (6.6 -> 6.6%), fraction_pct = value is a fraction
(0.066 -> 6.6%)". A regression test checks that the prompt carries this text. All questions were
then re-run at both levels.

## First read of the scored run (not scores)

These notes come from comparing each run with `effort-check/reference-values.md`.

- **Q1:** every run gives one of the two accepted totals: 24,172 contracts (main key) or 24,187
  (both keys). Only some runs say which agency key they used, and Q1 counts as correct only if
  the answer says so.
- **Q3:** every run gives the reference top 10 in the reference order.
- **Q4:** all runs give the 15 reference totals. In 5 of 6 runs the partial years are clearly
  flagged. **low run 2** needs a check.
- **Q5:**
  - low: 6.6 % in 3 of 3 runs.
  - minimal: 6.6 % in 1 of 3. Run 1 still shows "0.1%", and run 2 gives the two values but no
    share.

If the scores agree, Q5 meets the decision rule (minimal wrong in ≥ 2 of 3, low right in ≥ 2 of
3), and the default would move to `low`.

## Decision rule (from the spec)

Switch the default to `low` only if, on some question, `minimal` is wrong in at least 2 of 3 runs
and `low` is right in at least 2 of 3. Every 1-of-3 miss is still listed here.

## Other findings from the runs

- **The model shows notes through `Metric`.** Some answers select string literals in SQL (for
  example "2012-13 partial…") and show them with `Metric`. Numbers inside those literals are
  typed by the model. They reach the screen as if they came from the database, and the
  bare-number check never sees them. This works around ADR 0001.
- **A placeholder in a `Metric` label is not filled.** One first-run answer showed the raw text
  `{r1[0].total_value|currency_m}` in a label. Labels and titles don't go through placeholder
  filling.
- **The model typed a wrong date range.** In the acceptance test (criterion 2), the prose
  described FY 2024-25 as "July 2024–June 2026" instead of June 2025.
