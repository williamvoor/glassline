# SPEC: database connection (build order step 2)

Status: approved for build, 2026-11-01. Decisions: ADR 0010. Parent: `explorer/SPEC.md` §8 "Build order" item 2. Where
this file is silent, the parent spec applies (§3 tools, §7 configuration, §9 safety, §10 tests).
Previous step: `specs/thin-slice/SPEC.md`.

## Goal

The agent can talk to a dataset's Postgres safely and describe it. That means:
- configuration from the environment plus a committed dataset directory;
- the SQL guard and runner, with every query in a `READ ONLY` transaction, a statement timeout,
  a row cap that reports truncation, and the schema allowlist;
- the schema queries behind `list_tables`, `describe_table` and the prompt's schema section;
- the three tool functions, whose results land in the `ResultStore`;
- a disposable test database built by `austender.testing.build_test_db`.

All of this is tested against that database, and checked once by hand against the loaded
AusTender database.

There is still no LLM and no prompt assembly. The server keeps using the stub answerer. Step 3
connects these parts to `llm.py`, `prompt.py` and `python -m explorer_agent`.

## Files

Dataset (`explorer/datasets/austender/`, new, committed):
- `dataset.toml`: exactly the parent §7 example.
- `notes.md`: hand-written guidance for the model. It has a few short bullets and repeats nothing
  the `mart` comments already say. It covers:
  - prefer the `*_fy` summary tables, then `contract_supplier`, then `contract`;
  - values are already split across suppliers, and cancelled contracts are already excluded
    everywhere except `mart.contract`;
  - use financial years;
  - when asked about "2024", say whether calendar 2024 or FY2024-25 was used;
  - supplier names are shown as AusTender publishes them.
- `glossary.md`: short definitions of contract notice, amendment, cancelled, ABN, UNSPSC
  segment/family/class, financial year, procurement method, limited tender and agency. Each
  should be one line where possible.

Both Markdown files go into the cached prompt in step 3, so they are written for an LLM reader.
Keep them under about 2k characters together.

Agent (`explorer/agent/`):
- `pyproject.toml`, `uv.lock`:
  - Dependencies: `psycopg-pool==3.3.3` and `pglast==8.7`. pglast 8.7 has a cp313 macOS arm64
    wheel, and its embedded parser is PostgreSQL 18.6.
  - Dev dependencies: `testcontainers[postgres]==4.15.0` and
    `austender = { path = "../../austender", editable = true }`.
  - **Editable is required.** `build_test_db` finds its seed through `parents[2] / "tests" / "seed"`,
    which only exists in the source tree.
  - Register the `db` and `live_db` markers. Add `addopts = "-m 'not live_db'"`.
- `src/explorer_agent/config.py` (new): `Settings`, `Dataset` and `load_settings`.
- `src/explorer_agent/sql.py` (new): `guard`, `GuardError`, `open_pool`, `SqlRunner`.
- `src/explorer_agent/schema.py` (new): `SchemaCatalog`, `render_schema_section`.
- `src/explorer_agent/tools.py` (new): tool JSON schemas and `ToolBox`.
- `tests/conftest.py`: add the session-scoped database fixtures.
- `tests/test_config.py`, `test_guard.py`, `test_sql.py`, `test_schema.py`, `test_tools.py`,
  `test_live_db.py` (all new).

Explorer root:
- `.env.example` (new): every variable in parent §7, with placeholders. `DATABASE_URL` is shown as
  `postgresql://explorer_ro:<password>@localhost:5434/austender`.

## Behaviour

### `config.py`
- `load_settings(env: Mapping[str, str], explorer_root: Path) -> Settings`.
  - It is pure: it does not read `.env` itself. Step 3's `__main__` calls `python-dotenv` first.
    That keeps tests independent of a developer's real `.env`.
- `Settings`:
  - Every variable in parent §7, with its defaults, plus `dataset: Dataset`.
  - `REASONING_EFFORT` empty → `None`.
  - `FORMAT` not in `{express, direct_json}` → `ConfigError`.
  - Missing `DATABASE_URL` → `ConfigError`.
  - The integer settings must be positive.
  - `prompt_cache_key` defaults to `explorer-<dataset.name>`.
- `Dataset`:
  - Fields: `name`, `description`, `allowed_schemas: tuple[str, ...]` (sorted), `notes: str`,
    `glossary: str` and `currency: Currency(code, symbol)`.
  - The notes and glossary are read as UTF-8, with `\r\n` normalised to `\n`.
