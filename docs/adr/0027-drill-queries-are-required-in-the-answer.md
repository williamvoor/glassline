---
status: accepted
---

# Drill queries are required in the answer, paid for with first-answer time

ADR 0024 lets a click run a Drill query without the model, but left writing one to the model's judgement, and in the first live run Muse Spark never wrote one: every click became a confirmed Ask of about 6 s. We decided the prompt requires a Drill query for each chart or table whose rows have detail below them (at most two per Surface), with one generic example, and accept a longer first answer for it. Speed is the top requirement, so this holds only while the measured cost stays within 1 s on the median first answer (SPEC §17); past that, the wording goes back to optional.

## Considered Options

- **Dataset-configured drill paths** (for example `datasets/<name>/drills.toml`), matched to a Visual's columns with no model cost. Rejected: a drill must keep the answer's own filters and period, which only the model's SQL knows, so the server would have to parse and rewrite arbitrary SQL.
- **Dataset hints in the prompt** (the Dataset's hierarchies, with the model still writing the SQL). Kept as the fallback if the required wording yields too few valid Drill queries.

## Consequences

- Every answer with a chart or table costs more output tokens and up to one `LIMIT 0` check.
- The prompt's prefix changes, so the cached prefix is rebuilt once and the snapshots change.
