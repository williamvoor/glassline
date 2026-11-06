# vicprop

Victorian suburb median house prices in Postgres 18, built for the explorer's LLM agent.
The source is the committed `houses-by-suburb-2015-2025.xlsx` workbook, containing annual
medians for 2015–2025, preliminary 2026 medians, flags, and published growth figures.
This is a small second Dataset for the dataset-agnostic Explorer and its bootstrap tool.

See [SPEC.md](SPEC.md) for the behavioral contract (its header remains marked draft),
[AGENTS.md](AGENTS.md) for change rules, and the
[Explorer README](../explorer/README.md) for the application that consumes it.

## Architecture

```mermaid
flowchart LR
    X[Committed Excel workbook] --> P[loader.parse_workbook]
    P --> L[loader.load: one transaction]
    L --> PRICE[(mart.suburb_price)]
    L --> TREND[(mart.suburb_trend)]
    PRICE --> E[Explorer: explorer_ro]
    TREND --> E
```

There is no live ingest client, raw schema, scheduler, or model call. The workbook is the
source of truth. Parsing is separate from database loading so it can be tested with in-memory
workbooks. Loading replaces the mart tables using `DELETE` and inserts in one transaction;
readers continue to see the previous committed data until the new load commits.

## Prerequisites

Python 3.13, `uv`, and Docker with Compose v2. Keep Docker running for setup and database
tests. First-time dependency/image installation needs network access; workbook loading does
not. This package uses `openpyxl`, `psycopg` 3, and plain SQL migrations, with no Node project.

## Setup and load

```bash
cd vicprop
cp .env.example .env   # then set passwords
docker compose up -d --wait # Postgres 18 on host port 5435
uv sync
uv run vicprop migrate # create roles, schemas, mart tables
uv run vicprop load    # load houses-by-suburb-2015-2025.xlsx into mart
uv run vicprop stats   # row counts, year range
```

Start this block from the repository root. Later commands assume `vicprop/`. Copy the example
only if `vicprop/.env` does not exist; otherwise edit it. This settings file is separate from
the root Explorer `.env`.

A successful load prints the price-row and locality counts. The committed source has 797
localities; the year range reaches 2026 because preliminary medians are included. `stats`
reports preliminary/flagged rows and trend localities as well as the price rows. Loading the
same workbook again produces the same tables. `docker compose stop` retains the data volume.

## Configuration and commands

The CLI loads `vicprop/.env` without overriding existing environment variables.

| Variable | Default / purpose |
|---|---|
| `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB` | `localhost`, `5435`, `vicprop` |
| `LOADER_PASSWORD` | `vicprop_loader` password; also initialises the Compose database |
| `EXPLORER_RO_PASSWORD` | Read-only login password created/updated by `migrate` |
| `VICPROP_XLSX` | `houses-by-suburb-2015-2025.xlsx`; relative paths resolve from `vicprop/` |

| Command, from `vicprop/` | Behavior |
|---|---|
| `uv run vicprop migrate` | Ensure roles and apply pending checksum-verified SQL migrations |
| `uv run vicprop load` | Parse the configured workbook and transactionally refill both mart tables |
| `uv run vicprop load --xlsx /absolute/path/source.xlsx` | Load a workbook with the same expected structure from an explicit path |
| `uv run vicprop stats` | Print counts, year range, and preliminary/flagged rows |
| `uv run pytest` | Run parser tests and Docker-backed database tests |

Compose initialises credentials only for a new volume. Editing `LOADER_PASSWORD` in `.env`
does not change an existing database password; coordinate role and settings changes.

## Tables and interpretation

| Table | Grain | Key fields |
|---|---|---|
| `mart.suburb_price` | One published median per locality and calendar year | `locality`, `year`, `median_price`, `is_prelim`, `flagged` |
| `mart.suburb_trend` | One published summary per locality | `locality`, `change_24_25_pct`, `growth_15_25_pct`, `growth_pa_pct` |

