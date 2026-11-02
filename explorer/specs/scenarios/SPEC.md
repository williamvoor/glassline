# SPEC: end-to-end scenarios, scored automatically

**Status: draft, reviewed by a refuter on the user's behalf and amended** (2026-11-02). Scoring method in ADR 0018 (proposed). Sub-spec of
`explorer/SPEC.md` §10. Reuses the effort-check harness (`explorer/specs/effort-check/SPEC.md`).

The effort check asks 5 questions and is scored by hand. Prompt and loop changes now need a
quick, repeatable measure: did answers stay right, and did they get faster? This adds a set of
scenarios for a dataset, a runner that asks them against the real agent, and a scorer that decides
pass or fail from the SQL behind each Visual.

## Files

Paths relative to `explorer/` unless stated.

| File | What |
|---|---|
| `datasets/austender/scenarios.toml` | the AusTender scenarios (10, signed off by the user 2026-11-02) |
| `agent/scripts/scenarios.py` | runner and scorer: `run` and `report` subcommands. Imports the A2A client, agent process, log parser and secret scan from `effort_check.py`; no AusTender names in code |
| `agent/scripts/effort_check.py` | `ask()` gains an optional `context_id` for multi-turn scenarios; nothing else changes |
| `agent/tests/test_scenarios.py` | tests: no model, no network; scoring tests use a stub SQL runner |
| `docs/scenarios/<dataset>/runs-<stamp>.jsonl`, `agent-<stamp>.log`, `report-<stamp>.md` (repo root) | output of `run` (committed after a live run, after the secret scan) |

The agent reads only `dataset.toml`, `notes.md` and `glossary.md` from the dataset directory
(`config.py`), so `scenarios.toml` never reaches its prompt.

## Scenario file

```toml
[[scenario]]
id = "defence-cy2014"                       # stable, kebab-case
reviewed = false                            # only reviewed scenarios run by default
categories = ["calendar-year", "name-match", "cancelled"]  # descriptive, for the report; not scored
turns = ["How much contract value did ..."] # one Conversation; scored on the last Turn
visual = ["metric", "table"]                # ANY one of these kinds on the last Turn's Surface ([] = none needed)
max_model_calls = 4                         # optional: recorded as a flag, not a fail

[scenario.reference]                        # the correct answer
sql = """SELECT ..."""
expect = ["total_value", "contracts"]       # columns whose value in every reference row must appear

[[scenario.alternates]]                     # optional: other correct readings, same shape
sql = """..."""
expect = ["total_value", "contracts"]

[[scenario.wrong]]                          # optional: labelled known mistakes, same shape
label = "financial year 2013-14 instead of calendar 2014"
sql = """..."""
expect = ["total_value"]

[scenario.tolerance]                        # optional per-column overrides, used by every entry
overseas_pct = { rel = 0.002, percent = true }
```

Optional checks on the last Turn (any scenario):
- `answer_in = ["metric", "prose"]`: the expected values must also be **stated**, in a Metric value
  or the prose, not just present in the answer rows. For integer and text values only. Used where a
  reused Result holds many candidate values (a follow-up). "Stated" means:
  - an integer: a whole number token equal to it, after removing thousands separators and a
    leading currency symbol (`37`, `1,234`, `$37` count; `137`, `37.5` and `r37` don't);
  - text: the trimmed, case-folded value appears with word boundaries on both sides.
- `forbid_visual = ["chart"]`: none of these kinds may be on the Surface.
- `expect_empty = true`: every number in the answer rows is null or zero, and the prose is not
  empty (a question about something that doesn't exist; a fuzzy match to a real entity fails).
- `prose_no_numbers = true`: the prose has no number other than zeros, dates and years. It uses the
  agent's bare-number rule in `macros.py` (which skips dates, financial years such as `FY2024-25`,
  and years 1900–2099) on the filled prose, then ignores any number equal to zero (`0`, `0.00`,
  `$0`). Numbers written in words are not caught.
- `smoke = true`: passes if the Turn doesn't fail. For behaviour no rule can judge from text (a
  refused write). Smoke runs are **left out of the pass rate**: they can't fail on the agent's
  answer. The report prints their prose for a person to read.

There are no free-text phrase checks: two review rounds showed regexes can't tell a refusal from a
claim, failing good answers and passing bad ones (Review decisions, item 7).

A scenario has a `reference`, `expect_empty`, or `smoke`. One with none of them is rejected.

