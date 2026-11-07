---
status: accepted (2026-11-07, the user)
---

# A shared loader for CSV, Excel and Parquet files

Each database in the repo has its own loader: `austender/` reads an API, and `vicprop/` hand-parses
one workbook. A new file source means copying `vicprop/`. Most of that work is the same every
time: read a file, infer column types, create a table, bulk-load it, add comments, and grant
`SELECT` to the read-only role.

A new top-level Python package, `dataload/`, does that work for any Postgres database:

- **Loads files as they are.** Each CSV file, Excel sheet or Parquet file becomes one table in a
  staging schema. Column types are inferred, or taken from Parquet's schema. Rows are loaded
  with `COPY`. Original headers become column comments.
- **Reshapes in SQL.** Optional SQL transform files run after the load, in the same transaction,
  and build the tables the explorer reads (such as a `mart` schema). There is no declarative
  mapping language.
- **Writes to any Postgres.** A manifest (`load.toml`) names the files, schemas, transforms and
  read-only role. The owner login is a URL variable, or split fields whose password comes only
  from the environment. A reload drops and recreates its tables in one transaction, so explorer
  queries running during a load may time out. Staging tables are kept for inspection.
- **CLI only.** There's no admin UI, so owner logins never reach the explorer's processes or the
  public deployment. A `grant` command sets up
  read-only access on a database that `dataload` didn't load.
- **Is used by `vicprop/`.** vicprop's workbook parser is replaced by a manifest plus transform
  SQL, and must produce the same `mart` tables row for row. `austender/` reads an API and is
  unchanged.

## Considered Options

- **A declarative mapping** (renames, types, unpivot in TOML). This is more powerful, but it's a
  small language to design and test, and SQL already does the job.
- **A shared Postgres container in the repo.** Simpler to start, but it ties loading to the repo
  and can't load into an existing database.
- **Leave vicprop alone.** Less risk, but the loader would never be proven on a real, awkward file.
