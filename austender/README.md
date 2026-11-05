# austender

Australian Commonwealth contract notices (AusTender OCDS API) in Postgres 18, built for the explorer's
LLM agent. `SPEC.md` is the source of truth. Python 3.13, `uv`, `httpx`, `psycopg` 3, plain SQL migrations.

Schemas: `raw` (releases as received), `core` (normalised), `mart` (what the explorer reads), `ops`
(runs, checkpoints, rejected releases, applied migrations). `core` and `mart` are a pure function of
`raw`: `rebuild` from the same raw gives identical tables.

## Setup

```bash
cd austender
cp .env.example .env          # set LOADER_PASSWORD, EXPLORER_RO_PASSWORD, INGEST_USER_AGENT (never commit .env)
docker compose up -d          # Postgres 18 on host port 5434, named volume austender_pgdata
uv sync
uv run austender migrate      # creates roles austender_loader and explorer_ro, applies migrations
```

`INGEST_USER_AGENT` is required for any command that calls the API (it fails fast if unset). Use something
descriptive with a contact address, e.g. `glassline-austender/0.1 (research; you@example.org)`.

## Commands

| Command | What it does |
|---|---|
| `uv run austender migrate` | Create/update roles, apply pending migrations in `migrations/` |
| `uv run austender fetch-month 2026-09` | Fetch one UTC month (`contractPublished`) into `raw`, then checkpoint it |
| `uv run austender backfill --from 2013-01 --to 2026-09 [--resume]` | `fetch-month` for each month, oldest first |
| `uv run austender rebuild` | Rebuild `core` and `mart` from `raw` in one transaction |
| `uv run austender stats` | Row counts, latest checkpoint, rejected count |
| `uv run pytest` | Tests (need Docker for the Postgres 18 Testcontainers; no network) |

### Starting the full backfill

It is deliberately not part of setup. About 150 months from 2013-01, at roughly 45-70 pages and about one
minute per month (requests are at least 1 s apart), so around 2.5 to 3 hours. Run it in a terminal that
survives, then rebuild:

```bash
uv run austender backfill --from 2013-01 --to 2026-09
uv run austender backfill --from 2013-01 --to 2026-09 --resume   # after an interruption: skips completed months
uv run austender rebuild
```

Or run `./backfill.sh` (inside `tmux`), which does all of the above from 2013-01 to the last complete UTC
month, keeps a Mac awake, and logs to `logs/` (git-ignored). `./backfill.sh 2020-01 2020-12` sets the range.

A month is checkpointed only after all its pages are stored; an interrupted month is fetched again and
already-stored releases are skipped. A release id seen again with a different payload is recorded in
`ops.rejected_release` and the stored row is left unchanged. The latest month is incomplete until it ends,
so re-run it later (it is idempotent) and rebuild.

## How the explorer connects

Read-only role `explorer_ro` has `SELECT` on schema `mart` only (tables created later included) and
`default_transaction_read_only = on`. It cannot see `raw`, `core` or `ops`, create temp tables or use
schema `public`. Its real write barrier is the missing privileges, so the explorer should still open
`READ ONLY` transactions.

```
postgresql://explorer_ro:<password>@localhost:5434/austender
```

The agent's whole data dictionary is the `COMMENT ON` text of the `mart` tables and columns (about 6.8k
characters); print it with:

```sql
SELECT c.relname, a.attname, col_description(c.oid, a.attnum)
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'mart' AND c.relkind = 'r' ORDER BY 1, a.attnum;
```

### In explorer tests

```python
from austender.testing import build_test_db
build_test_db(dsn)   # dsn: superuser DSN of an EMPTY database (e.g. Testcontainers)
```

This creates the roles, migrates, loads the hand-written seed releases (`tests/seed/`) and rebuilds `core`
and `mart`. Afterwards `explorer_ro` / password `explorer_ro` (override with `explorer_ro_password=`) can
read `mart`. Add the package as an editable path dev-dependency; it needs only `httpx`, `psycopg` and `tzdata`.
`tests/seed/expected.py` has the hand-computed totals.

## Rules the mart enforces for you

- `mart.contract` has every notice, cancelled ones included, flagged `is_cancelled`. Cancelled notices keep
  their full value in the source, so spend questions should use `mart.contract_supplier` or the `*_fy`
  summary tables, which exclude them.
- A multi-supplier contract's current value is split equally across its suppliers (`value_share`), so
  summing never double counts.
- Current value is the highest-numbered non-cancelled amendment's value (skipping amendments without a
  value), else the original. Amendments are
  full snapshots; the API gives them no date, so none is stored.
- Dates are `Australia/Canberra`; financial years run July to June (`2024-25`).
- Suppliers are keyed by valid ABN, else `name:` plus the normalised name. A withheld name ("Name
  Withheld") without an ABN gets `withheld:<notice_id>`, so placeholders never merge into one supplier.
- `rebuild` empties and refills `mart` with `DELETE` in one transaction, so the explorer keeps reading the
  previous data until it commits.

## Data licence and attribution

Contract notices come from [AusTender](https://www.tenders.gov.au), Department of Finance,
Commonwealth of Australia, licensed under
[CC BY 3.0 AU](https://creativecommons.org/licenses/by/3.0/au/). The licence excludes the
Commonwealth Coat of Arms and Approach to Market (ATM) material, which is copyright. This project
loads contract-notice data only. Keep it that way: do not ingest or republish ATM material.
The tables here reshape the notices; that reshaping is ours, not the Commonwealth's. Licence questions
go to the AusTender Administrator, tenders@finance.gov.au. Credits for the whole repo are in the root
`NOTICE`.
