# SPEC: end-to-end scenarios, scored automatically

**Status: built** (2026-11-02); scenarios signed off by the user. Scoring method in ADR 0018. Sub-spec of
`explorer/SPEC.md` §10. Reuses the effort-check harness (`explorer/specs/effort-check/SPEC.md`).

The effort check asks 5 questions and is scored by hand. Prompt and loop changes now need a
quick, repeatable measure: did answers stay right, and did they get faster? This adds a set of
scenarios for a dataset, a runner that asks them against the real agent, and a scorer that decides
pass or fail from the SQL behind each Visual.

## Files

Paths relative to `explorer/` unless stated.

| File | What |
|---|---|
| `datasets/austender/scenarios.toml` (repo root; tests use the frozen `explorer/agent/tests/fixtures/austender/`, ADR 0045) | the AusTender scenarios (10, signed off by the user 2026-11-02) |
| `agent/scripts/scenarios.py` | runner and scorer: `run` and `report` subcommands. Imports the A2A client, agent process, log parser and secret scan from `effort_check.py`; no AusTender names in code |
| `agent/scripts/effort_check.py` | `ask()` gains an optional `context_id` for multi-turn scenarios, and returns the reply's `contextId` as `context_id` (amended during build); nothing else changes |
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
  - an integer: a whole number token equal to it, after removing thousands separators, a leading
    currency symbol and an all-zero decimal part (`37`, `1,234`, `$37`, `$1,234.00` count; `137`,
    `37.5` and `r37` don't). Dates are removed first, so the `25` in `FY2024-25` doesn't count;
  - with alternates, it's enough that one matched entry's values are all stated;
  - text: the trimmed, case-folded value appears with word boundaries on both sides.
- `forbid_visual = ["chart"]`: none of these kinds may be on the Surface.
- `expect_empty = true`: the prose is not empty (a fail otherwise), and every number in the
  answer rows is null or zero. A non-zero number makes the run **`needs_review`** (`review:
  not_empty`), not a fail: rows can't tell helpful context ("125 agencies, 0 matching") from a
  silent fuzzy match to a real entity, so a person reads it (amended after the first live run).
- `prose_no_numbers = true`: the model **typed** no number other than zeros. It reads the bare
  numbers the agent logged for the Turn (the agent's rule in `macros.py`, which skips dates,
  financial years such as `FY2024-25`, and years 1900–2099, and never includes values filled from
  Results through `Cite`), then ignores any number equal to zero. Numbers written in words are not
  caught. (Amended after the first live run: it read the filled prose, so a `Cite` value failed it.)
- `smoke = true`: passes if the Turn doesn't fail. For behaviour no rule can judge from text (a
  refused write). Smoke runs are **left out of the pass rate**: they can't fail on the agent's
  answer. The report prints their prose for a person to read.

Prose is matched after undoing markdown escaping (`\.` → `.`), as the user reads it. Filled
`Cite` values carry it; a backslash the model typed itself is undone too (amended during build).

There are no free-text phrase checks: two review rounds showed regexes can't tell a refusal from a
claim, failing good answers and passing bad ones (Review decisions, item 7).

A scenario has a `reference`, `expect_empty`, or `smoke`. One with none of them is rejected.

## Behaviour

`uv run python scripts/scenarios.py run [--file F] [--ids a,b] [--runs 1] [--level minimal] [--include-unreviewed]`

1. Load and validate the file. Reject a scenario with an unknown key, no `turns`, or none of
   `reference`, `expect_empty` and `smoke`.
2. Run every `reference`, `alternates` and `wrong` query once, read-only, through the agent's
   `SqlRunner` (same guard, `READ ONLY`, timeout, row cap, the dataset's read-only role from
   `DATABASE_URL`). A query that fails, returns no rows, hits the row cap, lacks an `expect`
   column or has a null expected value stops the run before any model call. Duplicate `wrong`
   labels in a scenario are rejected at load.
3. Start the agent once (as the effort check does) and send a warm-up question.
4. For each scenario and run: send the turns in one Conversation (`context_id` from the first
   Turn's reply). A failed Turn ends the scenario; that Turn is the one scored (it fails on
   `failure`). If a Turn fails on transport and the agent process has exited, the run stops. Record, for the last Turn, what the effort check records (prose, failure,
   statuses, Repairs, wall time, model calls with tokens and latency, SQL attempts).
   Since `../../SPEC.md` §17 the record also has `visuals` (each VegaChart or DataGrid with its
   `clickable`, `drillable` and clickable rows), `drills_dropped` (Drill queries the checks
   rejected, from the agent log) and `drill_downs`: after scoring, the harness clicks the first
   drillable Visual's first and last clickable rows and records each click's wall time, failure
   and whether the model was called, plus the Drill-down's `sql` as its Level shows it (values
   bound), for checking by hand that the answer's filters are kept. Clicks never change the score.
