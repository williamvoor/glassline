---
status: accepted
---

# Explorer tests build their database with the austender package

The explorer's database tests start a disposable Postgres 18 and call `austender.testing.build_test_db(dsn)`. That function creates the roles, applies austender's migrations, loads its hand-written seed and rebuilds `mart`. `austender` is an **editable path dev-dependency** of the agent, never a runtime one. The tests then run as the real read-only role (`explorer_ro`), against the real `mart` schema and comments, and assert totals imported from `austender/tests/seed/expected.py`. As a result, the guard, the privilege check and the schema section are tested against exactly what production serves, and a change to austender's schema breaks explorer tests immediately instead of in a live run.

## Considered options

- **A SQL dump or fixture schema committed in `explorer/`:** rejected. It drifts silently from austender's migrations and comments, and the schema section is generated from those comments.
- **A generic fake dataset, to keep `explorer/` dataset-agnostic:** rejected for now. It would test the code but not the role grants or the real comments. The coupling is test-only, so runtime code stays dataset-agnostic.

## Consequences

- The dependency must be editable: `build_test_db` finds its seed relative to austender's source tree.
- `build_test_db`'s signature and its light dependencies (`httpx`, `psycopg`, `tzdata`) are a contract that austender keeps stable.
- A second dataset will need its own test builder, or this ADR gets revisited.
