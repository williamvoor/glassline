# dataload: load CSV, Excel and Parquet files into Postgres

Status: **approved** (2026-11-07, the user). Its decisions came out of a grilling session on 2026-11-07. Decision: ADR 0052 (accepted). This is a new
top-level project. Like `austender/` and `vicprop/`, it is all Python, and it gets its own
`AGENTS.md` and `README.md` when it's built. The explorer never imports it.

## Why

The work every file-based database repeats (read a file, infer types, create a table, bulk-load,
comment, grant) gets written once. Then a new dataset needs a manifest and, if needed, some SQL,
not a copy of `vicprop/`.

## Decisions

| # | Decision | By |
|---|---|---|
| L1 | Load files as they are, one table each. Any reshaping is SQL transform files | user |
| L2 | Load into any Postgres through an owner login. No repo-owned database | user |
| L3 | Port `vicprop/` onto it. `austender/` stays as it is | user |
| L4 | A manifest, `load.toml`, describes a load. `dataload file` loads one file without a manifest (it builds a one-source manifest internally) | user |
| L5 | A load replaces its tables in one transaction (`DROP` + `CREATE` + `COPY` + transforms). Transforms create tables, not views over staging. Explorer queries during a load wait for the lock and may hit their timeout; this is a documented limitation | user |
| L6 | Libraries: stdlib `csv`, `openpyxl` (xlsx only, no .xls), `psycopg` 3 with `COPY`. Parquet needs the optional extra `dataload[parquet]` (`pyarrow`), with a clear error when it isn't installed | user |
| L7 | `dataload grant` sets up the read-only role on any database, including ones `dataload` didn't load | user |
| L8 | The owner login is either `url_env` or split fields (`host`, `port`, `database`, `user`, each literal or `*_env`, plus `password_env`). A password is never a literal | user |
| L9 | Staging tables are kept until the next load replaces them. They're private unless granted | user |
| L10 | CLI only. There's no admin UI for loads or grants | user |
| L11 | `header_match` finds the header row by its first cell, as an alternative to `header_row`. vicprop uses it | user |

## Manifest

```toml
# load.toml: paths are relative to this file
staging_schema = "staging"                     # created if missing; replaced tables live here
readonly_role  = "explorer_ro"                 # gets USAGE + SELECT on the schemas in `grant_schemas`
grant_schemas  = ["mart"]                      # staging stays private unless it is listed here
transforms     = ["transforms/*.sql"]          # run in name order after loading, same transaction

[owner]                        # the login that loads and grants (L8). Either:
url_env = "MYDATA_OWNER_DATABASE_URL"
# ... or split fields, each a literal or a *_env reference; the password only ever from the environment:
# host_env = "POSTGRES_HOST"   port_env = "POSTGRES_PORT"   database_env = "POSTGRES_DB"
# user = "vicprop_loader"      password_env = "LOADER_PASSWORD"

[[source]]
file   = "houses-by-suburb-2015-2025.xlsx"
table  = "houses_raw"          # default: the file stem (or the sheet name), normalised
sheet  = "Sheet1"              # Excel only; default: the first sheet. A sheet name or "*" (one table per sheet)
header_row = 3                 # 1-based; default 1. Excel and CSV only
header_match = "Locality"      # instead of header_row: the first row whose first cell equals this (trimmed, case-insensitive)
null_values = ["", "-", "NA"]  # strings that load as NULL; default [""]
types  = { locality = "text" } # optional overrides of inferred types

[[source]]
file = "extra/*.csv"           # a glob: one table per file
delimiter = ","                # CSV only; default: sniffed
encoding  = "utf-8"            # CSV only; default utf-8, with a BOM stripped
```

Single file, no manifest: `dataload file data.csv --schema staging --url-env X_OWNER_DATABASE_URL`.

The manifest refuses unknown keys, an `[owner]` that has both `url_env` and split fields, a
`password` key (only `password_env`), and any value that contains `://`.

## Loading rules

- **Column names** are normalised to lower snake_case. Names that start with a digit get a `c_`
  prefix (`2015` → `c_2015`). Empty headers become `column_<n>` (1-based position). Repeated
  names get `_2`, `_3` and so on. Names are capped at 63 bytes. The original header goes into
  `COMMENT ON COLUMN`.
- **Table comment:** `Loaded from <file>[, sheet <name>] by dataload`. There is no timestamp in
  the comment, because comments feed the explorer's cached prompt.
- **Type inference** (CSV and Excel) looks at every non-null value of a column and picks the
  first type that fits all of them: `boolean` (true/false/yes/no), `bigint`, `numeric`, `date`
  (ISO, or Excel date cells), `timestamp`, else `text`. Thousands separators are not stripped
  unless `thousands = ","` is set. An all-null column is `text`. Parquet keeps its own schema,
  mapped to Postgres types, and refuses types with no mapping.
- **Loading:** one transaction per manifest: create the schemas if missing, `DROP TABLE IF
  EXISTS` and `CREATE` each source table, `COPY` its rows, add the comments, run the transforms,
  then the grants. Any error rolls the whole thing back. The command prints row counts per table,
  and never the login.
- **Size:** files are read twice (once to infer types, once to `COPY`). Nothing is held whole in
  memory, apart from openpyxl's read-only stream.

## Grants (`dataload grant`)

`dataload grant --manifest load.toml` (its `[owner]`, `readonly_role` and `grant_schemas`), or
`dataload grant --url-env OWNER_URL --role explorer_ro --schema mart [--schema …] [--create-role]`
- grants `CONNECT` on the database, `USAGE` on each schema, `SELECT` on all their tables, and
  default privileges for future tables created by the owner login;