5. **Score the last Turn:**
   - Rerun each distinct SQL from the Surface's `SqlDetails` through the same `SqlRunner`. These
     rows are the **answer rows**. A rerun that fails is the fail reason `rerun_failed` (the rows
     are incomplete, so an empty-looking answer can't pass); one that hits the row cap sets the
     flag `answer_truncated`.
   - Answer rows and reference rows both come through the agent's `SqlRunner`, which converts
     values with `to_json_safe` (`Decimal` → `float`, dates → ISO text). Integer columns (`count`,
     `bigint`) stay Python `int`.
   - A reference value `b` **matches** an answer cell `a` when:
     - `b` is an integer (Python `int`): `a == b` exactly. Counts need it (24,172 and 24,187 must differ);
     - `b` is another number: `|a - b| <= max(abs, rel × |b|)`, defaults `abs = 0.01`, `rel = 0.0005`;
     - `b` is text: equal after trimming and case-folding.
     - Bools match only bools (a `true` cell never matches a count of 1).
     - With `percent = true` a **non-integer answer cell** is scaled (`a`, `a × 100`, `a / 100` are
       tried) and compared with `b` in the reference's own units; the `abs` floor is 0 unless set.
       So 6.5958 % never matches a fraction like 0.0627. Whole-number cells are never scaled
       (`660`, or `660.0` from a `numeric` column), so a stray count can't match 6.6 %.
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
   - A run that would pass but has a `review` item (`not_empty`), or matches a `wrong` entry, is **`needs_review`**, not `pass`: the
     Surface shows a known wrong reading next to the right one (a hedge), and only a person can
     tell which one it presents as the answer.
   - Every record names its fail reasons (`failure`, `no_visual`, `forbidden_visual`, `missing` with
     the values not found, `not_stated`, `not_empty` for `expect_empty` rows, `prose` for empty
     prose or numbers in it), and every `wrong` entry whose values all match
     (`matched_wrong: [labels]`), whatever the status.
   - **Flags** (recorded, never a fail): `over_max_model_calls`; `answer_truncated`; `write_attempted` when any SQL
     attempt in the Turn is rejected by the agent's own `guard()` as not a single `SELECT` (the guard
     and the read-only role already stop writes, so a write can't succeed and isn't scored).
6. Stop the agent, write `report-<stamp>.md`, and run the secret scan on every file written (it
   fails the run on a hit, as the effort check does). The scan runs even when the agent fails to
   start, a scenario fails part-way, or the report can't be written. `report RUNS.jsonl` writes
   `report-<stem>.md` beside it and never overwrites the input. The report:
   - per scenario: runs by status, fail reasons, matched wrong answers, median wall time, model
     calls and Repairs; the full prose of `smoke`, `needs_review` and `fail` runs, for reading;
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
| 1 | Valid file loads; unknown key, no turns, or none of `reference`, `expect_empty` and `smoke` is rejected | `test_load_valid`, `test_load_rejects[unknown_key, no_turns, nothing_to_check]`, `test_load_rejects_duplicate_ids` |
| 2 | Unreviewed scenarios are skipped unless `--include-unreviewed`; `--ids` filters | `test_selection` |
| 3 | Match: integers exact (24,180 ≠ 24,187); other numbers at the tolerance edges and `abs` floor; text case-folding; `percent` scales non-integer cells only, with no `abs` floor (0.0627 ≠ 6.5958 %; integer 660 ≠ 6.6 %); rounding within the tolerance matches, scaling to another unit doesn't | `test_value_match[...]`, `test_percent_tolerance_comes_from_the_column` |
| 4 | Pass needs failure `none`, any one listed visual, and all reference values; extra rows and Visuals don't fail | `test_score_pass`, `test_score_extra_rows_pass`, `test_score_fail_reasons[failure, no_visual, missing]` |
| 4a | `content_policy` → `blocked`; a passing run that matches a wrong entry → `needs_review` | `test_status_blocked`, `test_status_needs_review` |
| 4b | Tolerances apply to alternates and wrong entries; a failed rerun is `rerun_failed`; truncation is a flag | `test_tolerance_applies_to_alternates_and_wrong_entries`, `test_rerun_failure_and_truncation` |
| 5 | An alternate's values pass; a wrong entry is named when it matches, on failing and passing runs | `test_score_alternate`, `test_score_names_wrong_answer[fail, pass]` |
| 6 | Answer rows come from the Surface's `SqlDetails`, deduplicated, rerun through the runner | `test_answer_rows_from_surface_sql` |
| 7 | `answer_in`: a value only in a reused table fails; stated in a Metric or the prose passes; whole tokens only (`37`, `$37`, `1,234` count; `137`, `37.5` don't) | `test_answer_in[...]`, `test_stated[...]`, `test_answer_in_accepts_any_matched_entry_that_is_stated` |
| 7a | `forbid_visual`; `expect_empty` (a non-zero number in the rows → `needs_review`; null, zero and empty rows pass; empty prose fails); `prose_no_numbers` reads the typed numbers (a typed `2` fails; typed zeros and `Cite`-filled values don't); `smoke` | `test_empty_checks[...]`, `test_live_not_found_answer_is_not_failed`, `test_smoke_passes_unless_the_turn_fails` |
| 7b | `write_attempted` flag from the guard; `over_max_model_calls` flag; neither fails a run | `test_flags` |
| 8 | A reference that fails or is empty stops the run before the agent starts | `test_reference_failure_stops_run[...]`, `test_references_record_rows_and_time`, `test_references_reject_truncated_and_null[...]`, `test_duplicate_wrong_labels_are_rejected` |
| 9 | Multi-turn: turns share a `context_id`; only the last Turn is scored; a failed earlier Turn ends the scenario; the real A2A server returns a `contextId` | `test_multi_turn_context` (stub A2A transport), `test_a_failed_earlier_turn_ends_the_scenario`, `test_effort_check.py::test_ask_against_stub_agent` |
| 10 | The report: statuses per scenario; pass rate leaves out `smoke` and `blocked`; p90; full prose of smoke, needs_review and fail runs; reference run times; the output path never overwrites the input | `test_report`, `test_report_shows_prose_and_p90`, `test_report_path_never_overwrites_the_input` |
| 10a | `run()` end to end with a fake agent: records, answer rows, report, agent stopped; a secret in the output fails the run after the report is written; a failure part-way still writes the report | `test_run_end_to_end`, `test_run_secret_hit_and_part_way_failure` |
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
3. **`top-growth-fy2324` ranks by dollars**, matching the effort check's decision; ranking by
   percent is a labelled wrong answer. (The question first said "in dollars"; Meta's filter
   blocks that, see item 10.)
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

9. **Build review (PASS-WITH-NOTES).** Fixed in one round: `report` could overwrite a runs file
   not named `runs-*`; a failed or truncated answer rerun passed silently; the secret scan was
   skipped if the agent failed to start or the report failed; a bool cell matched a count of 1;
   `answer_in` checked only the first matched entry; the report cut prose at 600 characters;
   duplicate wrong labels; whole-number floats were scaled by `percent`; `25` in `FY2024-25`
   counted as stated; and tests for seven surviving mutants and for `run()` itself. Found during
   the build and fixed separately: the pool's `DISCARD ALL` reset broke psycopg's automatic
   prepared statements on the sixth run of a statement (`sql.py`, `prepare_threshold=None`).
   Re-review: all resolved, no blocker. Known limit, deferred: a count equal to a year (1900–2099)
   can never be "stated", because years are stripped first; no scenario has one.
10. **First live run (7/8 passed).** Two scenario fixes, not agent fixes: `no-such-agency` failed a
    correct not-found answer ("125 agencies, 0 matching") because `prose_no_numbers` read the
    filled prose and `expect_empty` failed on a context count (both amended above, with a
    regression test); `top-growth-fy2324` was `blocked`: Meta's filter rejects "grew the most in
    dollars". A probe with the bare question accepted "had the largest dollar increase in value",
    but the rerun was still blocked. Probed again with the agent's real system prompt: both
    "dollar" wordings blocked, the effort check's original wording accepted (as in all 13 earlier
    runs). So the question is back to the original; decision 3 now rests on the labelled
    percent-ranking wrong answer alone. Lesson: probe the filter with the real system prompt.

**Next batch** (after these 10 are signed off), including the refuter's trap suggestions: a top list
holding the FMS account (dropped only with a stated reason); corporate groups (BAE has three
supplier keys; merging is wrong); a bare year on a list where FY and calendar ranks differ; negative
growth; a partial latest year ("this year"); Defence's keys in FY 2014-15 (26,141 vs 26,121, a
sharper split than calendar 2014); UNSPSC segment; monthly counts in a calendar year; a truncated
long list; an out-of-scope field (state); a follow-up that filters an earlier Result.

