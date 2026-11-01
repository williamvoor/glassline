# SPEC: reasoning effort check harness

Sub-spec of `explorer/SPEC.md` §10 ("Reasoning effort check") and build order step 4. The parent
section sets the questions, runs, scoring and decision rule; this spec covers the harness that
produces the runs and the scoring sheet. Design choice in ADR 0012.

## Files

| File | What |
|---|---|
| `agent/scripts/effort_check.py` | the harness: `run` and `sheet` subcommands |
| `agent/tests/test_effort_check.py` | tests (no model, no network, no database) |
| `agent/pyproject.toml` | `pythonpath = ["scripts"]` for pytest so tests can import the script |
| `docs/effort-check/runs-<UTC stamp>.jsonl` | output of `run` (committed after a live run) |
| `docs/effort-check/agent-<level>-<UTC stamp>.log` | the agent's log for that level (committed) |
| `docs/effort-check/scoring-sheet.md` | output of `sheet` (committed) |

Reused, not changed: `executor.py` failure prose (`UI_ERROR_PREFIX`, `GENERIC_ERROR`),
`llm.py` failure prose and `REPAIR_STATUS`, the log lines at `llm.py:316` (model call),
`llm.py:205` (timeout), `llm.py:210` (model failed), `tools.py:143/146` (run_sql and its failure),
`ui.py:157` (bare numbers), `executor.py:152` (answering failed).

## Behaviour

`uv run python scripts/effort_check.py run [--levels minimal,low] [--runs 3] [--questions 1,2,3,4,5] [--out DIR]`

For each level, in order:

1. Start the real agent as a child process (`python -m explorer_agent`) with `REASONING_EFFORT`
   set to the level, `AGENT_PORT` set to a free port and `LOG_LEVEL=INFO`, everything else
   inherited (the agent loads the repo root `.env` without overriding the environment). Its stdout
   and stderr go to `agent-<level>-<stamp>.log`. Wait for the agent card (60 s limit); if the
   process exits or the wait ends, stop with its last log lines.
2. Send the warm-up question (`"How many contracts are in the dataset?"`), recorded with
   `warmup: true`.
3. Send rounds: round `r` asks questions 1..5 once each, for `r = 1..runs`. Every question goes in
   a fresh Conversation (no `contextId`).
4. Stop the agent (SIGTERM, then kill after 10 s).