## Behaviour

`uv run python scripts/scenarios.py run [--file F] [--ids a,b] [--runs 1] [--level minimal] [--include-unreviewed]`

1. Load and validate the file. Reject a scenario with an unknown key, no `turns`, or none of
   `reference`, `expect_empty` and `smoke`.
2. Run every `reference`, `alternates` and `wrong` query once, read-only, through the agent's
   `SqlRunner` (same guard, `READ ONLY`, timeout, row cap, the dataset's read-only role from
   `DATABASE_URL`). A reference that fails or returns no rows stops the run before any model call.
3. Start the agent once (as the effort check does) and send a warm-up question.
4. For each scenario and run: send the turns in one Conversation (`context_id` from the first
   Turn's reply). Record, for the last Turn, what the effort check records (prose, failure,
   statuses, Repairs, wall time, model calls with tokens and latency, SQL attempts).
5. **Score the last Turn:**
   - Rerun each distinct SQL from the Surface's `SqlDetails` through the same `SqlRunner`. These
     rows are the **answer rows**.
   - Answer rows and reference rows both come through the agent's `SqlRunner`, which converts
     values with `to_json_safe` (`Decimal` → `float`, dates → ISO text). Integer columns (`count`,
     `bigint`) stay Python `int`.
   - A reference value `b` **matches** an answer cell `a` when:
     - `b` is an integer (Python `int`): `a == b` exactly. Counts need it (24,172 and 24,187 must differ);
     - `b` is another number: `|a - b| <= max(abs, rel × |b|)`, defaults `abs = 0.01`, `rel = 0.0005`;
     - `b` is text: equal after trimming and case-folding.
     - With `percent = true` a **non-integer answer cell** is scaled (`a`, `a × 100`, `a / 100` are
       tried) and compared with `b` in the reference's own units; the `abs` floor is 0 unless set.
       So 6.5958 % never matches a fraction like 0.0627. Integer cells are never scaled, so a stray
       count of 660 can't match 6.6 %.
     - Rounding in SQL passes only while it stays within the tolerance: whole dollars and `$M`-level
       rounding of a large total pass; `6.6` passes a percent with `rel = 0.002`. Scaling to another
       unit (`sum(x) / 1e9`) fails. SQL should return raw values; formats round.
   - **Extra rows and columns are allowed.** A match needs each expected value somewhere in the
     answer rows; more rows (a top 25 for a top 10) and more Visuals don't fail a run. An
     alternate that is a subset of the reference (fewer years) is matched the same way.
   - **Status** of a run: `blocked` when the failure is `content_policy` (the provider declined the
     question: it measures the filter, not the agent); otherwise `pass`, `needs_review` or `fail`.
   - **Pass** when all of these hold:
     1. the failure is `none`;
     2. any one `visual` kind is present (`metric` = a Metric Card, `table` = DataGrid,
        `chart` = VegaChart), and no `forbid_visual` kind is;
     3. if there is a `reference`: every expected value of the reference, or of one `alternate`,
        matches some cell of the answer rows, and, with `answer_in`, is stated there too;
     4. `expect_empty` and `prose_no_numbers` hold, if set. A `smoke` scenario needs only 1.
   - A run that would pass but also matches a `wrong` entry is **`needs_review`**, not `pass`: the
     Surface shows a known wrong reading next to the right one (a hedge), and only a person can
     tell which one it presents as the answer.
   - Every record names its fail reasons (`failure`, `no_visual`, `forbidden_visual`, `missing` with
     the values not found, `not_stated`, `prose`), and every `wrong` entry whose values all match
     (`matched_wrong: [labels]`), whatever the status.
   - **Flags** (recorded, never a fail): `over_max_model_calls`; `write_attempted` when any SQL
     attempt in the Turn is rejected by the agent's own `guard()` as not a single `SELECT` (the guard
     and the read-only role already stop writes, so a write can't succeed and isn't scored).
6. Stop the agent, run the secret scan on every file written (it fails the run on a hit, as the
   effort check does), and write `report-<stamp>.md`:
   - per scenario: runs by status, fail reasons, matched wrong answers, median wall time, model
     calls and Repairs; the prose of `smoke` and `needs_review` runs, for reading;
   - overall: the pass rate (`pass` over runs, leaving out `smoke` and `blocked` runs), the
     `needs_review` and `blocked` counts, median and p90 wall time, and mean model calls;
   - failures by type.
   - The time each reference query ran is recorded, because live data can move between it and the
     answer's rerun (the latest, partial year grows with each ingest).

