# Dataset bootstrap: spec

Status: phase 1 and phase 2a **approved** (2026-11-02). The
rest of phase 2 is not yet specified; until it is, `docs/bootstrap-build-prompt.md` describes it. Related ADRs: 0019 (Comment overlay), 0020 (Muse
Wonder only here), 0010 (tests build their database with the austender package). Terms follow
`CONTEXT.md` (Dataset, Data dictionary, Comment overlay, Allowed schema, Dataset context).

## Phase 1: the report

A deterministic command-line report on a Postgres database: which tables and columns lack a
description, what the data looks like according to the planner statistics, and how large the
Dataset context would be. No model is called. The report also writes JSON, which is the
contract that phase 2 (the draft step and the UI) reads.

### Files

```
explorer/bootstrap/
  SPEC.md                       this file
  server/
    pyproject.toml              package explorer-bootstrap; path dep on ../../agent (explorer-agent)
    uv.lock                     committed
    README.md                   setup, CLI usage, the read-only login recommendation
    src/explorer_bootstrap/
      __init__.py
      __main__.py               CLI: `report` subcommand, env loading, exit codes
      report.py                 build_report(), Report dataclasses, to_dict(), render_text()
      stats.py                  catalog/pg_stats/ownership queries and min/max via SqlRunner
    tests/
      conftest.py               disposable Postgres 18 built by austender (same pattern as the agent)
      test_report_unit.py       pure helpers (no DB)
      test_report_db.py         against the seeded database
      test_cli.py               CLI behaviour, secrets, exit codes
explorer/.env.example           gains BOOTSTRAP_DATABASE_URL (placeholder)
```

Reused from `explorer_agent` (not copied): `schema.SchemaCatalog` (objects, comments, row
estimates), `schema.render_schema_section`, `prompt.dataset_context`, `config.load_dataset`,
`config.SCHEMA_NAME`, `sql.open_pool`, `sql.check_dsn`, `sql.SqlRunner` (read-only, timeout,
guard), `results.to_json_safe`. No change to `explorer_agent` is needed in phase 1.

### Behaviour

```
uv run python -m explorer_bootstrap report --dataset-dir PATH [--json OUT] [--no-minmax] [--timeout-ms N] [--check]
uv run python -m explorer_bootstrap report --schemas a,b      [--json OUT] [--no-minmax] [--timeout-ms N] [--check]
```

- Exactly one of `--dataset-dir` or `--schemas`. `--dataset-dir` loads the Dataset with
  `load_dataset` and reports on its Allowed schemas; the context size is then exact
  (`context_scope = "full"`). `--schemas` takes a comma-separated list of schema names, each
  matching `SCHEMA_NAME`; the context size is the schema section alone
  (`context_scope = "schema_only"`), and the terminal output says so.
- Connection: `BOOTSTRAP_DATABASE_URL` from the environment, after loading the repo root `.env`
  (`genUI/.env`, as the agent does; `BOOTSTRAP_ENV_FILE` overrides the path; existing env wins).
  Missing → exit 2 with a message naming the variable. Malformed → `check_dsn`'s message.
- Every query runs in a `READ ONLY` transaction with `statement_timeout` (`--timeout-ms`,
  default 5000). Catalog and statistics queries are ours; min/max goes through `SqlRunner` with
  the guard on and the chosen schemas as the allowlist.
- For each table, view, materialized view and foreign table in the chosen schemas (same set and
  order as `SchemaCatalog.objects()`):
  - name, kind, comment, `est_rows` (null when never analysed or a view),
  - `analysed`: true if the table has planner statistics (rows in `pg_stats`, or
    `reltuples >= 0`), false if neither, null for views (no statistics apply);
  - `can_comment`: whether the login can run `COMMENT ON` for it (it has the privileges of the
    owner role, which includes superusers);
  - each column: name, type, nullable, comment, and from `pg_stats` (when present) `null_frac`,
    `distinct_estimate` (an integer; a negative `n_distinct` is a fraction of `est_rows`), and
    `most_common` (up to 5 `{value, freq}`, values as text). Statistics absent → those fields
    are null, never an error.
  - min and max for numeric and date/time columns (`smallint`, `integer`, `bigint`, `numeric…`,
    `real`, `double precision`, `date`, `timestamp…`), one query per object. `range_status` is
    `ok`, `skipped` (statement timeout), `error` (any other failure, message kept), `n/a`
    (not a range type), or `off` (`--no-minmax`).
