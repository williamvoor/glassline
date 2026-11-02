---
status: accepted
---

# Names are matched in the main query, which returns what it matched

In the effort check, questions that named an entity ("Department of Defence") spent a whole model call on a lookup query for its key before running the real query. That's about 2 s, when SQL takes milliseconds. The lookups also gave inconsistent answers: some runs kept one of Defence's two keys, others both, and most answers didn't say which. The prompt now tells the model to filter by name with `ILIKE` inside the query that answers the question. When a pattern can match more than one name, the same query returns the matched names (for example `string_agg(DISTINCT name, '; ')`) and the answer cites them. The model gives up choosing among matches before it queries, but saves a model call, and the user sees what was included.

## Considered Options

- **Keep the lookup, and tell the model to report which keys it used.** Rejected: it keeps the extra model call, and speed is the first requirement.
- **Dataset notes listing known ambiguous names** (such as Defence's two keys). Rejected: it's per-dataset upkeep, and the explorer must work from an annotated database alone.

## Consequences

- A broad pattern can include more than the user meant (`%health%` matches several departments). The matched names in the answer are what shows this. The user can then narrow the question.
