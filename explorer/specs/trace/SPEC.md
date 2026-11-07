# Trace: a debugging record of model calls and SQL (2026-11-07)

**Status:** accepted 2026-11-07. Decision: ADR 0053. Glossary: **Trace**, **Trace event** (`CONTEXT.md`).

## Behaviour

A **Trace** switch in the Canvas header (one per browser, remembered in `localStorage`) turns on
a Trace for the requests sent while it's on: Conversation requests to the agent and Draft and
Boards runs in Settings. With it off, nothing is recorded and nothing extra is sent.

Trace events (shared shape, `explorer_agent/trace.py`):

| kind | fields |
|---|---|
| `prompt` | `hash`, `text`: the system prompt, sent once per Conversation (or run) |
| `model` | `model`, `prompt_hash`, `messages` (added since the previous model event; `first_message`, `messages_total`), `reply` (`role`, `content`, `tool_calls` [{id, type, function: {name, arguments}}]), `tokens`, `latency_ms`, `error`, `attempt` |
| `sql` | `source` (`run_sql`, `drill`, `drill_check`, `board`, `rerun`, `query`), `sql`, `params`, `ok`, `row_count`, `truncated`, `elapsed_ms`, `sqlstate`, `error` |

Every event has `seq` and `at` (UTC). No event holds data rows. A `model` event's `messages`
carry the run_sql row previews the model saw while live; the saved form replaces them with
`rows_omitted` (and cuts tool errors, as the saved history does).

### Agent (explorer)

- Request metadata `explorerTrace: true` turns tracing on for that request.
- Each event is sent live as a `working` status update with empty text and metadata
  `explorerTraceEvent: <event>`.
- The final message of a request that adds a Level carries `explorerTrace: {"level": "<turn>-<level>"}`;
  the events are saved in that Level's record (`trace`), and the system prompt once per
  Conversation in the saved state (`trace_prompts`, by hash). A Control change appends its events
  to its Board's Level. A request that fails before a Level exists sends its events live only.
- `GET /conversations/{cid}/levels/{level}` returns `trace: {"events": [...], "prompts": {hash: text}}`
  when that Level has a saved Trace.
- At `LOG_LEVEL=DEBUG` the agent logs the text of every query (never prompts or rows).

### Bootstrap (Settings)

- `POST /api/draft` and `POST /api/boards/draft` take `"trace": true` in the JSON body.
- Trace events go into the run's existing event list as `{"type": "trace", "event": <event>}`,
  delivered live by the existing polling `GET /api/draft/{id}/events?after=N`.
- When a traced run ends, its Trace is written to `<data dir>/workspaces/<name>/traces/<run id>.json`
  for a Regenerate, or `<data dir>/traces/<run id>.json` for a New Workspace (no Workspace name
  exists yet); only the newest 3 files in each directory are kept. Saved form as above.
- `GET /api/traces` lists the saved run Traces for the current Workspace (or New Workspace).

### Client

- The Trace switch sits in `.head-actions`; its state is read and written in `try/catch`.
- Live events are kept on the Tab being answered; a reopened Level gets its saved Trace.
- With the switch on, each Tab shows a "Trace" `<details>` of its events, oldest first. A Tab is
  one Level, so the Tab strip and log give the Turn → Level grouping; a Board Tab collects its
  Control changes' events. Every text (prompt, messages, SQL, errors) renders as plain text in
  `<pre>` through Lit bindings, never as markdown. Switching it off hides the pane; events of
  requests sent while on stay on their Tab. A retried Tab starts a fresh Trace.
- The Settings run card shows trace events of the current run when the switch is on.

## Criteria → tests

| # | Criterion | Test |
|---|---|---|
| T1 | No Recorder: `emit`/`sql_event`/`model_event` do nothing | `agent/tests/test_trace.py::test_untraced_records_nothing` |
| T2 | System prompt sent once, by hash; model events carry message deltas | `test_trace.py::test_prompt_once_and_deltas` |
| T3 | Closed Recorder drops events (background task after the request) | `test_trace.py::test_closed_recorder_drops` |
| T4 | A failing live callback doesn't fail the request | `test_trace.py::test_live_failure_is_swallowed` |
| T5 | `saved_form` drops prompt events and omits row previews | `test_trace.py::test_saved_form_omits_rows` |
| T6 | SqlRunner emits one sql event per query: ok, failure, guard rejection, with source | `test_trace.py::test_sql_events_from_runner` (db) |
| T7 | LlmLoop emits model events with reply and tokens; a failed attempt has `error`, and its retry lists the same messages | `test_trace.py::test_llm_loop_model_events_with_tokens_and_a_retry` |
| T7b | A Control change appends its events to its Board Level's saved Trace | `test_trace.py::test_a_control_change_appends_to_its_board_levels_trace` (db) |
| T8 | Executor with `explorerTrace` streams events as status metadata and marks the final message; without it, none | `test_trace.py::test_executor_streams_trace_and_saves_it_without_rows` / `test_executor_untraced` |
| T9 | Saved Level keeps its Trace without row values; level route returns it with prompts | `test_trace.py::test_saved_trace_has_no_rows` (db) |
| T10 | Bootstrap traced Draft run puts trace events in run events and saves one file, newest 3 kept | `bootstrap/server/tests/test_trace_runs.py` |
| T11 | Client: switch persists, sends metadata, shows events as plain text | `client/test/trace.test.ts` |

## Decisions and non-goals

- Settings uses the existing run-event polling, not a new SSE stream (it is already live).
- Not traced: Board warm-up, Settings Report/Connect/Export (synchronous steps), Drill prefetch.
- SQL error messages and bound values are kept in saved SQL events (values are single cells,
  already in the saved display SQL of Drill Results); model messages use the saved history's
  omission rules.
- No gating setting; privacy and security to be refined later (ADR 0053).
- Saved Settings Traces are served by `GET /api/traces` only; no client viewer yet (amended during
  the build, 2026-11-07). The live run card shows the current run's Trace.
- Saved run Trace files are named `<time_ns>-<run id>.json` so the newest-3 pruning is ordered by
  name, not by file mtime (amended during the build).
- Saved Settings run Traces keep the system prompt's hash only, never its text: the bootstrap
  prompt embeds the report, whose pg_stats `most_common` values are data (refuter, 2026-11-07).
  The live run card shows the prompt only while the run's events are in memory.
- Known limits: a Board render shared in flight with another request (single-flight) or served
  from the render cache records no SQL in the joining request's Trace; the first model event of
  each traced request lists the whole (compacted) history, so saved Traces grow with Conversation
  length.
- The bootstrap's run_sql tool result holds rows under `rows` (not `rows_preview`): its saved form
  also replaces `rows` with `rows_omitted` (found by T10 during the build).

## Test commands

`cd explorer/agent && uv run pytest` · `cd explorer/bootstrap/server && uv run pytest` ·
`cd explorer/client && npm test && npm run build`