`uv run python scripts/scenarios.py report RUNS.jsonl` rewrites the report from a runs file.

## Criteria → tests

All in `agent/tests/test_scenarios.py` unless stated. No model, network or database.

| # | Criterion | Test |
|---|---|---|
| 1 | Valid file loads; unknown key, no turns, or none of `reference`, `expect_empty` and `smoke` is rejected | `test_load_valid`, `test_load_rejects[unknown_key, no_turns, nothing_to_check]` |
| 2 | Unreviewed scenarios are skipped unless `--include-unreviewed`; `--ids` filters | `test_selection` |
| 3 | Match: integers exact (24,180 ≠ 24,187); other numbers at the tolerance edges and `abs` floor; text case-folding; `percent` scales non-integer cells only, with no `abs` floor (0.0627 ≠ 6.5958 %; integer 660 ≠ 6.6 %); rounding within the tolerance matches, scaling to another unit doesn't | `test_value_match[...]` |
| 4 | Pass needs failure `none`, any one listed visual, and all reference values; extra rows and Visuals don't fail | `test_score_pass`, `test_score_extra_rows_pass`, `test_score_fail_reasons[failure, no_visual, missing]` |
| 4a | `content_policy` → `blocked`; a passing run that matches a wrong entry → `needs_review` | `test_status_blocked`, `test_status_needs_review` |
| 5 | An alternate's values pass; a wrong entry is named when it matches, on failing and passing runs | `test_score_alternate`, `test_score_names_wrong_answer[fail, pass]` |
| 6 | Answer rows come from the Surface's `SqlDetails`, deduplicated, rerun through the runner | `test_answer_rows_from_surface_sql` |
| 7 | `answer_in`: a value only in a reused table fails; stated in a Metric or the prose passes; whole tokens only (`37`, `$37`, `1,234` count; `137`, `37.5` don't) | `test_answer_in[...]` |
| 7a | `forbid_visual`; `expect_empty` (a table of a real agency's figures fails; null, zero and empty rows pass; empty prose fails); `prose_no_numbers` (`0`, `0.00`, `$0`, dates, years and `FY2024-25` allowed; `2` fails); `smoke` | `test_empty_and_smoke_checks[...]` |
| 7b | `write_attempted` flag from the guard; `over_max_model_calls` flag; neither fails a run | `test_flags` |
| 8 | A reference that fails or is empty stops the run before the agent starts | `test_reference_failure_stops_run` |
| 9 | Multi-turn: turns share a `context_id`; only the last Turn is scored | `test_multi_turn_context` (stub A2A transport) |
| 10 | The report: statuses per scenario; pass rate leaves out `smoke` and `blocked`; reference run times recorded | `test_report` |
| 11 | `ask()` without `context_id` is unchanged | `test_effort_check.py` (existing tests still pass) |
| 12 | The draft file loads and every scenario has a reviewer-visible reference (`reviewed` present) | `test_austender_scenarios_load` |

A live check after the build: all reviewed scenarios, 1 run, at `minimal`, with the user's go-ahead
(billed). The spec records the pass rate and the time per Turn.

## Decisions

- **A separate script, not more `effort_check.py` subcommands.** It reuses the effort check's
  pieces by import. The effort check stays as it was specified.
- **Scored on the last Turn of each scenario.** Earlier turns set up context.
- **Values, not layout.** A pass means the right numbers are behind the Visuals (ADR 0018). Order
  in a ranked list is not checked yet.
- **The prose isn't scored**, except `prose_no_numbers` (no invented numbers) where a scenario asks.
  No phrase is ever required or forbidden (Review decisions, item 7).
- **`max_model_calls` is a flag, not a fail**, so speed is visible without making flaky tests.
- **One run by default.** Each scenario costs 2–4 billed model calls per run; `--runs 3` when
  comparing changes.
- **Q2 (limited tenders) is left out for now:** Meta's content filter blocks it every time, so it
  measures the filter, not the agent.
- **Never train on scenarios** (ADR 0018). If the Brief training rows are built later, they must
  not reuse these questions.

## Review decisions (2026-11-02)

A refuter reviewed the draft on the user's behalf (PASS-WITH-NOTES: every reference recomputed and
passing the SQL guard; the scoring rule too loose in places). The user asked for the open questions
to be decided on the recommendations:

1. **Prose-only answers fail** when a scenario has a `reference`: the explorer is meant to show its
   working, so a missing Visual is a real defect.
2. **Scaled SQL fails** (`/1e9`); rounding within the tolerance passes (corrected in item 8).
3. **`top-growth-fy2324` asks "in dollars"**, matching the effort check's decision; ranking by
   percent is a labelled wrong answer.
4. **The follow-up asks about "the first one"** (the third was the FMS payment account, which an agent
   may rightly drop) and needs the count stated (`answer_in`): matching any cell let a wrong Metric
   pass, because a reused top-5 table holds every supplier's count.
5. **`no-such-agency` and `write-request` use light prose checks.** `expect_empty` and
   `expect_no_write` are gone: the first passed invented prose; the second could never fail,
   because the guard and the read-only role block writes. **Re-review (FAIL, three regex
   blockers):** required phrases rejected good answers ("There is no Department of Magic among the
   agencies…", "No contracts were deleted because I only have read access.") and a bare "can't"
   passed a hedged claim. So neither scenario requires a phrase any more. `no-such-agency` relies on
   no Metric, no chart and no numbers in the prose; `write-request` fails only on a claimed
   deletion (`prose_none`, widened). The open-tender alternate now expects one row per method, as a
   natural `GROUP BY` answer gives (it expected an "other" total no answer contains).
6. Mechanical fixes: integers match exactly; `percent` scales the answer cell with no `abs` floor;
   tolerances apply per column to every entry; partial-year alternate for `value-by-fy`;
   two-value alternates for the shares; "cancelled included" wrong answers for the trend and both
   shares; wrong answers named on passing runs too.

7. **Second re-review (FAIL) → the approach changed, not the patterns.** Sentence-scoped regexes
   for claimed deletions still failed 3 of 18 good answers ("I haven't deleted anything…", "…the
   contracts that would be deleted") and passed 11 of 20 bad ones ("I deleted them, no problem.").
   With two FAILs on the same check, the check itself was dropped: `write-request` became a
   `smoke` scenario, read by a person; `no-such-agency` went back to checking the answer rows
   (`expect_empty`), which catches a silent fuzzy match to a real agency, plus `prose_no_numbers`.
   A model judge could score refusals later (only `meta/muse-spark-1.3` may be called); not now.
   The bare-number rule now treats `FY2024-25` as a date (it counted `2024, 25`), with a
   regression test in `test_macros.py`.

8. **Fresh review (PASS-WITH-NOTES, about 73 invented answers).** References all correct; fixed:
   - missing alternates: overseas value plus total (two Metrics); open versus not-open and open
     plus total (the `selective` slice is only $58M, so agents fold it into "other");
   - `no-such-agency`: a $0 Metric is a defensible answer, so only charts are forbidden; zeros
     are allowed in the prose (the bare-number rule counted `0`);
   - `percent` scaling skips integer cells (a stray count of 660 matched 6.6 %);
   - `visual` means any one kind; "stated" is defined; extra rows are allowed on purpose;
   - the spec claimed SQL rounding fails; only scaling does (decision 2 corrected);
   - hedges: a passing run that also matches a wrong entry is `needs_review`;
   - `smoke` and `blocked` (content filter) runs are left out of the pass rate; the write request
     may trip Meta's filter like Q2 did (unverified);
   - reference run times are recorded (the partial latest year moves with each ingest).
   Accepted, not fixed: a Defence answer mixing the both-keys total with the one-key count passes
   (15 contracts apart; only the count tells the readings apart); a "top 10" listing 25 passes; a
   prose-only reply to `no-such-agency` that invents a fact without digits passes (prose isn't
   scored); `categories` like `partial-period` are labels, not checks.

**Next batch** (after these 10 are signed off), including the refuter's trap suggestions: a top list
holding the FMS account (dropped only with a stated reason); corporate groups (BAE has three
supplier keys; merging is wrong); a bare year on a list where FY and calendar ranks differ; negative
growth; a partial latest year ("this year"); Defence's keys in FY 2014-15 (26,141 vs 26,121, a
sharper split than calendar 2014); UNSPSC segment; monthly counts in a calendar year; a truncated
long list; an out-of-scope field (state); a follow-up that filters an earlier Result.

## Test command

```bash
cd explorer/agent && uv run pytest tests/test_scenarios.py tests/test_effort_check.py -q
uv run pytest -q
```
