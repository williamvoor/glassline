# AGENTS.md

Guidance for coding agents working in `austender/`. Read `SPEC.md` (source of truth) and `README.md`
(setup, commands, how the explorer connects) first. This file covers the rules and traps that are easy
to break.

## What this is

AusTender OCDS contract notices in Postgres 18, built for the explorer's LLM agent. Data flows
`API → raw → core → mart`. The explorer reads only `mart`, through the read-only role `explorer_ro`.
`ops` holds ingest runs, checkpoints, rejected releases and applied migrations.

| Path | Role |
|---|---|
| `src/austender/api.py` | OCDS client: pagination, retries, rate limit |
| `src/austender/ingest.py` | `fetch-month` / `backfill` into `raw`, checkpoints |
| `src/austender/normalise.py` | `raw → core` |
| `src/austender/mart.py` | `core → mart` (`rebuild`) |
| `src/austender/abn.py` | ABN checksum, `name_norm` |
| `src/austender/db.py` | Connections, roles, migration runner |
| `src/austender/testing.py` | `build_test_db(dsn)`, which the explorer's tests use |
| `migrations/NNNN_*.sql` | Plain SQL, applied in order |
| `tests/seed/` | Hand-written releases; `expected.py` has the hand-computed totals |

## Commands

```bash
uv sync
uv run pytest                                  # needs Docker (Testcontainers Postgres 18), no network
uv run pytest tests/test_seed_expected.py::test_name
docker compose up -d                           # local DB on host port 5434
uv run austender migrate | fetch-month YYYY-MM | backfill --from YYYY-MM --to YYYY-MM [--resume] | rebuild | stats
```

## Hard rules

- **Python only** (3.13, `uv`), `httpx` and `psycopg` 3. No ORM, no Alembic, no TypeScript.
- **Do not run `backfill`** unless the user asks. It takes about 3 hours and hits a public API. A
  single `fetch-month` is fine when it is needed.
- **Tests never touch the network.** Use `httpx.MockTransport` for the API and inject the sleep.
- **Never edit an applied migration.** The runner stores each file's sha256 and refuses to run if a file
  changes. Add a new `NNNN_*.sql` instead.
- **Secrets:** `.env` is git-ignored. Never print or log passwords or DSNs that contain them. New
  settings go in `.env.example` with placeholders.
- Don't modify anything outside `austender/`. `../fti/` may be read for API lessons but not changed.
- Keep `build_test_db(dsn)`'s signature stable and its dependencies light (`httpx`, `psycopg`, `tzdata`),
  because the explorer depends on it.
- Nothing in `mart` may need PL/pgSQL functions. Use plain tables with indexes, not views.

## Data invariants (tests enforce these, so keep them true)

- `core` and `mart` are a **pure function of `raw`**: `rebuild` run twice gives identical tables. Derive
  keys from source identity, not sequences.
- `raw.release` is append-only, deduplicated on the release `id` (never `contracts[0].id`). A known id
  with a different payload hash goes to `ops.rejected_release` and the stored row stays unchanged.
- `value.amount` arrives as a string. Parse it straight to `numeric(18,2)`, never via float.
- Dates are converted to `Australia/Canberra` **before** taking the date. Financial years run July to
  June and are written `2024-25`.
- Amendments are full snapshots ordered by `k` in `CN…-A<k>`. The API gives them no date. **Never store
  the amendment release `date` as an amendment date**, because it is a re-publication stamp.
- Current value is the highest-`k` non-cancelled amendment that has a value, else the original. A
  cancelled amendment never sets it.
- `mart.contract` includes cancelled notices (`is_cancelled`). `mart.contract_supplier` and the `*_fy`
  summaries exclude them. `value_share` = current value ÷ number of suppliers, unrounded, so sums never
  double count.
- `supplier_key` is the valid ABN, else `name:<name_norm>`. A withheld name without an ABN gets
  `withheld:<notice_id>`.
- `rebuild` refills `mart` in one transaction (`DELETE`, not `TRUNCATE`), so readers keep seeing the old
  data until it commits.

## Changing `mart`

The `COMMENT ON` text of the `mart` tables and columns is the explorer agent's **only data dictionary**
(it goes into the LLM prompt). Any new table or column needs a comment written for an LLM that knows
nothing about AusTender: meaning, units (AUD as published), what is included or excluded, how nulls
should be read, and which table to prefer for what. The total must stay under 12,000 characters (it is
tested). It is about 6.8k now.

Speed comes right after correct numbers. Add indexes for the access paths (FY, agency, supplier, UNSPSC
prefix, publish date). Check new query shapes with `EXPLAIN ANALYZE`. The target is typical aggregates
under 500 ms on about 870k contracts.

Seed changes: update `tests/seed/expected.py` by hand-computing the numbers. Don't regenerate them from
the code under test.

## API traps (see SPEC §4)

- Stop paginating only when `links.next` is absent **or** `releases` is empty. A short page can still
  have a next link that leads to an empty page.
- Send requests one at a time, at least 1000 ms apart. Retry network errors, 429 and 5xx (5 s backoff,
  doubling, 5 attempts). Don't retry other 4xx.
- A missing `findById` comes back as HTTP **400** with `errorCode` 100, not 404.
- `INGEST_USER_AGENT` is required. Fail fast if it is unset.

## Commits

Conventional Commits per the repo's `.gitmessage`, scope `austender`
(e.g. `fix(austender): …`).