## Amendment: Board scenarios (2026-11-04, `../../SPEC.md` §18 slice 4 Part B, SC1)

Status: **approved** with Part B (the user asked for it to be built, 2026-11-04). It times the
Boards against §18's speed targets, with no model call. The Boards are the ones in the Dataset's
`boards/` directory (for austender, drafted by the bootstrap and reviewed: ADR 0036).

### Files

| File | Change |
|---|---|
| `agent/scripts/scenarios.py` | a `boards` subcommand, `run_boards()`, `boards_report()` and their helpers. No dataset names |
| `agent/tests/test_scenarios.py` | the tests below, with a fake agent and fake `act`. No model, network or database |
| `docs/scenarios/<dataset>/boards-<stamp>.jsonl`, `boards-agent-<stamp>.log`, `boards-report-<stamp>.md` | output (committed after a live run, after the secret scan) |

`effort_check.py` doesn't change.

### Behaviour

`uv run python scripts/scenarios.py boards [--runs 5] [--out DIR]`

1. Refuse to start when the Dataset has no `boards/` directory or no Explorer Board.
2. **Each run starts a fresh agent process**, so the Board cache is empty and every run measures
   the same cold path. Wait for the warm-up log line, `board warm-up: board=explorer ms=<n>`. Its
   `ms` is the run's **Explorer cold** time. Give up after 60 s, or when
   `board warm-up failed` is logged. The run is recorded as `warmup_failed` and the next run goes
   on.
3. **Open the Explorer:** send `open_board` with `{board: "explorer"}` in a new Conversation, then
   time it (**Explorer warm**). Read these from the reply:
   - the Conversation ID;
   - the Surface ID (`explorerLevel.surfaceId`);
   - the instance ID (`explorerBoard.id`);
   - the Controls (`explorerBoard.controls`);
   - each `choice` Control's options, from its `ChoicePicker` component's `options`;
   - the current values, from the `/controls` data model;
   - the clickable Visuals, with their `action.boards` rows, from the raw components.
4. **Before each click, wait for the prefetch to settle:** until the agent log has had no new
   `board query:` line for 1 s, at most 10 s. That's the time a person takes to look before
   clicking. It also keeps one click's follow-on prefetch out of the next click's evidence (refuter
   round 1).