- Prices are Australian dollars as published. A suburb median is not a transaction-level
  observation: averaging suburb medians does not produce a statewide median.
- A dash in an annual source cell means **no row**, never a zero or NULL median.
- Preliminary 2026 `NA` values also produce no price row. Published summary `NA` values become
  NULL fields in `suburb_trend`.
- `is_prelim` identifies 2026. Prefer completed years for comparisons and label preliminary
  figures when using them.
- `flagged` records `^` or `*`. The workbook has no flag legend; describe flagged values as
  needing caution, without inventing a cause.
- Growth figures in `suburb_trend` are preserved as published. Recompute from the long price
  table when answering a question about a different period.

## How the explorer connects

The explorer uses the read-only role `explorer_ro` (`SELECT` on schema `mart` only),
with allowed schemas configured in `../datasets/vicprop/dataset.toml`. The Data dictionary
combines database comments (`migrations/0002_mart.sql`) with the configured
`../datasets/vicprop/comments.toml` overlay, whose descriptions take precedence.

Set these values in the repository root `.env`, replacing the password placeholder with
`vicprop/.env`'s `EXPLORER_RO_PASSWORD` (URL-encoded where necessary):

```dotenv
EXPLORER_DATA_DIR=~/.glassline/workspaces
VICPROP_DATABASE_URL=postgresql://explorer_ro:<password>@localhost:5435/vicprop
```

Then either create the Workspace in Settings (New Workspace), or list the committed Dataset in
`$EXPLORER_DATA_DIR/workspaces.toml`:

```toml
[[workspace]]
name = "vicprop"
dataset_dir = "datasets/vicprop"
dsn_env = "VICPROP_DATABASE_URL"
```

Configure `META_API_KEY` and the serving model as described in
[Explorer's setup](../explorer/README.md#run). The loader login owns the
schemas and performs writes; Explorer must use the separate `explorer_ro` login. Dataset
notes, glossary, examples, optional overlays, and Boards live under
[`../datasets/vicprop/`](../datasets/vicprop/).

## Source and implementation

The committed workbook is from the Victorian Government ([land.vic.gov.au](https://land.vic.gov.au)),
licensed under [CC BY 3.0 AU](https://creativecommons.org/licenses/by/3.0/au/); see the root
[NOTICE](../NOTICE). `loader.py` parses and loads it; `db.py` manages roles and checksum-verified
SQL migrations. `ops` stores migration history.

## Tests and schema changes

From `vicprop/`:

```bash
uv sync --locked
uv run pytest
uv run pytest tests/test_loader.py
```

Parser tests create workbooks in memory and need no database. Database tests start disposable
Postgres 18 containers and verify the committed workbook, missing/preliminary values, comments,
and idempotent loading. Tests make no external API or model calls. `build_test_db(dsn)` is an
available integration helper: it creates roles, migrates, and loads the committed workbook
into an empty database supplied by the caller. Explorer's current default test dependency
is AusTender; the VicProp helper is not selected by adding a VicProp Workspace.

See [AGENTS.md](AGENTS.md) for migration, comment-budget and deterministic parsing rules.

## Troubleshooting

| Symptom | Check |
|---|---|
| Connection/authentication failure | Compose health, port 5435, and credentials matching the existing database roles |
| Workbook not found | `VICPROP_XLSX` resolves relative to this project; check the override or use an absolute path |
| Fewer observations for a locality | Missing medians are absent rows, not zeros; preliminary 2026 also has missing values |
| Explorer shows the wrong Dataset | Check the Workspace's `dataset_dir` and `dsn_env` in `$EXPLORER_DATA_DIR/workspaces.toml`, then reload (Settings) or restart |
| Migration checksum mismatch | Restore the applied file and express changes in a new migration |
| Database tests fail before assertions | Docker must be running and able to start Testcontainers |