- Summary: object and column counts, how many of each have a comment, coverage percentage
  (comments ÷ (objects + columns), one decimal), `can_comment` overall (true only if true for
  every object), `context_chars`, `context_target` (20000), `context_scope`, and the schemas
  that have no visible objects (listed, not fatal unless all are empty → exit 2).
- `context_chars` is `len(dataset_context(dataset, render_schema_section(objects)))` for
  `--dataset-dir`, else `len(render_schema_section(objects))`, with `objects` straight from
  `SchemaCatalog` — the same calls the agent makes at startup.
- Terminal output: connection (host and database name only; `localhost` when the DSN names no
  host), the summary, then each object and column that has no comment, then never-analysed
  tables, then any `skipped`/`error` ranges.
- `--json OUT` writes the full report (`to_dict()`), UTF-8, sorted keys, indent 2, with
  `"report_version": 1`. It is strict JSON: NaN and ±Infinity min/max values are `null`.
  Numeric values are JSON numbers, so a high-precision `numeric` can lose digits (accepted).
  The DSN, user name and password never appear in it.
- Exit codes: 0 success; 2 usage, config, connection or runtime error (a catalog query that
  times out, any other database error, an unwritable `--json` path), always as a one-line
  message without the DSN; with `--check`, 1 if any object or column lacks a comment or
  `context_chars > context_target`. A crash never exits 1.

### Criteria → tests

| # | Criterion | Test |
|---|---|---|
| 1 | Every uncommented object and column is listed; commented ones aren't | `test_report_db.py::test_finds_every_missing_comment` (fixture schema with a mix) |
| 2 | AusTender `mart` has 100% coverage | `test_report_db.py::test_austender_full_coverage` |
| 3 | Context size equals the agent's own computation | `test_report_db.py::test_context_size_matches_agent` (`--dataset-dir` on the committed AusTender dataset) and `::test_schema_only_context` |
| 4 | Never-analysed table: `analysed` false, stats null, no error; views get null | `test_report_db.py::test_unanalysed_table_and_view` |
| 5 | pg_stats fields: null_frac, distinct estimate (incl. negative n_distinct), most common values | `test_report_db.py::test_column_statistics` and `test_report_unit.py::test_distinct_estimate` |
| 6 | Min/max correct; a slow object is `skipped`, not a failure | `test_report_db.py::test_min_max` and `::test_slow_range_is_skipped` |
| 7 | `can_comment` false for `explorer_ro`, true for the owner | `test_report_db.py::test_can_comment_by_login` |
| 8 | Every connection the report uses is read-only with a `statement_timeout`, even for a login that isn't read-only by default | `test_report_db.py::test_pool_is_read_only_with_timeout` (the owner login: a write fails with SQLSTATE 25006; `SHOW statement_timeout` is the configured value) |
| 9 | JSON is strict, sorted, indent 2, and contains no secrets; non-finite values are null | `test_cli.py::test_json_output`, `test_report_db.py::test_non_finite_values_become_null` |
| 9a | Runtime errors exit 2 with a message, never a traceback | `test_cli.py::test_runtime_database_error_exits_2`, `::test_unwritable_json_exits_2` |
| 10 | Terminal output and errors never show the password or user | `test_cli.py::test_no_secrets_in_output`, `::test_bad_url_message` |
| 11 | Usage errors: both/neither selector, bad schema name, missing URL, all schemas empty → exit 2 | `test_cli.py::test_usage_errors` |
| 12 | `--check` exits 1 on a missing comment or oversize context, 0 otherwise | `test_cli.py::test_check_exit_codes` |
| 13 | Type classification for min/max | `test_report_unit.py::test_range_types` |

### Decisions

- The report never writes: no DDL, no `ANALYZE` (it reports missing statistics instead).
- The report opens its own pool (`stats.open_report_pool`, which calls `check_dsn`) instead of
  `sql.open_pool`, so it can pass `-c default_transaction_read_only=on -c statement_timeout=N`
  as connection options. That covers `SchemaCatalog`'s queries, which set `READ ONLY` but no
  timeout, without changing `explorer_agent`. Our own queries still use `BEGIN READ ONLY`.
