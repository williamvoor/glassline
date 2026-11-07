---
status: accepted
---

# Traces are opt-in, saved without rows, and visible to anyone signed in

To debug the Explorer from its own UI, a browser switch turns on a Trace: every model call
(request messages, reply, tool calls, tokens, latency) and every SQL query (text, bound
values, source, timing, row count, error) behind a Conversation or a Settings run. A Trace
is recorded only while the switch is on, and it is saved: a Conversation's Trace with the
Conversation (per Level, deleted with it), and a Settings run's Trace in its Workspace (the
last 3 runs). It never holds data rows: SQL events carry counts, and the run_sql previews the
model saw are shown live but saved as `rows_omitted`, so ADR 0040 ("a saved Conversation keeps
its SQL, never its rows") stands. There is no server setting that hides the switch: anyone past
the deployment's login can turn it on and read the full system prompt and SQL. That was chosen
for simplicity, with privacy and security to be refined later.

## Considered Options

- **A server setting gating the switch** (off when deployed): rejected for now; revisit with the
  privacy work.
- **In-memory only**: rejected; old Conversations could not be debugged.
- **Always recording**: rejected; a Trace costs bandwidth and storage only when asked for.

## Consequences

- Saved Conversation files now hold model messages twice (history and Trace) and the system
  prompt once, keyed by its hash.
- Exceptions to "no data": saved SQL events keep Postgres error messages (which can quote a
  value) and Drill bound values; a saved Settings run Trace keeps its prompt's hash only, because
  that prompt embeds pg_stats common values.
- Each traced request's first model event repeats the Conversation's history, so saved Traces
  grow with Conversation length.
- Settings run Traces ride on the existing run-events polling (`/api/draft/{id}/events`), not a
  new SSE stream: the polling already delivers events live.
