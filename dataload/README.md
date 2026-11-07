# dataload

Load CSV, Excel and Parquet files into Postgres as they are, reshape them with SQL if needed, and
give a read-only role access. It's for building a database the explorer can read; the explorer
itself never writes. The behavioural contract is [SPEC.md](SPEC.md).

```bash
cd dataload
uv sync --locked                 # add --extra parquet for Parquet files
```

## Load one file

```bash
export MYDATA_OWNER_DATABASE_URL=postgresql://owner:<password>@localhost:5432/mydata
uv run dataload file ~/data/sales.csv --url-env MYDATA_OWNER_DATABASE_URL --schema staging
```

The login comes from the variable you name; dataload never prints it. Each file becomes one table
(`staging.sales`). Column names become lower snake_case, and the original headers are kept as
column comments. Types are inferred from every value: boolean, bigint, numeric, date, timestamp,
otherwise text. Excel options: `--sheet NAME` (or `--sheet '*'` for every sheet), `--header-row N`,
`--header-match TEXT`. Use `--null VALUE` (repeatable) for values that mean "no value".

## Load with a manifest

For anything repeatable, write a `load.toml` next to the files:

```toml
staging_schema = "staging"
readonly_role  = "explorer_ro"        # gets USAGE + SELECT on grant_schemas after the load
grant_schemas  = ["mart"]
transforms     = ["transforms/*.sql"]  # run in name order after loading, in the same transaction

[owner]
url_env = "MYDATA_OWNER_DATABASE_URL" # or host/port/database/user (each literal or *_env) + password_env

[[source]]
file = "sales/*.csv"                  # one table per file
null_values = ["", "-", "NA"]
types = { postcode = "text" }         # override an inferred type
```

```bash
uv run dataload load path/to/load.toml
uv run dataload --env-file path/to/.env load path/to/load.toml   # the environment still wins
```

A load is one transaction: it drops and recreates each staging table, loads it with `COPY`, runs
the transforms, then grants. Any error rolls everything back, and the previous tables stay as
they were. Transforms should build real tables (or refill existing ones), not views over staging,
because the next load drops the staging tables. Staging tables are kept between loads so you can
check what was loaded.

**Limitation:** while a load runs it holds locks on the tables it replaces. Explorer queries on
those tables wait, and may hit their timeout. Run loads when nobody is using the explorer.

## Give the explorer a read-only login

```bash
uv run dataload grant --url-env MYDATA_OWNER_DATABASE_URL --role explorer_ro --schema mart
# prints the SQL and changes nothing; add --yes to run it
uv run dataload grant --url-env MYDATA_OWNER_DATABASE_URL --role explorer_ro --schema mart \
  --create-role --password-env EXPLORER_RO_PASSWORD --yes
```

It grants `CONNECT`, `USAGE` on each schema, `SELECT` on their tables, and `SELECT` on tables the
owner creates later. Nothing else. It refuses a role that is a superuser or can create roles. A
role it creates is read-only by default. `dataload grant --manifest load.toml` takes the owner,
role and schemas from a manifest. Then follow
[Add an existing Postgres database](../explorer/bootstrap/README.md#add-an-existing-postgres-database).

## Tests

```bash
uv run pytest        # database tests need Docker (Testcontainers Postgres 18); no network
```
