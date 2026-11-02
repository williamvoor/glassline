---
status: proposed
---

# Scenarios are scored by rerunning the SQL on the Surface against live reference SQL

End-to-end scenarios ask the real agent a question and decide pass or fail without a human. Every Visual shows the SQL behind it (ADR 0001), so the scorer reruns that SQL itself, read-only through the agent's own SQL runner, and gets the raw rows behind the answer. It then checks that each expected value appears in those rows, within a tolerance. The expected values come from reference SQL, also run at check time, so they follow the data when the backfill updates. A scenario can list alternates (another correct reading, such as one Defence key or both) and labelled wrong answers (such as "cancelled included"), so a failure says which mistake was made.

## Considered Options

- **Compare the agent's SQL text with the reference SQL.** Rejected: many different queries are correct, so text comparison fails good answers.
- **Read the numbers off the screen** (Metric text, table cells, chart values). Rejected: they are formatted ($38.4B), rounded and scaled, so matching them means undoing each format. The SQL gives the exact values.
- **Store expected values in the scenario file.** Rejected: they go stale with each backfill. The reference SQL is what a person reviews, and it stays right.
- **Score by hand, as the effort check does.** Rejected for scenarios: 25 questions × several runs is too many to read, and hand scoring drifts.

## Consequences

- An answer given only in prose, with no Visual, has no SQL on screen and fails, unless the scenario says no Visual is expected.
- Matching values in rows proves the data is right, not that the screen shows it well. Layout quality is still checked by eye.
- Where a reused Result holds many candidate values (a follow-up), a scenario also requires the value to be stated in a Metric or the prose. A missing entity is checked by its rows being empty and its prose having no invented numbers.
- Free-text phrases are never scored: two review rounds showed regexes can't tell a refusal from a claim. Behaviour like a refused write is a smoke test, read by a person in the report.
- The scenarios are evaluation data. They must never be used to train Muse Glimmer (as with ADR 0004's splits) or be put in the agent's prompt.