- creates the role only with `--create-role`, reading its password from `--password-env`;
- refuses to grant anything beyond these (no `INSERT`, no `CREATE`), and refuses to grant to a
  role that is a superuser or can create roles;
- prints the SQL it will run, without the password, and runs it only with `--yes`.

## vicprop port

- vicprop's `load.toml` uses split `[owner]` fields over its existing `vicprop/.env` names
  (`POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, user `vicprop_loader`, `LOADER_PASSWORD`),
  so `vicprop/.env` and `regen_dataset.sh` don't change.
- vicprop gets `load.toml` and `transforms/*.sql`. The SQL unpivots the year columns into
  `mart.suburb_price` and copies the summary columns into `mart.suburb_trend`, with the same
  rules as `loader.parse_workbook`: `-` means no row, `^`/`*` means flagged, and the
  preliminary 2026 column has `is_prelim` set.
- `loader.py` is replaced. `uv run vicprop load` calls dataload with the manifest. The existing
  migrations stay; a new migration adds the `staging` schema, which only the loader role can see.
- `testing.build_test_db(dsn)` keeps its signature and output (the explorer's tests use it).
- vicprop depends on dataload through a local editable path, the same way bootstrap depends on
  the agent.

## Out of scope

- An admin UI (L10).
- API sources (AusTender), scheduling, incremental or append loads.
- `.xls`, JSON, or SQLite files. These can be added later as readers.
- Declarative reshaping (renames or unpivots in TOML).
- Writing Connections or Datasets. The README's walkthrough goes load → grant → Connection →
  New Workspace.

## Acceptance (tests to write with the build; Docker Postgres 18, no network)

| # | Check |
|---|---|
| T1 | Name normalisation: digits, empties, duplicates, 63-byte cap, original header kept in the comment |
| T2 | Type inference per type, mixed columns fall back to `text`, `null_values` respected, all-null is `text` |
| T3 | CSV (sniffed delimiter, BOM), Excel (`header_row`, `sheet = "*"`) and Parquet (schema mapping, unmapped type refused) each load the expected rows |
| T4 | A transform error rolls back the whole load, and the previous tables are still there and readable |
| T5 | After a load, `readonly_role` can `SELECT` the granted schemas, can't write, and can't see `staging` unless granted |
| T6 | `grant` refuses a superuser role, never grants write rights, and with no `--yes` changes nothing |
| T7 | vicprop: the committed workbook loaded through dataload gives `mart` tables identical, row for row, to today's loader (a golden snapshot taken before the port) |
| T8 | No login or password appears in any output or error |
| T9 | `[owner]`: `url_env` and split fields both connect. Both together, or a literal password, are refused |
| T10 | Parquet without the extra fails with "install dataload[parquet]". CSV and Excel work without it |
| T11 | Staging tables survive a successful load and are replaced by the next one |
| T12 | `header_match` finds a header row that moved. No match fails the load with the text it looked for. Setting both `header_row` and `header_match` is refused |

## Build decisions (2026-11-07)

Made while building, within the approved decisions:

- **Cells are trimmed** (CSV and Excel) before the null check and inference, so `" "` is empty.
  A row whose cells are all empty is skipped. Empty header cells at the right edge are dropped,
  and a data row with a value beyond the header's width is refused.
- **Names keep ASCII letters and digits only** (`Café` becomes `caf`).
- **Library:** `dataload.run(conn, manifest, role=None)`. `role` runs `SET LOCAL ROLE` first, so a
  superuser can load on the owner's behalf and the tables still belong to the owner (vicprop's
  `build_test_db` does this). `plan(manifest)` reads and infers without a database.
- **An absolute `file`** is read as is (a caller overriding the manifest's file, such as
  `vicprop load --xlsx`). Globs stay relative to the manifest.
- **CLI:** `dataload [--env-file F] load MANIFEST | file PATH --url-env V [--schema S] | grant`.
  `--env-file` never overrides the environment.
- **Parquet types:** integers are `bigint` (`uint64` is `numeric`), floats are `double precision`,
  decimals are `numeric`, timestamps with a time zone are `timestamptz`. Lists, structs and
  binary are refused.
- **`grant --create-role`** also sets `default_transaction_read_only = on` on the new role. It
  refuses to grant to the login running it.
- **vicprop** refills its migration-made `mart` tables (keeping their comments and indexes)
  instead of creating them, and stores the published change figures with `trim_scale` so the
  rows match the old loader's exactly (T7).

### After review (2026-11-07)

- **A number with a leading zero stays text** (`0800`, `007`): as a number its zero would be
  lost. `0`, `-0` and `0.5` are still numbers. Only ASCII digits count, and integers longer than
  4,000 digits are text.
- **A string timestamp with an offset stays text**, since a `timestamp` column would drop the
  offset. Parquet and Excel timestamps keep their own types.
- **`table` on a source that expands to several tables is refused**, and `types` is checked on
  the library path too, not only when parsing a manifest.
- **File problems** (bad bytes, an unknown encoding, a broken CSV, a missing transform) are load
  errors that name the file.
- **vicprop's transform checks the layout first** (2026 under Prelim, only `^`/`*` in the flag
  columns) and stops the load otherwise, as the old parser did.
- **A consumer of vicprop** must declare dataload's path in its own `[tool.uv.sources]`, because
  uv doesn't read a dependency's sources.
- **Deferred:** openpyxl's read-only mode trusts a sheet's declared dimension, which some
  generators write wrongly (rows would be cut short). `grant --create-role` sends the new
  password to the server as a literal, which a server logging DDL would record; pre-hashing
  it (SCRAM) would avoid that.

