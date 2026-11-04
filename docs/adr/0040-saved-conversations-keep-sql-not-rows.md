---
status: accepted (2026-11-04, the user)
---

# Saved Conversations keep SQL, not rows, and re-run it when shown

Conversations are saved so they can be resumed after a restart
(`explorer/specs/workspaces/SPEC.md`). A saved Conversation holds no row data. Each Result keeps
its SQL, its columns, its row count and a hash of its rows. Each Level keeps the input it was built
from: the model's reply with its Macro calls, never the expanded Surface. When a Level is shown
again, its Results are re-run and its Macros expanded again. If a re-run's hash differs from the
saved one, the Level is marked "The data has changed since this answer". The saved model history
has its row previews removed, so on resume the model sees each Result's SQL, columns and row count,
and queries again if it needs values. The user chose this (2026-11-04) over storing rows.

## Considered Options

- **Store the rows** (capped). A reopened answer would show exactly what it showed, but row data
  would sit on disk, against "never log row data", and the store would grow with every answer.
- **Store the expanded Surfaces.** Simplest to replay, but they embed rows (tables, chart data,
  `Cite` values), so this is "store the rows" in another form.
- **Rebuild the model's row previews on resume** by re-running every Result first. The model would
  behave exactly as before, but resuming would wait on a re-run of the whole Conversation. Summaries
  are simpler and cost at most an extra query when the model needs values.

## Consequences

- The questions and the model's replies are saved as plain text in the owner-only data directory
  (the user, 2026-11-04). They are stored, never logged.

- Prose and Visuals stay consistent with each other, because prose numbers are `Cite`s of the same
  re-run Results (ADR 0001). The model's own wording can go stale, and the hash marks those Levels.
- A query with `LIMIT` and ties can return a different row set from unchanged data, which is a false
  "data has changed" mark. This is accepted.
- A saved query that no longer runs (a dropped column, a timeout) shows its error and SQL in place
  of its Visuals. The rest of the Level renders.
- An unavailable Workspace's Conversations can't be opened, because nothing to show is stored
  without its database.
- A saved Board Tab whose Board file changed since (hash mismatch, or removed) shows "This Board
  has changed" with a button to open the current one, instead of rendering an old definition.
