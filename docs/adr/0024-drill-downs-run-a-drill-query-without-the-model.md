---
status: accepted
---

# Drill-downs run a Drill query the agent wrote ahead of time, without the model; anything that consults the agent is confirmed first

Clicking a Visual to see the next level of detail should feel instant, but a model run takes about 8 s, and speed is the explorer's first requirement. So when the agent writes a Visual it may also write a Drill query: SQL with placeholders for the clicked row's values. A click binds the row's values from the stored Result (the client sends only IDs and a row index), runs the query read-only, and shows the result with a Macro, with no model call. The first rows on screen are prefetched in the background. Anything that does need the model (an Ask) is confirmed by the user before it is sent. ADR 0001 still holds: the data comes from the database through a Macro, and the SQL is shown.

## Considered Options

- **Every click is a model run.** Simplest, but each click costs a full Turn. This remains the fallback (an Ask) for Visuals without a Drill query.
- **Prefetch model Turns for the top rows.** Clicks would be instant and come with prose, but each answer would trigger several extra model calls, most of them never viewed.
- **Compute the next level in the client from rows already loaded** (Vega-Lite selections). No new data can be reached that way, and it bypasses the server's checks. Deferred for re-slicing rows already on screen.
- **Generate UI code at runtime.** Rejected: it breaks ADR 0001 and 0002, needs a sandbox, and is slow.

## Consequences

- The first answer is slower, because the agent writes extra SQL. It is capped per Surface and must be measured.
- A Drill query is checked before the answer is sent (guard, placeholders, NULL-safe comparisons, a `LIMIT 0` run with bound values that also gives its output columns), so a broken one costs a Repair rather than a failed click. When Repairs run out, the Drill query is dropped, not the answer.
- Placeholders are rewritten to native `$n` parameters and run with psycopg's raw cursor, so the model's `%` literals need no escaping. The SQL shown for a Drill-down is a display copy with the values written in.
- The client sends only IDs. Everything an Action needs is copied when the Visual is built, so Actions don't depend on the ResultStore keeping old Results, and database text reaches the model only inside tool results.
- A Drill-down has no prose. The model learns what it showed through a tool result added to the history before its next run.
- Prefetch adds database load, so it is limited to one query at a time across the whole agent, with a short timeout and a cap on held results.
- Only one level is reachable without the model; a Drill-down's own Visual has no Drill query.
- Inputs (sliders, pickers) can later reuse the same mechanism, binding an input's value instead of a row's.
