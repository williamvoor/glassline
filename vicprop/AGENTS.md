# AGENTS.md

Guidance for coding agents working in `vicprop/`. Read `SPEC.md` (source of truth) and
`README.md` (setup, commands, how the explorer connects) first.

## What this is

Victorian suburb median house prices in Postgres 18, built for the explorer's LLM agent.
Data flows `committed .xlsx → mart`. The explorer reads only `mart`, through the
read-only role `explorer_ro`. `ops` holds applied migrations.

| Path | Role |
|---|---|
| `src/vicprop/loader.py` | Workbook parse (pure) + `load` into `mart` |
| `src/vicprop/db.py` | Connections, roles, migration runner |
| `src/vicprop/cli.py` | `uv run vicprop <command>` entry point |
| `src/vicprop/config.py` | Settings from `.env` |
| `src/vicprop/testing.py` | `build_test_db(dsn)`, which the explorer's tests use |
| `migrations/NNNN_*.sql` | Plain SQL, applied in order |
| `houses-by-suburb-2015-2025.xlsx` | The committed source workbook |

## Commands

```bash
uv sync
uv run pytest                                  # needs Docker (Testcontainers Postgres 18), no network
docker compose up -d                           # local DB on host port 5435
uv run vicprop migrate | load [--xlsx PATH] | stats
```

## Hard rules

- **Python only** (3.13, `uv`), `openpyxl` and `psycopg` 3. No ORM, no Alembic, no TypeScript.
- **Tests never touch the network.** Build test workbooks in memory with `openpyxl`.
- **Never edit an applied migration.** The runner stores each file's sha256 and refuses to run if a file
  changes. Add a new `NNNN_*.sql` instead.
- **Secrets:** `.env` is git-ignored. Never print or log passwords or DSNs that contain them. New
  settings go in `.env.example` with placeholders.
- Don't modify anything outside `vicprop/`, except `../datasets/vicprop/` (the dataset config,
  covered by SPEC §7).
- Keep `build_test_db(dsn)`'s signature stable and its dependencies light (`openpyxl`, `psycopg`,
  `tzdata`-free), because the explorer depends on it.
- Nothing in `mart` may need PL/pgSQL functions. Use plain tables with indexes, not views.

## Data invariants (tests enforce these, so keep them true)

- `'-'` (no published median) means **no row** in `mart.suburb_price`, never zero or NULL.
- `'NA'` prelim 2026 means no 2026 row; `'NA'` summaries mean NULL in `mart.suburb_trend`.
- `flagged` is true exactly when the source flag is `'^'` or `'*'`. The workbook has no legend:
  comments say "treat with caution", never assert a specific cause.
- `load` run twice gives identical tables (`DELETE` + `INSERT` in one transaction).
- Every `mart` table and column has a `COMMENT ON`, written for an LLM that knows nothing about
  Victorian property data. Total comment text stays under 12,000 characters.

## Commits

Conventional Commits per the repo root's `../.gitmessage`, scope `vicprop`
(e.g. `feat(vicprop): …`).
