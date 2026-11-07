# SPEC: VicProp database v1

Status: draft (2026-11-03). Source of truth for `vicprop/`.

## 1. Purpose

Build a Postgres database of Victorian suburb median house prices from the
`houses-by-suburb-2015-2025.xlsx` workbook (one sheet, ~797 localities, median
prices 2015–2025 plus a preliminary 2026 column and published change figures),
committed in this directory. Its consumer is the explorer (`../explorer/`): an
LLM agent that writes SQL against this database to answer plain-English
questions, and charts the results.

This is the `vicprop/` second dataset promised in `../docs/bootstrap-build-prompt.md`
§2 and `../docs/summary-20261102-3.md` (manual download; scripts get HTTP 403
from `land.vic.gov.au`).

## 2. Non-negotiables

- **Python only** (3.13, `uv`). No TypeScript, no Node.
- The workbook is loaded with `../dataload` (amended 2026-11-07, ADR 0052): `load.toml` puts it
  into `staging.houses_raw` as it is, and `transforms/10_mart.sql` refills `mart` from it in the
  same transaction. Plain SQL: migrations are `.sql` files. No ORM, no Alembic.
- Tests never touch the network. Database tests use a disposable Postgres 18 (Testcontainers).
- Never modify anything outside `vicprop/` (the only exception is the new
  `../datasets/vicprop/` config, which this spec also covers).
- Secrets live in `vicprop/.env` (git-ignored by the repo root `.gitignore`).
  Commit an `.env.example` with placeholders. Never print or log passwords or
  URLs containing them.
- The workbook file is committed (138 KB). `load` always reads it deterministically;
  there is no incremental ingest and no backfill.

## 3. Runtime and layout

- Postgres 18 in Docker Compose (`vicprop/docker-compose.yml`), host port **5435**,
  database `vicprop`, a named volume.
- Roles, created by `ensure_roles` with passwords from `.env`:
  - `vicprop_loader`: owns the schemas, does all writes.
  - `explorer_ro`: `LOGIN`, `SELECT` on schema `mart` only (including future tables,
    via default privileges), nothing else. Set
    `ALTER ROLE explorer_ro SET default_transaction_read_only = on`.
- Schemas: `mart` (what the explorer reads), `ops` (applied migrations). No `raw`:
  the committed workbook is the raw source of truth.

```
vicprop/
  SPEC.md  README.md  AGENTS.md  pyproject.toml  uv.lock  .env.example  docker-compose.yml
  houses-by-suburb-2015-2025.xlsx
  migrations/0001_….sql …            applied in order by our own runner
  src/vicprop/
    config.py      settings from env
    cli.py         `uv run vicprop <command>`
    db.py          connections and the migration runner
    loader.py      load.toml through dataload → staging → transforms → mart
    testing.py     build_test_db(dsn): migrate + load workbook (used by explorer tests)
  tests/
    test_loader.py   the load rules on small workbooks with the real layout (Testcontainers)
    test_db.py       Testcontainers tests: golden mart rows, comments, idempotent load
  load.toml          dataload manifest (owner login from the .env names)
  transforms/        SQL that refills mart from staging.houses_raw
```

The explorer will add this package as an editable path dev-dependency and call
`vicprop.testing.build_test_db(dsn)` against its own Testcontainers Postgres. Keep that
function's signature stable and its dependencies light (`dataload`, `psycopg`).

CLI commands: `migrate`, `load [--xlsx PATH]`, `stats` (localities, price rows, year range).

## 4. The workbook (verified 2026-11-03)

- One sheet (`Sheet1`), header rows 0–4, data rows 5–801: **797 localities**, distinct names.
- Year price columns 2015–2025 (integers as strings), each followed by a flag column:
  `' '` normal, `'^'` or `'*'` flagged. The workbook carries no legend for the flags.
- `'-'` means no median published for that year (new growth-area estates, e.g. BONNIE
  BROOK has no 2015–2019 median). Absent medians become **no row** in `mart.suburb_price`,
  never a zero or NULL row.
- `Prelim 2026`: a preliminary 2026 median, `'NA'` in 32 localities (no row then).
  Its flag column is `'^'` in ~half the rows — preliminary means preliminary.
- Published summaries: `Change 24-25 (%)`, `15-25 (%)`, `Growth PA 15-25 (%)`;
  `'NA'` where not computable (no 2015 median). Stored as published, NULL where `'NA'`.

## 5. Mart shape

Small (~9.5k rows) and already trap-free, so no summary tables beyond the published figures:

- `mart.suburb_price(locality, year, median_price, is_prelim, flagged)`: one row per
  published median. `year` 2015–2025 ordinary, 2026 preliminary (`is_prelim`). `flagged`
  is true when the source flag is `'^'` or `'*'`.
- `mart.suburb_trend(locality, change_24_25_pct, growth_15_25_pct, growth_pa_pct)`:
  one row per locality, NULLs where the source says `'NA'`.

`load` refills both tables in one transaction (`DELETE`, not `TRUNCATE`), so readers keep
seeing the old data until it commits. Loading twice gives identical tables. The published change
figures are stored with `trim_scale`, so `5.0` in the source is `5`, as before the dataload port
(`tests/fixtures/mart_golden.json` holds the pre-port rows, and a test compares them).

## 6. Comments and speed

- The `COMMENT ON` text of the `mart` tables and columns is the explorer agent's **only
  data dictionary**. Every table and column needs a comment written for an LLM that knows
  nothing about Victorian property data: meaning, units (AUD), what `flagged`,
  `is_prelim` and missing rows mean, and which table to prefer for what. The total must
  stay under 12,000 characters (tested).
- Speed: the tables are tiny; one index on `(year)` and one on `(locality)` is enough.
  No `EXPLAIN ANALYZE` ritual needed at this size.

## 7. Explorer dataset config

`../datasets/vicprop/`: `dataset.toml` (`allowed_schemas = ["mart"]`, AUD
currency), `notes.md` (prefer the long `suburb_price` table; 2026 is preliminary;
flagged medians need caution; `'-'` years have no row), `glossary.md` (locality, median
price, preliminary, flagged), and `examples` starter questions. No `scenarios.toml` yet
(evaluation data comes later, if needed).