- No superuser/`CREATEROLE` refusal in the CLI (unlike the agent's `check_privileges`); the
  README recommends the read-only login, and every query is read-only regardless.
- Min/max is per object, so one slow column skips its object's ranges, and one column the
  login can't read makes the object's ranges `error`. Accepted for speed; documented in the
  README.
- `--timeout-ms` bounds every query, including catalog and `pg_stats` reads. If those time out,
  the run fails with exit 2 and asks for a larger timeout.
- Coverage counts objects and columns together; views count like tables.
- Comment source per object (database vs overlay) is phase 2, when the overlay exists (ADR 0019);
  see "Phase 2a" below.
- The JSON has a version field but no JSON Schema file; the dataclasses are the schema.
- Not in phase 1: the server, the UI, the model, export.

### Test command

```bash
cd explorer/bootstrap/server
uv sync
uv run pytest            # needs Docker (Testcontainers Postgres 18); no network, no model
```

## Phase 2a: the Comment overlay in the report

Status: **approved** (2026-11-02). The explorer now reads a Comment overlay
(`explorer/SPEC.md` §13, ADR 0019). The report has to apply it the same way, or its context
size and coverage are wrong for a Dataset with one, and it has to show where each comment came
from.

### Files

- `src/explorer_bootstrap/report.py`: apply the overlay; source fields; summary counts; text.
- `src/explorer_bootstrap/__main__.py`: an overlay drift error exits 2.
- `tests/test_report_db.py`, `tests/test_cli.py`, `tests/test_report_unit.py`.
- `README.md`: one paragraph.

### Behaviour

- With `--dataset-dir` and a Dataset whose `dataset.toml` has `[comments] overlay`, the report
  reads the database's comments with `SchemaCatalog(pool, schemas)` (no overlay), checks them with
  `explorer_agent.schema.overlay_problems`, and applies `apply_overlay`: the same functions the
  agent uses, so the figures can't drift. Coverage, missing comments, `--check` and
  `context_chars` all use the overlaid comments. With `--schemas`, or a Dataset without an overlay,
  nothing changes.
- Each object and column gains:
  - `comment_source`: `"overlay"` if the comment comes from the overlay, `"database"` if from the
    database, `null` if there is none;
  - `database_comment`: the database's comment when the overlay replaced one, else `null`.
- `Summary` gains `overlay_comments` (comments from the overlay) and `overlay_replacements` (of
  those, how many replace a database comment). Both are 0 without an overlay. `Report` gains
  `comment_overlay`: the overlay's file name (no directories, so no local paths), or `null`.
- Terminal output: after the coverage line, when there's an overlay,
  `Comment overlay comments.toml: N comments (M replace a database comment)`.
- An overlay entry for a missing table or column: exit 2 with
  `comment overlay <path>: unknown …` (the agent's wording), never a traceback.
- `report_version` stays 1: phase 1 has not been committed, so no reader depends on version 1.

### Criteria → tests

| # | Criterion | Test |
|---|---|---|
| B1 | Sources: overlay replaces a database comment (`database_comment` kept), overlay fills a missing comment, untouched comments are `database`, still-missing are `null`; coverage and missing list use the overlaid comments; summary counts | `test_report_db.py::test_overlay_sources` |
| B2 | Context size with an overlay equals the agent's own computation | `test_report_db.py::test_context_size_matches_agent_with_overlay` |
| B3 | Without an overlay every comment is `database` or `null`, counts are 0, `comment_overlay` null | `test_report_db.py::test_austender_full_coverage` (extended) |
| B4 | Overlay drift → exit 2, one line, names every unknown entry | `test_cli.py::test_overlay_drift_exits_2` |
| B5 | Text output shows the overlay line only when there is one | `test_cli.py::test_overlay_text_line` |

### Decisions

- The agent's dataclasses don't record comment sources (explorer SPEC §13); the report compares
  the raw catalog with the overlaid one itself.
- `database_comment` only when replaced, so the JSON doesn't repeat every comment twice. An
  overlay comment identical to the database's still counts as a replacement: the overlay names it.
- No visible objects at all: the drift check is skipped, so the CLI reports "no visible objects"
  rather than every overlay entry as unknown.
- The "Login can add comments: no" hint says "use a Comment overlay" without a file name.

### Test command

Same as phase 1.