Each question is one A2A `message/stream` call over plain httpx JSON-RPC (the same shape as
`tests/test_server.py`), with the A2UI extension header and client capabilities for
`EXPLORER_CATALOG_ID`. Read timeout 180 s (above the agent's 60 s Turn budget).

Each run writes one JSONL record as soon as it finishes:

- `level`, `question` (id; 0 for the warm-up), `question_text`, `run`, `warmup`, `started_at` (UTC ISO), `wall_s`
  (send → final event), `statuses` (status texts in order), `repairs` (count of `REPAIR_STATUS`)
- `prose` (final text), `failure` (see below)
- `surface`: `metrics` (`[label, value]`), `tables` (`title`, `columns`, `rows`, `caption`),
  `charts` (`title`, `values`), `sql` (each `SqlDetails` `summary` and `sql`), and `a2ui` (the raw
  A2UI messages)
- from the agent's log lines written during the run: `model_calls` (`prompt`, `cached`,
  `completion`, `reasoning`, `latency_ms` each), `sql_attempts` (`purpose`, `sql`, `ok`, and
  `sqlstate`/`message` on failure), `sql_failures` (count), `bare_numbers` (list), `log_errors`
  (every WARNING/ERROR line except the bare-number warning, so timeouts, model failures and slow
  turns show here, plus unprefixed lines other than the agent's startup line)

Parallel tool calls interleave their log lines and the outcome lines don't name their query, so an
attempt's `ok` is set only when it was the one pending attempt. Overlapping attempts get
`parallel: true` and `ok: null`. `sql_failures` counts every failure either way.

`failure` is one of `none`, `timeout`, `step_limit`, `no_view`, `ui_error`, `model_failed`,
`error` (the agent's generic error), `transport` (HTTP error, connection error, no final event, or
a final event that is not `completed` with a message; `prose` then holds the error text). UI,
model and generic errors replace the whole prose and are matched at its start. Timeout, step limit
and failed Repairs keep any partial prose and put the failure sentence after it in its own
paragraph (`LlmLoop._give_up`), so they are matched at the start of any paragraph. The run is not
retried and the harness goes on.

On every exit, including a failed level, `run` checks every file it wrote for the values of `META_API_KEY` and the
`DATABASE_URL` password, both raw and percent-decoded. These are read in memory from the
environment and from the agent's env file (`EXPLORER_ENV_FILE`, else the repo root `.env`) and are
never printed. A hit stops with a non-zero exit naming the file, not the value.

`uv run python scripts/effort_check.py sheet RUNS.jsonl [--out docs/effort-check/scoring-sheet.md]`

Writes a Markdown sheet: a summary table per level (scored runs only, warm-ups left out: median
and max `wall_s`, total model calls, prompt and cached tokens, cache rate, failure count), then per
question the reference pointer (`reference-values.md#q<n>`) and, per run (level, run), the
failure, prose, metrics, tables (first 20 rows), chart values (first 20), the SQL, and an empty
`Score:` line for the user. The harness never scores.

## Criteria → tests

| # | Criterion | Test (`tests/test_effort_check.py`) |
|---|---|---|
| H1 | One question against an agent returns prose, metrics, tables, chart values and SQL from the Surface, `failure: none` | `test_ask_against_stub_agent` (stub app via `httpx.ASGITransport`) |
| H2 | The request carries the extension header, the client capabilities and no `contextId` | `test_ask_sends_extension_and_fresh_conversation` (recording `MockTransport`) |
| H3 | Each failure prose maps to its failure type, also after partial prose as `_give_up` builds it; normal prose is `none` | `test_classify_failure`, `test_classify_failure_from_give_up` |
| H4 | HTTP 500, a connection error, a stream with no final event, a final without a message and a `failed` final each give `failure: transport` and no exception | `test_transport_failures`, `test_odd_final_events` |
| H5 | Repairs are counted from status texts | `test_repairs_counted` |
| H6 | Log slice parsing: model call lines, a multi-line `run_sql` SQL followed by `sql ok` and by `run_sql failed`, bare numbers, timeout, other warnings; overlapping attempts marked `parallel` with unknown `ok` | `test_parse_log_slice`, `test_parse_log_slice_parallel_calls` |
| H7 | A record holds only the lines written during its run (offset slicing) | `test_log_offsets` |
| H8 | The agent env sets `REASONING_EFFORT`, `AGENT_PORT`, `LOG_LEVEL` and keeps the rest | `test_agent_env` |
| H9 | Question order: warm-up first, then rounds of 1..5; `--questions` filters | `test_schedule` |
| H10 | Secret scan finds a planted key or password (also percent-encoded, also from `EXPLORER_ENV_FILE`) and names the file, not the value; clean files pass; it runs when a level fails | `test_secret_scan`, `test_secret_values_encoded_and_override`, `test_secret_scan_runs_on_early_exit` |
| H11 | The sheet leaves warm-ups out of the summary, lists every scored run under its question with a `Score:` line, caps rows at 20 | `test_sheet` |
| H12 | Summary medians, token totals and cache rate are right for a fixture; calls with an unknown cached count are left out of the rate | `test_summary`, `test_summary_unknown_cached` |

The live run is by hand, not a test (tests never call a model).

## Decisions

- Plain httpx JSON-RPC, not the a2a-sdk client: the request shape is already proven by
  `tests/test_server.py`, and the SDK's streaming wrapper adds behaviour we would have to verify.
- Tokens, SQL attempts, bare numbers and failure causes come from the agent's log (ADR 0012).
  Runs are sequential, so a byte-offset slice of the log belongs to one run.
- The warm-up question is not one of the five and is never scored.
- Order is by rounds, not by question, so a slow period of the model service spreads across
  questions.
- The JSONL and logs are committed; full SQL in them is fine (no secrets; the secret scan guards).
- Known limits, accepted: a `run_sql` purpose containing `"): "` splits wrongly (the log format
  can't tell purpose from SQL); unprefixed foreign lines (such as uvicorn's) are glued to the
  previous record; a run abandoned by the 180 s client timeout could leak log lines into the next
  run, which is unlikely given the agent's 60 s Turn budget.
- Non-goals: automatic scoring, parallel runs, retrying failed runs, levels other than the ones
  given, any change to the agent.

## Test command

`cd explorer/agent && uv run pytest tests/test_effort_check.py` (and the full `uv run pytest`).