5. **Entity Board clicks:** for each Visual with `action.boards`, in the reply's component order,
   click its first `boards` row, then its last when that is a different row. Each click is `drill` with
   `{source, row}` on the Explorer's Surface. Record per click:
   - wall time and failure;
   - the opened Board's name, matched from the tab title before " · " against the Dataset's Board
     titles;
   - `queries_run`: the `board query: board=<that name>` lines in the agent log between the
     request and 0.2 s after the reply;
   - `prefetched`: true when `queries_run` is 0. It comes from that evidence, not from which row
     was expected to be prefetched. It is null for a failed click, because a rejected action runs
     nothing.
6. **Control changes:** for each `choice` Control, set each option in turn (up to 3, in option
   order), then set it back to its starting value. Each change sends `controls` with
   `{board, seq, values}`. `values` holds every Control in wire form: a choice's index, or null
   for "All". `seq` counts up from 1. Record per change:
   - wall time and failure;
   - `superseded`;
   - `queries_run`: the `board query: board=explorer` lines in the change's log slice;
   - `cached`: true when `queries_run` is 0, and null for a failed change.
   
   Controls of other kinds are recorded as `skipped: <kind>`. They aren't timed.
7. Stop the agent. Write one JSONL record per run (`run`, `started_at`, `explorer_cold_ms`,
   `explorer_open`, `clicks`, `changes`, `warmup_failed`), then the report. Run the secret scan on
   every file written, as `run` does, including when a run fails part-way.

**The report** (`boards_report(records)`) has one row per measure: the count, median, p90, target,
and `ok` / `miss` against the median. The p90 is shown, not judged.

| Measure | Samples | Target (median) |
|---|---|---|
| Explorer warm | `explorer_open` | < 0.5 s |
| Explorer cold | `explorer_cold_ms` | < 1.5 s |
| Control change, not cached | changes with `cached` false | < 1.0 s |
| Control change, cached | changes with `cached` true | < 1.0 s |
| Entity Board, prefetched | clicks with `prefetched` true | < 0.3 s |
| Entity Board, not prefetched | clicks with `prefetched` false | < 1.5 s |

A measure with no samples shows "—". Failed actions are counted per measure and left out of the
times. A failed click counts under "not prefetched", and a failed change under "not cached".

§18's "Any Board, cold" is two rows: Explorer cold, and Entity Board not prefetched. With them in
one row, the cheap Entity Board renders hid the Explorer's cold time behind the median (refuter
round 1). `--runs` must be at least 1.

### Criteria → tests

All in `agent/tests/test_scenarios.py`. No model, network or database.

| # | Criterion | Test |
|---|---|---|
| SC1a | One fresh agent per run, each stopped; the warm-up line's ms is `explorer_cold_ms`; a warm-up failure or timeout records `warmup_failed`, and the next run still goes | `test_boards_fresh_agent_per_run_and_warmup` |
| SC1b | `open_board` is sent with `{board: "explorer"}` and no Conversation; the Surface, instance, Controls, options, values and `boards` Visuals are read from the reply | `test_boards_open_reads_the_reply` |
| SC1c | Clicks: the first and last `boards` row of each such Visual, as `drill` `{source, row}` on the Explorer Surface in its Conversation; the opened Board's name from the tab title; `prefetched` from the log's `board query:` lines for that Board only | `test_boards_clicks_and_prefetch_evidence` |
| SC1d | Control changes: each option up to 3, then back to the start; full wire `values` (index or null) and `seq` 1, 2, …; `cached` from the log; a non-`choice` Control is `skipped` | `test_boards_control_changes` |
| SC1e | The report: per measure the count, median, p90, target and ok/miss; failed actions counted and left out of the times; "—" with no samples | `test_boards_report` |
| SC1g | Refuter round 1: a failed click or change isn't classified (null) and counts under not prefetched or not cached; the prefetch settles before each click; a failed `open_board` records only the open; `--runs 0` is refused; Explorer cold is its own measure | `test_boards_failed_actions_are_not_counted_as_prefetched_or_cached`, `test_boards_settle_before_each_click`, `test_boards_failed_open_records_only_the_open`, `test_boards_runs_must_be_positive`, `test_boards_explorer_cold_is_its_own_measure` |
| SC1f | A Dataset with no Boards is refused before an agent starts; a secret in the output fails the run after the files are written; a failure part-way still writes the report and stops the agent | `test_boards_refused_without_boards`, `test_boards_secret_scan_and_part_way_failure` |
| BM | Manual: `boards --runs 5` against the real database, with the medians and p90s recorded in `../../SPEC.md` (B-M) | manual |

### Decisions

- **A fresh process per run**, not a cache flush. The agent has no flush hook, and a fresh
  process is what a user meets after a deploy. Startup is a few seconds per run, and no model is
  called.
- **The prefetch evidence comes from the log** (no `board query:` line for that Board), not from
  which row we expected to be prefetched. The agent has no "prefetched" flag for Entity Boards, and
  this way a prefetch that lost its race is counted honestly.
- **Explorer cold comes from the warm-up line**, since that render runs on the server with no
  transport. A Board's cold time in §18 is the server's render.
- **Only `choice` Controls are changed.** austender's Boards have only `choice`, and the other
  kinds need their own value pickers. They're recorded as skipped, so a gap shows.
- **Not scored and not a test.** It measures speed. Correctness of Board numbers is the startup
  check's job (B1) and the reviewer's.
- **B-M's typed question after a Control change** (it uses the Board's state, and the Board pair
  adds prompt tokens) calls Spark. It is done by hand in the explorer, not in this script.