- Errors are `ConfigError`, and the message names the variable or file:
  - a missing `dataset.toml`, notes file or glossary file;
  - a missing key;
  - an empty `allowed_schemas`;
  - a schema name that doesn't match `^[a-z_][a-z0-9_]*$`.
- `ConfigError` messages never include `DATABASE_URL` or any other value from the environment.

### `sql.py`
- `guard(sql, allowed_schemas) -> None` raises `GuardError(message)`. It implements every rule in
  parent §9.2 using `pglast`:
  - The deny list is a module constant.
  - A parse error is a `GuardError` with pglast's message, so the model can fix it.
  - Each message tells the model what to do (e.g. "qualify table names with their schema:
    mart.contract").
- `open_pool(dsn) -> AsyncConnectionPool`:
  - `min_size=1`, `max_size=4`. Four covers the parallel `run_sql` calls of one turn.
  - It is opened explicitly, and connections are reset with `DISCARD ALL` when they go back to the
    pool.
  - The DSN is never logged.
- `SqlRunner(pool, allowed_schemas, timeout_ms, row_cap)`:
  - `async check_privileges()` raises `StartupError` if `current_user`:
    - is a superuser;
    - has `CREATEROLE`;
    - has `INSERT`, `UPDATE` or `DELETE` on any table in the allowed schemas;
    - or if any allowed schema has no tables.
  - `async run(sql) -> SqlOk | SqlFail`. Exceptions never escape it.
    1. Run `guard`. A `GuardError` becomes `SqlFail(sqlstate=None, message, hint=None)`.
    2. Take a connection. `BEGIN READ ONLY`, then
       `SET LOCAL statement_timeout = <timeout_ms>`, then `execute(sql, prepare=True)`.
       The rows are **streamed** (`cursor.stream`, which uses the extended protocol, in chunks of
       500), and reading stops at `row_cap + 1` rows. Stopping early cancels the rest of the
       query, so the agent never holds more than about `row_cap` rows plus one chunk. Then roll
       back.
    3. `SqlOk(columns, column_types, rows, truncated, elapsed_ms)`:
       - `column_types` are Postgres type names resolved from the description OIDs with
         `conn.adapters.types.get(oid).regtype` (`bigint`, `numeric`, `date`,
         `timestamp with time zone`, …). If the OID is unknown, the name falls back to
         `Column.type_display`. `results.py` already accepts both spellings;
       - `rows` are dicts already passed through `to_json_safe`;
       - `elapsed_ms` covers execute plus fetch.
    4. `psycopg.Error` → `SqlFail(sqlstate, message, hint)`, taken from the diagnostic.
  - A `guard_enabled=True` keyword exists for the tests that send writes past the guard.

### `schema.py`
- `SchemaCatalog(pool, allowed_schemas)` reads the catalog through the same read-only path:
  - `async objects() -> list[TableInfo]`:
    - covers every table, view and materialized view in the allowed schemas;
    - sorted by `(schema, name)`;
    - each object has its comment and its columns in `attnum` order (name, `format_type`
      type, nullable, comment);
    - `TableInfo.est_rows` comes from `reltuples`.
  - `async describe(qualified_name) -> TableInfo | None`.
- `render_schema_section(objects) -> str` is deterministic Markdown:

  ```
  ### mart.contract (table)
  <table comment>
  - notice_id text not null: <column comment>
  ```

  - Objects are separated by one blank line.
  - A missing comment renders as nothing, never as `None`.
  - **It does not include `est_rows`.** Row estimates change after every rebuild and `ANALYZE`,
    and the prefix must stay byte-identical for prompt caching.

### `tools.py`
- `TOOL_SCHEMAS`: the JSON schemas, in OpenAI function format, for `list_tables()`,
  `describe_table(name)` and `run_sql(sql, purpose)`. Their descriptions are short and generic,
  with nothing about a specific dataset.
- `ToolBox(runner, catalog, store, rows_to_model)`:
  - `async call(name, args) -> dict` returns the JSON results described in parent §3.
  - An unknown tool or bad arguments → `{"error": {...}}`, never an exception.
  - `run_sql` stores a successful result in the conversation's `ResultStore` and returns
    `result_id` and `rows_preview`.
    - `rows_preview` holds the first `rows_to_model` rows, with text over 500 characters cut and
      marked `…`.
    - Failures are not stored.
  - `describe_table` with an unknown or disallowed name → an error listing up to 5 close matches
    (`difflib`) from the allowed objects.

### Test database (`tests/conftest.py`)
- `pg` (session):
  - starts `PostgresContainer("postgres:18", driver=None)`, imported from
    `testcontainers.community.postgres` (the old path is deprecated);
  - calls `austender.testing.build_test_db(superuser_dsn)`;
  - yields the `explorer_ro` DSN (password `explorer_ro`) and the loader DSN.
  - If Docker is unavailable (`docker.errors.DockerException`), it calls `pytest.fail` with a
    clear message. It never skips.
- `ro_pool` (function scope) builds an `open_pool` on the `explorer_ro` DSN.
- Database tests are marked `db`. Expected numbers come from `austender/tests/seed/expected.py`
  and are imported, not copied.

## Criteria → tests

Run with `cd explorer/agent && uv run pytest`.

| # | Criterion | Test |
|---|---|---|
| D1 | Defaults per parent §7; `REASONING_EFFORT=""` → `None`; bad `FORMAT`, missing `DATABASE_URL`, non-positive ints → `ConfigError` | `test_config.py` (parametrized) |
| D2 | Missing `dataset.toml` / notes / glossary, empty or invalid `allowed_schemas` → `ConfigError` naming the file or key; no env value appears in any message | `test_config.py` |
| D3 | The committed `datasets/austender` loads: name `austender`, schemas `("mart",)`, currency `AUD`/`$` | `test_config.py::test_committed_dataset_loads` |
| D4 | Guard accepts plain `SELECT`, `WITH`, CTE names, unqualified aggregate functions, schema-qualified `mart.*` | `test_guard.py` (parametrized) |
| D5 | Guard rejects every case in parent §10 "SQL guard", each with a message that says what to fix | `test_guard.py` (parametrized) |
| D6 | Runner on the seed: a valid query returns typed columns and JSON-safe rows; `row_cap=2` → 2 rows, `truncated` | `test_sql.py` (db) |
| D7 | With the guard disabled: `DELETE`/`UPDATE`/`CREATE` → `SqlFail` with SQLSTATE 25006 or 42501 | `test_sql.py` (db) |
| D8 | `pg_sleep(1)` with `timeout_ms=100` and the guard disabled → SQLSTATE 57014; the next query on the pool still works | `test_sql.py` (db) |
| D9 | `SELECT 1; SELECT 2` with the guard disabled fails (extended protocol) | `test_sql.py` (db) |
| D10 | Session state that survives `ROLLBACK` (a session advisory lock) is gone on every pooled connection by the next run (pool reset); the test fails without `DISCARD ALL` | `test_sql.py` (db) |
| D11 | `check_privileges` passes for `explorer_ro` and raises `StartupError` for the loader | `test_sql.py` (db) |
| D12 | Schema section lists every `mart` table with its comment and columns, sorted; nothing from `raw`/`core`/`ops`; no row counts | `test_schema.py` (db) |
| D13 | Schema section is identical after a `rebuild` plus `ANALYZE` on the seed (proves it doesn't depend on data) | `test_schema.py` (db) |
| D14 | `list_tables` matches the schema; `describe_table("mart.contract")` has live columns; `describe_table("mart.contrat")` → error with `mart.contract` among the matches; `describe_table("raw.release")` → error | `test_tools.py` (db) |
| D15 | `run_sql` stores the result (`r1`, then `r2`), returns `rows_preview` capped at `rows_to_model`, long text cut; a failing query returns `{"error": {sqlstate, message, hint}}` and stores nothing | `test_tools.py` (db) |
| D16 | The acceptance query (FY2024-25 top 10 suppliers and their agency split, as in `austender/tests/test_seed_expected.py`) through `run_sql` gives the totals in `expected.py` | `test_tools.py` (db) |
| D17 | Unknown tool name or missing argument → error dict, no exception | `test_tools.py` |
| D18 | Two result columns with the same name → `SqlFail` asking for unique aliases (rows are dicts, so one would silently disappear) | `test_sql.py::test_duplicate_column_names_are_rejected` |
| D19 | Functions that run SQL from a string (`ts_stat`, the `*_to_xml` family) and session or monitoring functions (`pg_try_advisory*`, `pg_stat_*`) are on the deny list; a NUL byte is rejected | `test_guard.py::test_denied_functions`, `::test_nul_byte_is_rejected` |
| D20 | A CTE name exempts an unqualified relation only in the query level that sees it: its own query and nested queries, and for a non-recursive WITH, later sibling CTEs only | `test_guard.py::test_cte_name_is_scoped`, `::test_cte_scoping_still_allows_visible_ctes` |
| D21 | The row cap bounds the fetch: 20M rows with `row_cap=2` return 2 rows in under 3 s, and the connection is reusable | `test_sql.py::test_row_cap_bounds_the_fetch` |
| D22 | A malformed `DATABASE_URL` is rejected by `load_settings` and `open_pool` (libpq parse) with a fixed message, so `psycopg.pool` never logs it | `test_config.py::test_malformed_database_url_is_a_config_error`, `test_sql.py::test_malformed_dsn_is_rejected_without_the_secret` |
| D23 | A query nested too deeply for the walker → `GuardError`, never `RecursionError` | `test_guard.py::test_deeply_nested_query_is_a_guard_error` |

**Live check (marked `live_db`, not run by default; local database only, no model, no network):**
`uv run pytest -m live_db`. It reads `DATABASE_URL` from `explorer/.env`.

| # | Check |
|---|---|
| L1 | `check_privileges` passes against the loaded AusTender database |
| L2 | The schema section renders every `mart` table and is under 12k characters |
| L3 | The acceptance query through `SqlRunner` on the full backfill: report the median `elapsed_ms` of 5 runs. Target: under 500 ms (`austender/SPEC.md` §1). Only a miss **over 2 s** fails the test, so a slow laptop doesn't fail it; a miss between 500 ms and 2 s is reported, then an index or summary table gets added in `austender/` |

## Decisions

- **Scope:** step 2 only. Nothing calls the model. `prompt.py`, `llm.py`, `__main__` and connecting
  the currency to `UiPipeline` are step 3, and the parent spec already covers them.
- **No row counts in the prompt.** `est_rows` is only in `list_tables`' result, never in the
  cached prefix (D13).
- **`load_settings` is pure:** loading `.env` belongs to the entry point. That way tests can't pick
  up real secrets or a real DSN.
- **Test DB once per session:** one container, built once by `build_test_db`. Tests only read it,
  except D13, which runs `rebuild` and leaves the same data behind (the austender rebuild is
  idempotent).
- **Pool size 4,** fixed, with no setting until a need appears.
- **Deferred (refuter MINOR, 2026-11-01):**
  - Partitions are hidden from the schema section but can still be queried. `mart` has none.
  - `check_privileges` doesn't check `TRUNCATE`, schema `CREATE` or `pg_read_server_files`
    membership. The parent spec doesn't ask for these.

- **Prompt files:** I draft `notes.md` and `glossary.md` during the build. The user reviews them
  in the diff (user decision, 2026-11-01).
- **L3 threshold:** missing the 500 ms target is reported, and the test fails only above 2 s
  (user decision, 2026-11-01).
- **Amended during the build:**
  - `config.py` uses frozen dataclasses with explicit checks instead of pydantic, because
    pydantic isn't a direct dependency and would be one only for this. Parent §8 says pydantic,
    but the behaviour is the same.
  - A schema-qualified function must be in `allowed_schemas`, so `pg_catalog.sum()` is rejected
    as well (parent §9.2 says this literally). Unqualified functions are allowed.
  - Deny list additions: `pg_reload_conf`, `ts_stat`, the `*_to_xml` family, and the prefixes
    `pg_try_advisory` and `pg_stat_` (D19).
  - The parent §9.2 rule "a relation is a CTE name or schema-qualified" is applied **with
    scope** (D20). A global set of CTE names let `pg_catalog` relations through (refuter,
    2026-11-01).
  - The fetch is incremental (D21). The parent's "fetch `SQL_ROW_CAP + 1` rows" on a
    client-side cursor buffered the whole result first.
  - Tool calls with `None` or `""` arguments are treated as `{}`. Preview cutting applies
    inside json objects and arrays.
  - Duplicate result column names are rejected (D18).
  - The tool schemas' examples are generic (`'top 10 by value, 2024'`). A test checks that no
    dataset words appear in them.
- **Guard implementation:** `pglast.parse_sql` plus a `pglast.visitors.Visitor` over every
  node. `FuncCall.funcname` is a tuple of `String` nodes: the last is the name, and the one before
  it, if present, is the schema. Functions in `FROM` (`RangeFunction`) are visited too.