## Amendment: scored Board scenarios and the model A/B (2026-11-04, `../../SPEC.md` §21)

Status: **approved** (2026-11-04, with `../../SPEC.md` §21), revision 2. The refuter gave
revision 1 a FAIL (2 blockers, 9 majors), and the fixes are worked in here (see "Review round 1"
below).

It is the harness half of slice 5b, which picks the default answer model. Today a scenario can
only type questions into a new Conversation. ADR 0030 says the default is chosen "on the Board
and Controls scenarios", and none exist. This lets a scored scenario start from a Board:
- open the Explorer Board;
- set its Controls;
- click through to an Entity Board;
- then type its questions while viewing that Board, or end with "Ask within filters" on a row
  (`../../SPEC.md` §20).

It also adds `compare`, which applies §21's gate to two runs files.

### Files

| File | Change |
|---|---|
| `agent/scripts/scenarios.py` | Board steps in `parse_scenario` and `run`; `read_board`, a superset of `read_open`. `read_open` stays as a thin wrapper over it, with the same output, so the `boards` subcommand and its tests don't change; new fields on each record; `setup_failed` in `score`, `summary` and `report`; a `compare` subcommand. No dataset names |
| `agent/scripts/effort_check.py` | `ask()` and `act()`, through `action_body`, gain an optional `viewing`, sent as `explorerViewing` in the message metadata. Without it, nothing changes |
| `agent/tests/test_scenarios.py` | the tests below. The `boards` fakes are extended: `act` takes `viewing` and returns Surfaces, tabs and components, and `controls` replies carry `updateComponents`. No model, network or database |
| `datasets/austender/scenarios.toml` | 9 Board scenarios, `reviewed = false` until the user signs them off |
| `docs/scenarios/<dataset>/compare-<A stem>-vs-<B stem>.md` | output of `compare`, written next to file A unless `--out` is given |

### Scenario file

A scenario may start from a Board. All the new keys are optional:

```toml
[[scenario]]
id = "defence-thales-within"
reviewed = false
categories = ["board", "ask-within"]
board = "explorer"                                # open this Board first (a Board without a key)
controls = { "Procurement method" = "open" }      # Control label → option label, set in this order
click = [{ visual = "breakdown", row = "Department of Defence" }]  # each opens an Entity Board
ask_within = { visual = "top", row = "THALES AUSTRALIA" }          # instead of turns: the last step
visual = ["metric", "table"]
[scenario.reference]
sql = """SELECT ..."""
expect = ["total_value", "contract_count"]
```

- **`board`** names a Board file without a key in the Dataset's `boards/`. Today that is only the
  Explorer Board.
- **`controls`** maps a `choice` Control's label to one of its option labels, or `"All"`.
  - Labels are compared after trimming and case-folding.
  - A label that matches no Control or option, or more than one, is a set-up failure.
  - Only `choice` Controls are supported, as in the `boards` subcommand.
- **`click`** is a list of steps. Each step clicks a row that opens an Entity Board, in order,
  starting from the Board on screen.
- **`visual`** in a `click` or `ask_within` step is the Visual's **layout name**: the ID of the
  clickable component up to its first `__`. Only two kinds are supported, a `DataGrid` and a
  `Treemap` VegaChart.
- **`row`** is the row's displayed text, compared after undoing markdown escaping (`unescape`),
  trimming and case-folding:
  - in a `DataGrid`, a row with any cell equal to it. The row index sent is the row's position,
    which is its index in the Result;
  - in a `Treemap`, a leaf whose `_name` is equal to it. The row index sent is the leaf's `_row`.
- **`ask_within`** makes the last step an `ask` on that row. A scenario has `turns` or
  `ask_within`, never both.
- **Dependencies:** `controls`, `click` and `ask_within` need `board`.
- **Sets:** a scenario with `board` is in the **Board set**. Every other scenario is in the
  **typed set**.
- **Kind**, which comes from the scenario's shape:
  - (a) `controls` and no `click`;
  - (b) `click`, then `turns`;
  - (c) `ask_within`.

### Behaviour

1. **Load.** The existing checks apply, plus these. Each of the following is rejected before the
   agent starts:
   - an unknown key in a step;
   - `controls`, `click` or `ask_within` without `board`;
   - both `turns` and `ask_within`, or neither;
   - a `board` that isn't a Board file without a key in the Dataset.
2. **Set-up**, per run, before anything is scored. The harness keeps a **Board model**: the
   current Surface ID, tab ID, the Controls with their labels and option labels, and the
   components by ID (each clickable Visual's `action`, `DataGrid` rows and chart data).
   `read_board` builds it from a reply.
   1. **Open.** Send `open_board` with `{board}` in a new Conversation, and build the Board model
      from the reply.
   2. **Controls.** For each `controls` entry, in order, send `controls` with the full wire
      `values` and the next `seq`. Wait for its reply. A superseded reply means the
      run fails as `setup_failed`, since set-up sends one change at a time and no later reply can
      come. Then **merge the reply's `updateComponents` into the Board model**,
      replacing those components. A re-queried panel comes back with new source IDs and new rows,
      and the old ones are no longer valid on the server (`TOO_OLD`).
   3. **Clicks.** For each `click` step:
      - wait for the prefetch to settle (`settle_prefetch`);
      - find the Visual by layout name and the row in the current Board model;
      - check the row is in the Visual's `action.boards`;
      - send `drill` with `{source, row}` on the current Surface.

      The reply's Surface, tab and components become the current Board model.
   - **`setup_failed`.** Any of these makes the run's status `setup_failed`, with its reason, and
     no model is called:
     - the Board isn't found;
     - a Control or option matches nothing or more than one;
     - a layout name matches no clickable Visual, more than one, or an unsupported kind;
     - the row matches nothing or more than one;
     - the row isn't in `boards`;
     - any reply in set-up fails.

     The record still carries a `score` with `status: setup_failed`, the reason in `reasons`, and
     empty `matched_wrong` and `flags`, so `summary` and `report` read it like any other record.
3. **Turns.** Each typed turn goes in the Board's Conversation, with `viewing` set to the Board's
   tab ID. That is the tab when set-up ended. A Turn's reply can open a new tab, and later turns
   still name the Board's tab, since the scenario is about asking while the Board is in view.
4. **Ask within filters.**
   - Find the row as in a `click` step.
   - Send `ask` with `{source, row}` on the current Surface, through `act` with `viewing` set to
     the current tab.
   - Its reply is the last Turn. Its log slice is read as for a typed Turn (model calls, tokens,
     Repairs and SQL attempts), and it is scored as one, with wall time measured from the `ask`.
5. **Scoring and Drill-down clicks are unchanged.** A typed scenario runs exactly as before. The
   Drill-down clicks after scoring run for both sets, and they never change the score.
6. **Records** gain:
   - `set` (`board` or `typed`);
   - `reviewed`, the scenario's flag when it ran;
   - `model`, the configured `MODEL` the harness passed to the agent;
   - `served_model`, the reply's `explorerModel`, or null when the reply has none;
   - `level`;
   - `commit`, the `git rev-parse HEAD` of the repo and whether `explorer/agent/` or the
     Dataset's directory had uncommitted changes. Other paths don't run in the agent, and the tree
     often carries unrelated work;
   - for the Board set, `setup`: each step's wall time, plus the `setup_failed` reason.

   **The report:**
   - names the model and the level;
   - shows each set's pass rate and median wall time separately, then the overall figures as
     before;
   - adds a `setup_failed` column per scenario.

   `setup_failed` runs are left out of the pass rate, like `blocked`, and counted. A runs file
   from before this amendment, without these fields, is reported as before, all in the typed set.
7. **`compare A.jsonl B.jsonl [--out DIR]`**, where A is the incumbent and B the candidate. It
   calls nothing. The steps are in this order:
   1. **Validity.** These make the result `invalid`, with no verdict, and the report names the
      reason:
      - a record without `reviewed`, `model`, `level` or `commit`;
      - more than one `model`, `level` or `commit` value within a file;
      - A and B have the same `model`;
      - A and B differ in `level` or `commit`;
      - either file was run with uncommitted changes in `explorer/agent/` or the Dataset's
        directory.
   2. **Filter.** Keep only records with `reviewed: true` and leave out `smoke` scenarios. Then
      each of these makes the result `invalid`:
      - the two files have different scenario IDs, or a different number of runs per scenario;
      - any kept run is `setup_failed` or `blocked`;
      - either set is empty after the filter.

      Unreviewed scenarios can't make it `invalid`.
   3. **Figures**, per set and per file:
      - passes and runs, and the pass rate (`pass` over runs; `needs_review` and `fail` are both
        **not a pass**);
      - median and p90 Turn wall time over all kept runs;
      - mean model calls;
      - total Repairs and `write_attempted` flags;
      - mean prompt, cached and output tokens per answer.
   4. **Verdict:**
      - **`B wins`** when all of these hold:
        1. Board-set pass rate of B ≥ A's + 10 points;
        2. typed-set runs that are not a pass: B's ≤ A's + 1;
        3. median Turn wall time of B ≤ 1.15 × A's, in each set.
      - **`A stays`** otherwise, with the failed conditions listed.
      - Repairs and `write_attempted` are reported, never gated.
   5. **Rerun check.** The report says **`close: rerun at 5`** when either of these holds:
      - flipping any single kept run's result (pass ↔ not a pass), recomputed once per run,
        changes the verdict;
      - B's actual median time ratio, in either set, lies between 1.10 and 1.20 × A's (inclusive).

      Otherwise the verdict is clear. When it is close, §21 says to rerun both models at 5 runs,
      and that compare is final, whatever its verdict.

   The report prints both models' names next to the verdict.

### Criteria → tests

All in `agent/tests/test_scenarios.py` unless stated. No model, network or database.

| # | Criterion | Test |
|---|---|---|
| BS1 | Board keys load, and kinds (a), (b) and (c) are read from the shape; each rejection in step 1 happens before the agent starts | `test_board_scenario_load`, `test_board_scenario_rejects[...]` |
| BS2 | Set-up sends `open_board`, then `controls` with full wire `values` and `seq` 1, 2, … matched by label, then `drill` on the matched `boards` row of the current Surface; each `click` follows a prefetch settle; a click's reply becomes the current Board model | `test_board_setup_sequence` |
| BS2a | A Control change that re-sends the clicked panel with new source IDs and rows is merged, so the click uses the new source and the new row index; a superseded reply gives `setup_failed` | `test_board_setup_merges_the_controls_reply` |
| BS3 | Row matching: a `DataGrid` cell or a Treemap leaf's `_name`, unescaped, case-folded and trimmed; a Treemap sends `_row`, a `DataGrid` its position | `test_board_row_matching[...]` |
| BS3a | Each `setup_failed` cause in step 2 (unknown Board, Control or option matching none or several, layout name matching none, several or an unsupported kind, row matching none or several, row not in `boards`, a failed `open_board`, `controls` or `drill` reply) gives `setup_failed` with its reason, no model call and a `score` stub | `test_board_setup_failed[...]` |
| BS4 | Typed turns after set-up go in the Board's Conversation with `viewing` = the Board's tab, and keep naming it after a reply opens another | `test_board_turns_view_the_board`, `test_effort_check.py::test_ask_and_act_send_viewing` |
| BS5 | `ask_within` sends `ask` with `{source, row}` and `viewing` in the message metadata; its reply's log slice is read and scored as the last Turn, with wall time from the `ask` | `test_ask_within_is_scored` |
| BS6 | Records carry `set`, `reviewed`, `model`, `served_model`, `level`, `commit` and `setup`; `setup_failed` is left out of the pass rate and counted; the report shows both sets, the model and level; an old runs file is reported as before | `test_board_records_and_report`, `test_report_reads_old_records` |
| BS7 | `compare` figures per set; `needs_review` counts as not a pass | `test_compare_figures` |
| BS7a | `B wins` only when every condition holds: one test per condition, each failing alone at its edge, including exactly +10 points and exactly 1.15× | `test_compare_gate[...]` |
| BS7b | Each `invalid` cause in step 7.1 and 7.2; unreviewed scenarios can't make it `invalid`, whether by ID, by a blocked run or by a `setup_failed` run | `test_compare_invalid[...]`, `test_compare_filters_before_matching_ids` |
| BS7c | The rerun check: one flipped run that changes the verdict, or a ratio of 1.10–1.20 inclusive, says `close: rerun at 5`; a win at 1.05× with a margin of 2 runs on gates 1 and 2 doesn't | `test_compare_close_call` |
| BS7d | `compare` writes `compare-<A stem>-vs-<B stem>.md` next to A, or in `--out`, with both models next to the verdict, and calls nothing (no agent, no runner) | `test_compare_output` |
| BS8 | A typed scenario's run is unchanged; `ask()` and `act()` without `viewing` are unchanged | the existing tests still pass |
| BS9 | austender's file loads with its 9 Board scenarios, 3 of each kind by shape | `test_austender_scenarios_load` |

### The 9 Board scenarios (austender)

Written during the build with reference SQL. They are `reviewed = false` until the user signs them
off, and only reviewed ones count. Three of each kind:
- **(a) Explorer, Control set.** A question while viewing the Explorer Board with Procurement
  method set to `open` or `selective`, never `limited`: Meta's filter blocks "limited tenders"
  questions (Decisions above), and the Board pair would carry it in every prompt. The reference
  keeps the filter, and a wrong entry is the same query without it.
- **(b) Entity Board.** A question while viewing an Agency or Supplier Board. The reference keeps
  that entity, and a wrong entry is the whole Dataset.
- **(c) Ask within filters.** An onward row on an Entity Board, such as Defence → THALES
  AUSTRALIA (the displayed text on 2026-11-04, 3rd in the Agency Board's top 15). The reference
  keeps both entities, and a wrong entry is the clicked entity alone.

The wrong entries are the point of this set: they name the failure that a Board context invites,
losing the filter. A run showing both the right and the wrong reading is `needs_review`, which is
not a pass.

Before sign-off, each scenario is run once with Spark (with the user's go-ahead) to check that its
set-up works and Meta's filter doesn't block it.

### Decisions

- **Rows are named by displayed text, not key value.** Key columns are hidden from tables and
  labels (`7067937`), so the harness can't see them. A label that matches nothing or several rows
  stops the run instead of picking one. If live data renames an entity, the scenario fails loudly.
- **Only `DataGrid` and `Treemap` rows.** Those are what austender's Boards click through. A
  Treemap matches on `_name` alone, so `"root"` or a formatted value can't match. Other charts can
  be added when a Board needs them.
- **Entity Boards are reached by a click**, as a person reaches them. `open_board` opens only
  Boards without a key.
- **`setup_failed` and `blocked` make `compare` invalid.** Neither says anything about the model,
  and leaving them out on one side only would change the denominators. A blocked scenario is
  reworded and probed before the A/B.
- **`needs_review` is not a pass, on both sides.** A hedge that shows the wrong reading next to
  the right one is not a clean answer, so the verdict needs no person to compute.
- **Repairs and write attempts are reported, never gated.** This is the harness's standing rule
  (flags never fail a run). `write_attempted` also counts guard rejections that aren't writes,
  and the typed set provokes one on purpose (`write-request`).
- **The verdict is computed, and close calls are named mechanically.** The gate and the rerun
  check are fixed before any number is seen, so they can't be fitted to the result.
- **No cost in the verdict.** Prices aren't in the code. Tokens are reported, and §21 records the
  cost by hand.

### Review round 1 (2026-11-04)

The refuter gave a FAIL. Fixed in revision 2:
- **Blocker:** the `controls` reply is merged, because re-queried panels get new source IDs and
  rows.
- **Blocker:** `reviewed` is recorded. The filter comes before the ID check, and old files are
  `invalid`.
- `act()` sends `viewing`. Without it, no Board pair is sent for "Ask within filters".
- `read_board` is a superset of `read_open`, with layout names, tab, labels and row data.
  `read_open` stays as a wrapper.
- `needs_review`, "fails", the denominators and empty sets are defined.
- Gate 4 is report-only.
- A mechanical rerun trigger.
- Run identity: `model`, `level`, `commit`, and different models are required.
- Every `setup_failed` cause is tested.
- Re-review (PASS-WITH-NOTES, no new blocker), fixed in turn:
  - the time rerun check uses B's actual ratio (1.10–1.20) instead of fixed times, which flipped
    every win;
  - `setup_failed` and `blocked` are checked after the reviewed filter;
  - `read_open` is kept as a wrapper;
  - a superseded set-up reply fails the run instead of waiting.
- Kinds come from the shape, and the record shape and output name are defined.
- The label-matching rules are stated, and chart matching is narrowed to the Treemap `_name`.
- The example uses `open`, not `limited`.

### Build notes (2026-11-04)

Where the build had to decide, these notes win over the text above.
- **Merging a `controls` reply** replaces every component of each layout name the reply
  re-sends (the ID up to the first `__`), not just the components with the same IDs. A
  re-queried panel can come back with other components, for example "no rows" instead of a
  table. A stale clickable component left behind would then match a second time.
- **Kind:** a Board scenario with neither `controls` nor `click` nor `ask_within` loads, with
  kind null. It belongs to the Board set, but to none of the three kinds.
- **`ask_within`'s row must be in the Visual's `boards`**, as in a `click` step. A row that can't
  be found is a `setup_failed` reason. It is found before the `ask` is sent, so no model is
  called.
- **Refuter round (PASS-WITH-NOTES), fixed:**
  - a Board reply without a tab ID is `setup_failed`, since no Board pair would be sent;
  - a set-up failure from a dead agent stops the run, as a transport failure in a Turn does;
  - tests were added for `repo_commit` (untracked files count, other paths don't), for
    `_main_run` (`model`, `commit` paths, Board names without a key, and an unknown Board
    stopping before `run`), and for `ask_within` on a row that isn't in `boards`.

  Deferred: `model` comes from the repo root's `.env` even when the agent reads
  `EXPLORER_ENV_FILE`. This predates the amendment. `served_model` is recorded next to it.
- **Record fields:** `commit` is `{sha, dirty}`. Records also carry `kind`. A Board record's
  `setup` is `{steps: [{step, wall_s, failure}], reason}`.
- **`compare` identity:** a record lacks `model`, `level` or `commit` when the key is missing or
  empty. A missing `reviewed` key also counts, but `reviewed: false` doesn't. The gate's
  comparisons allow a float slack of 1e-9 at the edges, so exactly 1.15× passes gate 3.
- **`load(path, boards)`:** `run` passes the Dataset's Board names that have no key, so an unknown
  `board` stops the run before the reference queries and the agent.
- **austender's 9 scenarios:**
  - (a): `board-open-top-agencies-fy2324`, `board-selective-top-suppliers-fy2425` and
    `board-open-home-affairs-total`;
  - (b): `board-agency-home-affairs-fy2223`, `board-supplier-auspost-top-agencies-fy2324` and
    `board-agency-services-australia-by-year`;
  - (c): `board-ask-within-defence-thales`, `board-ask-within-home-affairs-serco` and
    `board-ask-within-bae-parliamentary-services`.

  All their reference and wrong queries ran on 2026-11-04, and every wrong entry differed from
  its reference.
- **Spark probe (2026-11-04, one run each, `docs/scenarios/austender/runs-20261104T092340Z.jsonl`):**
  - every set-up worked, with no `setup_failed`;
  - Meta's filter blocked nothing;
  - Spark answered both (a) list questions without the Procurement method filter. The wrong
    entry matched, so they failed. With that bare wording, this was a valid reading (see the
    scenario review below). `board-open-home-affairs-total` showed both readings (`needs_review`);
  - all 3 (b) passed;
  - in (c), the Thales and BAE answers were right but showed the pair's total by financial year
    in their tables, so each (c) scenario gained an alternate for that breakdown. Rescored from
    the saved answers, with no model call: Thales passes, and BAE is `needs_review` (it also
    showed the agency's total across all suppliers). Serco failed: the reply was "Prose answer
    with UI." with no Visual or SQL.
- **Scenario review (refuter FAIL on the (a) scenarios; fixes applied 2026-11-04, then signed off
  by the user):**
  - the (a) questions said neither that the Control applies nor which measure, so the unfiltered
    reading was defensible. They now end "in this view" and name contract value. They still don't
    name the method, which the model has to read from the Board's state;
  - "paid them" became "awarded them … contract value". Supplier side values are award values,
    not payments;
  - two alternates identical to their references were removed;
  - (c) scores the pair's lifetime value, as a total or by financial year. The by-year alternate
    was added after the probe. Other shapes, such as a split by method, fail;
  - no prompt rule that a question on a Board honours its Controls, by design. The user decided
    (2026-11-04) that a typed question doesn't have to keep a Board's filters; only clicks
    (Drill-downs, "Ask within filters") do. The (a) questions ask for the filter ("in this
    view"), and the model has to find its value in the Board pair.

## Test command

```bash
cd explorer/agent && uv run pytest tests/test_scenarios.py tests/test_effort_check.py -q
uv run pytest -q
```
