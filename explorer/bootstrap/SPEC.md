# Dataset bootstrap: spec

Status: phase 1, 2a, 2b and 2c **approved** (2026-11-02); phase 3 (Boards) **approved** (2026-11-04). Still open from the brief: the live
run against `vicprop/`. Related ADRs: 0019 (Comment overlay), 0020 (Muse
Wonder only here), 0010 (tests build their database with the austender package). Its UI moves into
the explorer's Settings (`../specs/workspaces/SPEC.md` phase 2, ADR 0041, approved 2026-11-04). Terms follow
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

## Phase 2b: draft, claims, validation and export (library + CLI)

Status: **approved** (2026-11-02; decided by the builder on the user's delegation). Related:
ADR 0020 (Wonder only here), ADR 0023 (the draft arrives as a tool call). Phase 2c (HTTP server
and Lit UI) wraps these functions; nothing in 2b depends on it. The CLI flow below is enough for
the live run against `vicprop/` before the UI exists.

### Files

```
server/src/explorer_bootstrap/
  settings.py      BOOTSTRAP_* settings, the model allowlist, prices from prices.toml
  prices.toml      per-model USD per 1M tokens: input, cached_input, output
  draft_schema.py  the submit_draft tool schema and validate_draft_args()
  prompts/draft.md the instructions (generic: nothing about any one dataset)
  draft.py         build_draft_prompt(), run_draft() (the tool loop), Usage and cost
  claims.py        verify_claim(), verify_claims(), export_blockers()
  drafts.py        Draft dataclasses, to/from JSON, validate_files(), context size
  export.py        export_dataset(), render_comments_toml(), render_comments_sql()
  __main__.py      `draft`, `verify`, `export` subcommands
server/tests/      test_draft.py, test_claims.py, test_drafts.py, test_export.py, test_cli.py (extended)
```

### Settings (env, after the repo root `.env`)

| Variable | Default | Meaning |
|---|---|---|
| `BOOTSTRAP_MODEL` | `meta/muse-wonder-1.0` | Only `meta/muse-wonder-1.0` or `meta/muse-spark-1.3`; anything else is a usage error (exit 2) |
| `BOOTSTRAP_REASONING_EFFORT` | `low` | Sent as `reasoning_effort`: `minimal`, `low`, `medium`, `high`, `xhigh` or `max`; `none` is rejected (Wonder doesn't accept it) |
| `BOOTSTRAP_MAX_TOOL_ITERATIONS` | `12` | Model calls per draft, including the submit |
| `BOOTSTRAP_MAX_TOKENS` | `32000` | `max_tokens` per call |
| `BOOTSTRAP_CALL_TIMEOUT_S` | `300` | Per model call (`asyncio.timeout`) |
| `BOOTSTRAP_SQL_ROW_CAP` | `200` | Rows fetched per `run_sql` / claim query |
| `BOOTSTRAP_ROWS_TO_MODEL` | `20` | Rows shown to the model per `run_sql` |

The CLI's `--model` and `--effort` override the first two. Prices live in `prices.toml`, not code;
a test checks them against LiteLLM's pinned cost map (Wonder $4.00 / $0.20 / $20.00, Spark
$1.25 / $0.15 / $4.25 per 1M input / cached input / output).

### The draft (model loop)

- **Prompt**, built once per draft, stable prefix first: `prompts/draft.md` (instructions and the
  output rules), then the report JSON (compact: sorted keys, no spaces). The user message is a
  fixed sentence. No dates, IDs or paths. `prompt_cache_key = "bootstrap-<dataset or schemas>"`.
  Same report → byte-identical prompt (test).
- **Tools:** `run_sql(sql, purpose)` and `submit_draft(...)`. `run_sql` goes through
  `SqlRunner` (guard on, the report's schemas as the allowlist, read-only, timeout, row cap) and
  returns `{columns, rows (first ROWS_TO_MODEL, JSON-safe), row_count, truncated}` or
  `{error: {sqlstate, message}}`. Never `list_tables`/`describe_table`: the report has it all.
- **Loop:** call with both tools (no `tool_choice`, no `response_format`). Tool calls in one
  reply run in parallel. A valid `submit_draft` ends the loop; if a reply also has `run_sql`
  calls, they're ignored. An invalid one gets a tool error listing the problems (at most 20) and
  the loop goes on. A reply with no tool calls gets one fixed nudge ("Call submit_draft with the
  draft."). The cap counts every model call; reaching it without a valid draft is a
  `DraftError`. A model exception is a `DraftError` with the exception's class name and its
  message stripped of anything that looks like a key (never the env).
- **Events:** `run_draft` takes an `on_event` callback: `{"type": "sql", "purpose", "sql"}`,
  `{"type": "call", "usage": {...}}`, `{"type": "invalid_draft", "problems"}`. The CLI prints
  them; phase 2c streams them.
- **Usage and cost**, per call and totals: prompt, cached, completion, reasoning tokens,
  latency, cost = (prompt − cached) × input + cached × cached_input + completion × output.
  Reasoning tokens are assumed to be inside `completion_tokens` (OpenAI convention;
  UNVERIFIED for Meta, noted in the README). Logged per call, without message content.

### `submit_draft` arguments (JSON schema; all keys required, `additionalProperties: false`)

```
files:    {dataset_toml: str, notes_md: str, glossary_md: str}
comments: [{object: "schema.table", column: str ("" = the object itself), comment: str}]
claims:   [{text: str, file: enum(dataset.toml, notes.md, glossary.md, comments), sql: str,
            expect: anyOf(
              {kind: enum(scalar), value: number, abs_tol: number, rel_tol: number},
              {kind: enum(set), values: [str]},
              {kind: enum(none)})}]
```

Depth 5, no `oneOf`, no recursion. `validate_draft_args` checks the shape, plus: every comment
names an object (and column) in the report; no duplicate (object, column); comments are
non-blank; tolerances ≥ 0; `set` values are unique; `dataset_toml` has no `[comments]` table
(export adds it) and proposes starter `examples` (the prompt asks for three; the model is told
again in the tool loop when it forgets). Problems are short strings naming the field path.

A comment for something that has a comment in the report is a **rewrite**; anything else is
**new**. The draft records `kind` (`new`/`rewrite`) and `accepted` (new: true, rewrite: false), so
rewrites are never applied silently.

### Claims

`verify_claim` runs the claim's SQL through `SqlRunner` (same rules as `run_sql`) and returns a
state, the actual result (first 20 rows, JSON-safe) and a message:

| Expectation | verified | failed | error |
|---|---|---|---|
| `scalar` | 1 row × 1 column, a number (not bool), `abs(actual − value) ≤ max(abs_tol, rel_tol × abs(value))` | the number is outside | SQL fails, wrong shape, NULL or not a number |
| `set` | 1 column, not truncated, set of values (as text: strings as is, others via JSON) equals `values` | sets differ (message lists missing and extra, up to 10 each) | SQL fails, more than one column, truncated |
| `none` | never | never | SQL given and it fails |

`none` is otherwise `needs_review` (SQL blank → not run). Never `verified` without a passing
comparison. Every claim is verified, also after a failure in an earlier one. `export_blockers`
lists claims whose state is `failed` or `error`, or that were never checked, and that aren't
`dismissed`.

### The draft file (`draft.json`)

`{"draft_version": 1, "model", "effort", "report": <report JSON>, "files": {...},
"comments": [{object, column, comment, kind, accepted}], "claims": [{text, file, sql, expect,
dismissed: false, result: {state, actual, message}}], "usage": {calls: [...], totals}}`.
Strict JSON, sorted keys, indent 2, no secrets or local paths. The person may edit it by hand
(files, comment text, `accepted`, claim SQL/expectation, `dismissed`) and then run `verify`.

### Validation (`validate_files(draft)`)

Writes the three files to a temporary directory and runs `load_dataset`; returns `errors` (the
`ConfigError` text, with the temp path replaced by `<dataset>`) and, when valid,
`context_chars`: the Dataset context the explorer would build with the **accepted comments
applied** (report objects → `TableInfo`, accepted comments → `CommentOverlay`, `apply_overlay`,
`render_schema_section`, `dataset_context`). Also an error: `allowed_schemas` not a subset of the
report's schemas; `[comments]` present; an accepted comment (possibly edited by hand) that
names no reported object or column, repeats one, or is blank; `notes` or `glossary` not exactly `"notes.md"` /
`"glossary.md"` (the names export writes); no starter `examples` (the person adds them in the
`dataset.toml` tab before exporting).

### Export (`export_dataset(draft, name, target, root, *, force=False)`)

- Refuses (an `ExportError` listing every reason) when: `name` doesn't match `[a-z][a-z0-9_]*`;
  `root/name` exists (without `force`) or is a file (with it); `validate_files` has errors;
  `export_blockers` isn't empty; `target` isn't `toml` or `sql`.
- With `force`, an existing directory is kept and only the draft's four files (`dataset.toml`,
  `notes.md`, `glossary.md`, plus `comments.toml` or `comments.sql`) are replaced, atomically
  per file; anything else there (scenarios, a previous comments file for the other target)
  is left alone. Replaced files are copied to a dot-prefixed sibling backup
  (`.<name>.bak-<UTC timestamp>`, which the dataset picker ignores); the result reports
  what was replaced and where the backup is.
- Writes `dataset.toml` (with `notes = "notes.md"`, `glossary = "glossary.md"` as drafted),
  `notes.md`, `glossary.md`, and the accepted comments:
  - `toml`: `comments.toml` in the overlay format (entries sorted by object, columns in report
    column order; TOML strings escaped), and appends `[comments]\noverlay = "comments.toml"\n` to
    `dataset.toml`.
  - `sql`: `comments.sql`, one `COMMENT ON {TABLE|VIEW|MATERIALIZED VIEW|FOREIGN TABLE} … IS …;`
    per object (kind from the report) and `COMMENT ON COLUMN … IS …;` per column, identifiers
    double-quoted, literals single-quoted with `'` doubled, wrapped in `BEGIN;`/`COMMIT;`. The
    tool never runs it.
- Writes into a temporary sibling directory, then renames it to `root/name`, so a failure
  leaves nothing behind. Never commits, never runs DDL.

### CLI

```
uv run python -m explorer_bootstrap draft  (--dataset-dir P | --schemas a,b) --out draft.json [--model M] [--effort E]
uv run python -m explorer_bootstrap verify --draft draft.json          # reruns every claim, rewrites the file
uv run python -m explorer_bootstrap export --draft draft.json --name N --target toml|sql [--root DIR]
```

`draft` builds the report (same code as `report`, min/max on), runs the loop, verifies every
claim, validates, writes `draft.json`, and prints progress, the claims table, validation and
the usage/cost totals. `--root` defaults to `explorer/datasets`. Exit codes: 0 ok; 1 `export`
refused, or `verify` finished with blockers; 2 usage, config, connection, model or runtime
error. Messages never show the DSN or keys.

### Criteria → tests

| # | Criterion | Test |
|---|---|---|
| D1 | Loop: `run_sql` → result → `submit_draft` ends it; kwargs carry model, both tools, `prompt_cache_key`, `reasoning_effort`, `max_tokens`, no `response_format`/`tool_choice` | `test_draft.py::test_loop_happy_path` |
| D2 | The model's SQL that writes or reads an unchosen schema is refused by `guard` and returned as a tool error; the loop continues | `test_draft.py::test_guard_refuses_model_sql` (db) |
| D3 | Invalid `submit_draft` → tool error with the problems, then a valid one succeeds; no tool calls → nudge; cap → `DraftError`; model exception → `DraftError` without secrets | `test_draft.py::test_invalid_then_valid`, `::test_nudge`, `::test_cap`, `::test_model_error` |
| D4 | Prompt byte-identical for the same report; no per-run values | `test_draft.py::test_prompt_is_stable` |
| D5 | Usage and cost totals from stub usage; prices match LiteLLM's map | `test_draft.py::test_usage_and_cost`, `::test_prices_match_litellm` |
| D6 | `validate_draft_args`: each rule above, including starter `examples` in `dataset.toml` | `test_drafts.py::test_validate_draft_args` (parametrised) |
| D8 | The draft proposes three starter `examples`: the prompt asks for them in the `dataset.toml` shape; missing examples are a `validate_files` error (export blocked until the person adds them); export carries them into the dataset | `test_drafts.py::test_validate_files_requires_starter_examples`, `test_export.py::test_export_carries_examples` |
| D7 | rewrite vs new, `accepted` defaults | `test_drafts.py::test_comment_kinds` |
| C1 | Claim states per the table, incl. NaN/NULL scalar, bool, truncated set, failing SQL in a `none` claim, and a failing claim not stopping the rest | `test_claims.py` (db, parametrised) |
| C2 | Blockers: failed/error block unless dismissed; needs_review doesn't | `test_claims.py::test_export_blockers` |
| V1 | `validate_files`: a valid draft gives no errors and the exact context size (equals the agent's computation over a Dataset dir exported with the overlay); bad TOML, missing key, `[comments]`, unchosen schema → errors without temp paths | `test_drafts.py::test_validate_files` (db) |
| E1 | Export refuses: bad name, existing dir, invalid files, blockers; nothing is written | `test_export.py::test_export_refusals` |
| E2 | The same draft exports to both targets with the same comments: `comments.toml` loads as an overlay, `comments.sql` applied as the owner gives the same comments in the database | `test_export.py::test_both_targets_same_comments` (db) |
| E3 | The overlay export works through the read-only role: the explorer starts with `DATASET_DIR=<export>` (subprocess, as `test_main` does) | `test_export.py::test_explorer_starts_with_overlay_export` (db) |
| E4 | Quoting: quotes, backslashes, newlines, DEL, non-BMP characters and odd identifiers round-trip through `comments.sql` (applied in the database) and `comments.toml` (`load_overlay`) | `test_export.py::test_sql_and_toml_quoting`, `::test_overlay_file_loads_with_tricky_text` (db) |
| E5 | `export --force` into an existing directory replaces only the draft's files (others untouched), keeps replaced files in a dot-prefixed sibling backup the picker ignores, and still refuses a file; without `force` the existing directory still refuses; the API reports `overwrote` and `backup` | `test_export.py::test_export_force_replaces_only_the_drafts_files`, `::test_export_force_into_an_empty_dir_keeps_no_backup`, `::test_export_force_still_refuses_a_file`, `test_server.py::test_export_force` (db) |
| L1 | CLI `draft` with a stub model writes a strict, secret-free `draft.json`; `verify` and `export` exit codes | `test_cli.py::test_draft_verify_export` (db; the stub is injected through a module-level `COMPLETION` hook, never the network) |
| L2 | Model allowlist and settings parsing | `test_cli.py::test_model_allowlist`, `test_draft.py::test_settings` |

### Decisions

- ADR 0023: the draft comes back as a `submit_draft` tool call, validated by us.
- The draft is one model loop; there's no separate step per file.
- No pre-run cost estimate in 2b (it's a UI feature in 2c); actual cost is logged.
- Existing notes and glossary (with `--dataset-dir`) are not sent to the model; the report is.
- `set` comparisons are on text; the prompt tells the model to cast to text in claim SQL.
- `comments` claims refer to drafted comments; their checking works the same way.
- `draft.json` is the hand-off between CLI steps and later the UI. Not a stable public format.
- A comment is a rewrite when the report shows a comment for it (after any existing overlay). The
  context size and both export targets build on the **database's** comments plus the accepted
  draft comments: an existing overlay of a `--dataset-dir` Dataset is not carried into the new
  directory. Bootstrapping is for new Datasets; redrafting one with an overlay is out of scope.
- Unexpected exceptions: in a tool call they become a tool error (the draft goes on); anywhere
  else in the CLI they exit 2 with the exception's class name only. Exit 1 means only "refused".
- A `run_sql` call in the same reply as `submit_draft` is not run; it gets an "ignored" tool reply
  (every tool call needs a reply).
- Out of 2b: HTTP server, UI, pre-run estimate, the live run.

### Test command

Same as phase 1 (`uv run pytest`; Docker; never the network or a model).

## Phase 2c: local server and Lit UI

Status: **approved** (2026-11-02; decided by the builder on the user's delegation). Wraps the
phase 2b functions; adds the pre-run cost estimate. Brief: `docs/bootstrap-build-prompt.md` §4–5.

### Files

```
server/src/explorer_bootstrap/
  server.py        Starlette app: API routes, in-memory state, static UI
  stats.py         + visible_schemas()
  draft.py         + estimate_cost()
  __main__.py      + `serve [--port 8765] [--timeout-ms N]`
server/tests/test_server.py
server/pyproject.toml   starlette==1.10.0, uvicorn==0.54.0 direct (already locked transitively); httpx (dev)
ui/                Vite + Lit + TypeScript, its own npm package (`explorer-bootstrap-ui`)
  package.json, package-lock.json, tsconfig.json, vite.config.ts, index.html, README.md
  src/main.ts, src/api.ts, src/app.ts (<bootstrap-app>), src/connect.ts, src/report.ts,
  src/review.ts, src/claims.ts, src/types.ts
  test/report.test.ts, test/review.test.ts, test/claims.test.ts, test/app.test.ts
```

UI stack, same as `explorer/client` (pinned exactly, `lit` at the client's resolved 3.3.3): lit
3.3.3, vite 6.4.5, typescript 5.8.3, vitest 3.2.7, happy-dom 20.14.6. Decorators
(`experimentalDecorators`, `useDefineForClassFields: false`), light DOM (`createRenderRoot()`
returns `this`), tests mount with `document.createElement` + `appendChild` + `updateComplete`.
No imports from `explorer/client`, no component library, no router, no code editor.

### Server

`python -m explorer_bootstrap serve` opens the report pool (read-only, timeout) at startup, binds
`127.0.0.1` only, serves the API under `/api` and the built UI (`ui/dist`) at `/` (one origin:
no CORS). In development the Vite dev server proxies `/api` to it.

> **2026-11-05:** `ui/` is retired (Workspaces SPEC G22). Its views are the explorer client's
> Settings (`#/settings`), and `/` now answers with a pointer to it. The `ui/` test commands below
> are historical.

- **Request guard** (a page on another site must not drive the tool): every `/api` request must
  have a `Host` of `127.0.0.1:<port>` or `localhost:<port>` (DNS rebinding), and every `POST`
  must be `Content-Type: application/json` (so a cross-site request needs a CORS preflight,
  which we never answer). Otherwise 403.
- **State**, in memory, one person: the last report, the current draft (with claim results), the
  current draft run. One run at a time (409 while one runs).
- **Routes** (JSON in and out; errors are `{"error": message}` or `{"reasons": [...]}`):

| Route | Does |
|---|---|
| `GET /api/connection` | `{host, database, schemas: [{name, objects}]}`: schemas the login can use, with readable object counts. Never the user or password |
| `GET /api/models` | `{models: [...], default}`: the model allowlist and the configured default, for the UI's draft picker. The server never offers a model outside `ALLOWED_MODELS` |
| `GET /api/schema[?schemas=a,b]` | the readable catalog the login can see — schemas, tables, columns, types, comments, no statistics — for browsing and downloading before a report. Without `schemas`, every usable schema; invalid names → 400 |
| `GET /api/datasets` | `{datasets: [name]}`: dataset names on the server (subdirectories of its datasets directory holding a `dataset.toml`), so a client with no filesystem access can pick one |
| `POST /api/report` `{schemas}` \| `{dataset}` | the report JSON (same as the CLI's), stored. Exactly one of the two: `schemas` is a non-empty list of schema names; `dataset` is a plain name resolved against the server's own datasets directory (same overlay and exact context as the CLI's `--dataset-dir`). Unknown name → 404; anything else malformed → 400 |
| `POST /api/estimate` `{model}` | pre-run estimate for the stored report: `{model, prompt_chars, input_tokens_est, first_call_usd, run_ceiling_usd, max_calls}` |
| `POST /api/draft` `{model, effort}` | starts a run on the stored report → `{run_id}`; 409 if one runs; 400 without a report or with a model outside the allowlist |
| `GET /api/draft/{id}/events?after=N` | `{events: [...], done, draft?, error?}`: events after index N (the on_event dicts); when done, the verified draft and its validation, or the error |
| `GET /api/draft` | the current draft and its validation, or 404 |
| `POST /api/claims/verify` `{claims: [{text, file, sql, expect, dismissed}]}` | replaces the draft's claims, reruns all, stores and returns them |
| `POST /api/validate` `{files, comments}` | stores the edits; returns `{errors, context_chars}` |
| `POST /api/export` `{name, target, files, comments, dismissed: [index], force?}` | stores the edits and dismissals, exports with the **stored** claim results → `{path, overwrote: [...], backup}` (paths relative to the repo) or 422 `{reasons}`; `force: true` replaces the draft's files in an existing directory (backup kept) instead of refusing |

  Claim results only ever come from the server's own runs: a client can't send a state.
- **Estimate:** `input_tokens_est = ceil(len(system prompt + user message) / 3)` (deliberately
  high for English and JSON); `first_call_usd = input × input price + max_tokens × output price`;
  `run_ceiling_usd = max_calls × first_call_usd`. The UI labels it "rough ceiling", says it
  ignores the cache discount and the growth from tool results, and shows the actual cost after.
- **Errors and secrets:** responses and logs never hold the DSN, user, password or API key;
  database errors give the class and primary message only; unexpected errors are 500 with the
  class name. Model errors are the run's `error` (already scrubbed by `run_draft`).

### UI

`<bootstrap-app>` holds the state and shows a step bar (Connect · Report · Review, current one
marked) and one screen element at a time.

1. **`<bs-connect>`**: database host and name; when the server holds datasets, a choice of an
existing dataset (a plain name; the directory stays server-side) or hand-picked schemas, else
a checkbox per schema (with object count); "Run report" (disabled until a dataset or at least
one schema is chosen).

`<bs-connect>` also offers a "Test connection" button (re-checks and shows host, database, visible schemas and round-trip time, or the error) and a "Show schema" toggle rendering the readable catalog from `GET /api/schema` with a "Download schema (JSON)" button.
2. **`<bs-report>`**: summary bar (objects, columns, % with comments, context chars vs 20,000);
   a filter "Only missing comments"; per object a `<table>`: name, kind, coverage badge
   ("n/m commented"), row estimate; rows per column: name, type, nullable, null fraction,
   distinct estimate, top values, comment or a "no comment" marker. Model picker (the server's `/api/models` list, starting on its default; the client hardcodes nothing),
   effort picker, the estimate, and "Draft with <model>". While drafting: a progress list (each
   `run_sql` purpose, each call's tokens and cost) and running totals; polled every 500 ms.
3. **`<bs-review>`**: a tab per file (`dataset.toml`, `notes.md`, `glossary.md`) with a
   monospace `<textarea>`; a comments table (object, column, kind, text in a textarea, an
   "accept" checkbox; rewrites show the current comment); `<bs-claims>`: per claim the text,
   file, SQL (editable), expectation (editable JSON), state badge, message, actual result,
   "Re-run" (sends all claims) and "Dismiss"/"Undismiss". Validation (errors, context size) is
   refreshed 400 ms after the last edit. Name field, target radio (overlay `comments.toml` /
   `comments.sql`; default from the report's `can_comment`: true → sql), an "Overwrite the draft's
   files if the directory exists" checkbox (replaced files are backed up), "Export" disabled
   while validation has errors, a failed/error claim isn't dismissed, a claim was edited but
   not re-run, or the name is empty; the server's refusal reasons are shown. "Re-run report"
   goes back to the Report screen with a fresh report (the draft is kept).

Keyboard: everything is a native control in tab order; tabs are buttons with `aria-selected`;
visible `:focus-visible` outline. Real `<table>` markup with `<th scope>`.

### Criteria → tests

| # | Criterion | Test |
|---|---|---|
| S1 | Full flow over HTTP with a stub model: connection → report → estimate → draft (events, done) → verify → validate → export, for both targets; the toml export loads and has no overlay drift | `test_server.py::test_full_flow` (db) |
| S2 | Request guard: wrong Host → 403; POST without JSON content type → 403; GETs from the right host work | `test_server.py::test_request_guard` |
| S3 | One run at a time (409); draft without report (400); model outside the allowlist (400) | `test_server.py::test_draft_errors` (db) |
| S4 | Claim states come only from the server: export with a client-sent "verified" is ignored; a result sent to verify is ignored (the claim is rerun); dismissals by index unblock | `test_server.py::test_claims_integrity` (db) |
| S5 | No secrets in any response body or captured log over the full flow, including a model error | `test_server.py::test_no_secrets` (db) |
| S6 | `visible_schemas` lists usable schemas with counts, not `pg_*`/`information_schema` | `test_server.py::test_connection` (db) |
| S7 | Estimate arithmetic and labels | `test_server.py::test_estimate` (db) |
| S8 | The built UI is served at `/` next to the API (one origin) | `test_server.py::test_serves_the_built_ui` (db) |
| S9 | `/api/draft` and `/api/estimate` refuse a body that isn't a JSON object, or a non-string model/effort, with 400 (no run starts); `/api/report` is 409 while a run is going; an unexpected error is a 500 whose log holds only the class name | `test_server.py::test_bad_bodies_are_400`, `::test_report_is_refused_while_drafting`, `::test_unexpected_error_logs_only_the_class` (db) |
| U1 | Report: summary numbers; "only missing" filter hides fully commented objects and commented columns; "no comment" marker; real `<table>`/`<th>` | `ui/test/report.test.ts` |
| U2 | Review: export disabled by validation errors, by an undismissed failed/error claim, by an edited-not-rerun claim (the server can't see edits that weren't re-run, so this is the UI's rule), by an empty name; enabled otherwise; refusal reasons shown; a validation reply older than the latest is ignored | `ui/test/review.test.ts` |
| U3 | Claims panel: each state's badge; dismiss toggles; re-run posts all claims and shows new states | `ui/test/claims.test.ts` |
| U4 | App: step bar marks the current step; connect → report → review with a stubbed `fetch`; review → "Re-run report" (pending edits saved first) → "Back to the draft" shows the server's stored draft; dataset mode posts `{dataset}` and schemas mode posts `{schemas}` | `ui/test/app.test.ts` |
| S10 | `/api/report` takes exactly one of `schemas` or `dataset`: a known name reports with the overlay and a `full` context; unknown → 404; traversal, empty and non-string names, both keys and neither → 400; `/api/datasets` lists only directories holding a `dataset.toml` | `test_server.py::test_report_by_dataset_name`, `::test_report_by_dataset_name_applies_the_overlay`, `::test_report_dataset_selection_errors` (db) |
| S11 | `GET /api/schema` dumps schemas, tables, columns, types and comments without statistics (`?schemas=` narrows it; bad names → 400); `GET /api/models` returns the allowlist and the configured default | `test_server.py::test_schema_dump`, `::test_models` (db) |
| U6 | Connect: "Test connection" re-checks and shows the result; "Show schema" renders the dumped catalog with a download button; the draft model picker offers the server's list starting on its default | `ui/test/app.test.ts` (three new cases) |
| U5 | `npm run build` passes (type check + bundle) | build |

### Decisions

- Polling (`?after=N`) instead of SSE: simpler on both sides and in tests, and per-tool-call
  progress every 500 ms is enough (the brief allows progress per tool call).
- The server serves the built UI: one origin, so no CORS and no extra port in use.
- The estimate is a labelled rough ceiling, not a bound: we have no tokenizer for Muse models.
- One person, in memory: restarting the server loses the draft (export or keep the CLI's
  `draft.json` for durability). Saving a draft from the UI is out of scope.
- **Where review edits live:** on the server. `validate` and `export` store the files and comments,
  `verify` stores the claims. Leaving review flushes a pending validation first; "Back to the
  draft" reloads `GET /api/draft`. Dismissals not yet sent by verify or export are not kept.
- A kept draft is validated and exported against the report it was drafted from, not a newer
  report run afterwards (the new one is for looking at coverage, e.g. after applying
  `comments.sql`). `/api/report` is refused while a draft runs.
- A request body that isn't a JSON object is a 400 everywhere; it never starts a paid run.
- Unexpected errors: the client gets `unexpected <Class>`; the log gets the class only (the
  server swallows Starlette's re-raise so uvicorn doesn't print the message).
- The report screen offers schemas or a server-side dataset name (the client never sees a path);
  redrafting an existing Dataset is still out of scope — reporting on one is for checking coverage
  (e.g. after applying `comments.sql`), and a kept draft stays validated against the report it was
  drafted from. The CLI keeps `--dataset-dir` for local use.

### Test command

```bash
cd explorer/bootstrap/server && uv run pytest     # Docker; no network, no model
cd explorer/bootstrap/ui && npm test && npm run build
```

## Phase 3: Boards (explorer SPEC §18 slice 4)

Status: **approved** (2026-11-04). The user answered the draft's two open questions and asked for
it to be built. It was drafted as `explorer/specs/board-drafting/SPEC.md`, which now points here.
Decision: ADR 0032 (a second Wonder loop, with TOML checked by the agent's own code). Where this
phase is silent, explorer `SPEC.md` §18 and phases 1–2c apply.

ADR 0028 says Boards are written with the Dataset, drafted here and reviewed by a person. Slices
1a–3 built the runtime. This phase makes the bootstrap draft, check and preview
`datasets/<name>/boards/*.toml` for any database.

It's built in two parts:
- **Part A** (this build): templates, the loop, checks, preview, server, UI, export and CLI.
- **Part B** (after the live run): Board scenarios in `agent/scripts/scenarios.py` (SC1), and the
  manual run that drafts austender's Boards and measures B-M (BM). Part B needs real Boards to
  time, and the live run needs the user's OK first.
- **Part B is built** (2026-11-04). SC1 is specified in `specs/scenarios/SPEC.md` (the Board
  scenarios amendment). austender's Boards reached `datasets/austender/boards/` through a scratch
  export (ADR 0036). Both halves of B-M are recorded in `explorer/SPEC.md` §18: the speed half
  and, measured by hand on 2026-11-04, the typed-question half.

### Files

| File | Change |
|---|---|
| `templates/explorer.toml` | an optional `[[controls]]` slot block, commented out |
| `templates/entity.toml` (new) | the Entity Board template |
| `templates/controls.md` (new) | guidance for the model, not a file the agent loads: which Control kinds suit which columns, the typed-NULL "All" idiom, and that option queries take no placeholders |
| `server/src/explorer_bootstrap/draft.py` | the loop body moves into `tool_loop()`, shared by both loops. `run_draft` behaves as before |
| `server/src/explorer_bootstrap/boards.py` (new) | prompt, `submit_boards` schema and validation, `run_boards`, `check_boards`, `preview_boards`, `estimate_boards` |
| `server/src/explorer_bootstrap/prompts/boards.md` (new) | generic instructions for the Boards loop |
| `server/src/explorer_bootstrap/drafts.py` | `draft_dataset(draft)`: the drafted Dataset and its schema section, shared by `validate_files` and Boards |
| `server/src/explorer_bootstrap/settings.py`, `explorer/.env.example` | `BOOTSTRAP_MAX_ENTITY_BOARDS`, `BOOTSTRAP_BOARD_PREVIEW_ROWS` |
| `server/src/explorer_bootstrap/server.py` | the Boards endpoints, and Boards in state |
| `server/src/explorer_bootstrap/export.py` | `boards/` written, and replaced whole under `force` |
| `server/src/explorer_bootstrap/__main__.py` | `boards` subcommand; `export --boards` |
| `ui/src/boards.ts` (new), `app.ts`, `api.ts`, `types.ts`, `review.ts` | the Boards step, and Board problems as an export blocker |
| `agent/src/explorer_agent/boards.py` | `check_board_set()` (the set rules, out of `load_boards`), `BOARD_FILE_RULES`, and an optional `on_query` hook on `BoardRenderer` |

### Templates

- **`explorer.toml`** keeps its shape. It gains a commented `[[controls]]` block (a `choice` with
  `type`, `options_sql`, `all_label` and `default` slots) and a comment saying that every Control
  must be used as `:<name>` by a query.
- **`entity.toml`**: `title`, `question`, `scope`, `key`, `key_type` and `key_columns` slots, all
  before `[[queries]]`. The `scope` slot says the Board isn't limited to the clicked answer's
  filters. Its layout has 2–3 Metrics on a totals query, a `Timeline` or `LineChart` trend, and a
  top-N `DataTable`. Every query is filtered by `:<key>`.
- No SQL and no dataset words in the `.toml` templates. `controls.md` is guidance, so it shows
  generic SQL idioms (the typed-NULL "All" filter) but names no dataset (amended after refuter
  round 1).

### The Boards loop (`run_boards(draft, runner, settings, *, completion, on_event)`)

- **Needs** a draft whose files validate (`validate_files` with no errors). The Dataset is read
  from the drafted files (`draft_dataset`), not from disk.
- **Same loop as the draft** (`tool_loop`): LiteLLM `acompletion`, `BOOTSTRAP_MODEL`,
  `reasoning_effort`, `max_tokens`, `num_retries: 0`, the nudge, the iteration cap
  (`DraftError`), usage and cost per call, and `on_event` events. The tools are `run_sql` (on the
  Dataset's `allowed_schemas`) and `submit_boards`. `prompt_cache_key` is
  `bootstrap-boards-<dataset name>`.
- **The system prompt**, in this order, is byte-identical for the same draft:
  1. the A2UI Express rules and inference catalog, from the agent's `UiPipeline` format, so the
     Macro signatures are the agent's own;
  2. `prompts/boards.md`;
  3. `BOARD_FILE_RULES`;
  4. the three templates, each under its file name;
  5. the drafted Dataset context (`dataset_context`, with the accepted comments applied);
  6. the setting `BOOTSTRAP_MAX_ENTITY_BOARDS`, as "Draft at most N Entity Boards".
- **The model picks the entities** (the user, 2026-11-04): key-like columns, meaning identifier
  columns that appear in several tables or views. Each Board carries a one-line `why` for the
  reviewer.
- **`submit_boards`** (ADR 0023: no `oneOf`, `additionalProperties: false`, every key required):
  `{boards: [{name, toml, why}]}`. `name` is the file stem and `toml` is the whole file.
- **Checks on submit** (`check_boards(files, dataset, runner, max_entity_boards)`) go through the agent's code, so a
  Board that passes here passes the agent's startup check:
  - each name matches `[a-z_][a-z0-9_]*` and appears once;
  - `parse_board(toml, Path("boards/<name>.toml"), dataset.allowed_schemas)`. The guard keeps
    every query inside the allowed schemas;
  - `check_board_set`: `explorer` is present, it has no key, `key_columns` don't overlap, and
    there are at most `BOOTSTRAP_MAX_ENTITY_BOARDS` Entity Boards;
  - `check_board` on each parsed Board, through a `UiPipeline` built from the drafted Dataset
    (currency, allowed schemas) and the submitted set's `entity_columns` and key types.
  - The result is a problem list per Board (the agent stops at a Board's first problem, so that
    is one per Board), plus set problems. Any problem goes back as the tool error and the loop
    continues. A clean submit ends the loop.

### Preview (`preview_boards(check, dataset, runner, report, preview_rows)`, no model)

- One cold render per accepted Board: `BoardRenderer.outcomes(fresh=True)` at its default
  Controls, then `build_board`. It uses the agent's defaults (`BOARD_QUERY_CONCURRENCY` 2,
  `BOARD_ROW_CAP` 500, the server's SQL timeout).
- **An Entity Board's sample key** comes from the first reported table or view (in report order,
  within the allowed schemas) that has a column named in its `key_columns`:
  `SELECT "<col>" FROM "<schema>"."<table>" WHERE "<col>" IS NOT NULL LIMIT 1`. If no reported
  object has such a column, the preview says "no sample key" and skips the render. That isn't a
  problem: key columns can be aliases.
- **It reports:**
  - per query: rows, truncated, ms (through the new `on_query` hook), and the first
    `BOOTSTRAP_BOARD_PREVIEW_ROWS` rows as text;
  - per Visual (a Macro call in the layout, by its layout ID): the Macro, its query, and `drawn`,
    `no_rows` (the panel holds the "No rows" text) or `failed` (a failed query's Card, or "couldn't
    be drawn");
  - the total seconds, against `BOARD_COLD_TARGET_S` (1.5), as `ok` or `slow`.
- The preview informs the reviewer but never blocks export: a "No rows" panel can be right. It
  shows no A2UI. The reviewer opens the exported Dataset with `./run.sh --real` before committing.

### Server

- **State:** `boards` beside `draft`: `{boards: [{name, toml, why}], problems: {name: [..]},
  set_problems: [..], preview, usage}`. It's lost on restart, as the draft is. A new dataset draft
  clears it. One run at a time across both loops (the shared `state.run`).

| Route | Does |
|---|---|
| `POST /api/boards/estimate` `{model, effort}` | the Boards prompt's estimate, as `/api/estimate` gives for the draft; 404 without a draft |
| `POST /api/boards/draft` `{model, effort}` | starts the Boards loop → `{run_id}`; 404 without a draft; 400 when the draft's files have errors (`reasons`); 409 while any run is going |
| `GET /api/draft/{id}/events` | as before. When a Boards run is done, it gives `boards` (the state above) instead of `draft` |
| `GET /api/boards` | the Boards state, or `null` before any Boards (200: no Boards yet is not an error) |
| `POST /api/boards/validate` `{boards: [{name, toml, why}]}` | stores the edits, re-checks and re-previews, and returns the state; 404 without Boards; 400 for a malformed body |

- `POST /api/export` re-checks the stored Boards against the current files before exporting, so an
  edit to `allowed_schemas` can't slip a stale Board through.

### Export

- `export_dataset(..., boards=None)` takes the Boards as `[{name, toml}]` together with their
  problems. It refuses (with the reasons) when any Board or the set has problems.
- A new directory gets `boards/<name>.toml` beside the other files, inside the same temporary
  sibling directory, so a failure leaves nothing.
- **With `force` and an existing directory:** an existing `boards/` is replaced whole (temp
  sibling, then rename), and the old one is copied to the backup as `boards/`. A Board left out of
  the new set must not linger. Without Boards in the draft, an existing `boards/` is left alone.
- The exported directory loads with the agent's `load_dataset`, Boards included.

### CLI

```
uv run python -m explorer_bootstrap boards --draft draft.json --out boards.json [--model M] [--effort E]
uv run python -m explorer_bootstrap export --draft draft.json --name N --target toml|sql [--boards boards.json] [--force]
```

- `boards` runs the loop, checks, previews, writes `boards.json` (`{boards_version: 1, boards,
  problems, set_problems, preview, usage}`), and prints the preview. Its exit codes are those of
  `draft`. The stub model comes through `COMPLETION`.
- `export --boards` re-checks `boards.json` against the draft's files and writes the Boards.

### UI

- A **Boards** step after Review: Connect · Report · Review · Boards. Review gets a "Boards"
  button.
- The Boards step has:
  - the estimate (at the model and effort picked on the Report screen), then "Draft Boards";
  - while drafting, the same progress list as the draft;
  - one tab per Board, each with a monospace `<textarea>`, its `why` line, its problems, and the
    preview: per query a `<table>` of the first rows with rows and ms, per Visual its verdict,
    and the cold time against 1.5 s;
  - set problems above the tabs;
  - re-validation 400 ms after the last edit, where a reply older than the latest is ignored;
  - "Back to Review".
- **Export** stays on Review. Its blockers gain "a Board has problems" when the server holds
  Boards.

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `BOOTSTRAP_MAX_ENTITY_BOARDS` | `3` | Entity Boards per draft (the user, 2026-11-04) |
| `BOOTSTRAP_BOARD_PREVIEW_ROWS` | `5` | Rows per query shown in the preview |

### Criteria → tests

`(db)` needs Docker. Part B's rows are listed but not built in Part A.

| # | Criterion | Test |
|---|---|---|
| T1 | Each template is valid TOML with its top-level keys before `[[queries]]`, holds no SQL and no dataset words, and loads and passes `check_board` once its slots are filled. That covers `entity.toml`, and `explorer.toml` with its `[[controls]]` block uncommented | `agent/tests/test_boards.py` (B22 extended) |
| T2 | `check_board_set`: no `explorer`, a keyed `explorer` and overlapping `key_columns` each raise naming the file; `load_boards` still raises the same messages | `agent/tests/test_boards.py` |
| T3 | `BOARD_FILE_RULES` names every key in `KEYS`, every Control kind and `MAX_BOARD_QUERIES`; `on_query` gets each query's name, ms and outcome | `agent/tests/test_boards.py` |
| D9 | The Boards prompt holds the six parts in order; it's byte-identical for the same draft and different for a different max; the rules text is `BOARD_FILE_RULES` itself; `run_draft`'s tests D1–D5 still pass on `tool_loop` | `server/tests/test_boards_draft.py`, `test_draft.py` |
| D10 | `submit_boards` follows ADR 0023's schema rules. A Board that fails the name rule, `parse_board`, the set rules, `check_board` or the schema guard comes back as a tool error naming the Board (and key), and the loop continues. A clean submit ends it; the cap is a `DraftError` | `test_boards_draft.py` (stub model, db) |
| D11 | One Entity Board more than `BOOTSTRAP_MAX_ENTITY_BOARDS` is a tool error | `test_boards_draft.py` (db) |
| D12 | Preview: per-query rows, ms and first rows as text; per-Visual drawn / no_rows / failed; the cold verdict; an Entity Board previewed with a sample key from the report; "no sample key" when none fits | `test_boards_draft.py` (db) |
| E6 | Export writes `boards/*.toml` that `load_dataset` loads; it refuses with Board problems; under `force` it replaces `boards/` whole and backs the old one up; without Boards it leaves an existing `boards/` alone | `test_export.py` (db) |
| S12 | Boards endpoints: 404 without a draft, 400 when the draft's files have errors, 409 while either loop runs; the events give `boards` when done; validate stores and re-checks edited text; export re-checks and refuses on problems; a new draft clears Boards | `test_server.py` (stub model, db) |
| S13 | `/api/boards/estimate` gives the Boards prompt's estimate, larger than the draft's for the same report | `test_server.py` (db) |
| U7 | The Boards step: estimate then draft with progress; a tab per Board with problems and preview; re-validation after edits ignores stale replies; Review's Export is disabled while a Board has problems | `ui/test/boards.test.ts`, `ui/test/review.test.ts` |
| L5 | `boards` CLI writes `boards.json` with a stub model; `export --boards` writes them | `test_cli.py` (db) |
| SC1 | Part B. Board scenarios run with no model and record open, Control change and Entity Board times against §18's targets | `agent/tests/test_scenarios.py` |
| BM | Part B, manual. austender's Boards drafted (one live run, with the user's OK), reviewed and exported. B-M measured, with median and p90 per target, and recorded in `explorer/SPEC.md` | manual |

### Decisions

- **A second loop, after the dataset draft** (ADR 0032). Phase 2b's "one loop, no step per
  file" holds for the dataset files.
- **TOML text in the tool call, checked by the agent's own code.** One source of truth for the
  Board rules, and no second schema.
- **The model picks the entities, and the default is 3** (the user, 2026-11-04). No stats query,
  no confirm step.
- **One tool loop** (`tool_loop`), now used twice. The submit check is the only part that
  differs, and the Boards check is async because it needs the database.
- **The Boards prompt carries the agent's Express rules and catalog.** A layout is Express, and
  the Macro signatures must be the agent's own, not a copy.
- **Events reuse `/api/draft/{id}/events`.** One run slot, one poller.
- **The estimate gets its own route** (`/api/boards/estimate`), as the draft's has.
- **The preview never blocks.** Only the checks block export.
- **Export re-checks** the Boards against the files as they are at export time.
- **Replace `boards/` whole under `force`**, so a Board left out of the new set doesn't linger.
  Without Boards in the draft, `boards/` isn't touched.
- **Accepted Boards** means the Boards the server holds, with no problems. There's no separate
  accept toggle. To export without Boards, export before drafting them.
- **The set rules move** out of `load_boards` into `check_board_set`, so the bootstrap checks a
  set it holds in memory. The messages don't change.
- **Per-query ms** comes from an optional `on_query` hook on `BoardRenderer`, rather than a
  wrapped runner that would have to recognise the wrapped SQL.
- **A re-draft replaces the Boards whole**, hand edits included, on the server and on screen.
  The button says "Draft the Boards again" (refuter round 1).
- **Validates are serialised** on the server, in arrival order, with the export's re-check
  under the same lock, so an older edit can't overwrite a newer one (refuter round 1).
- **The Boards step's model and effort** are those of the last draft run in this UI session, or
  the server's default model at `low` when there was none (for example after "Back to the draft").
- **Export never trusts a Board name**: one that isn't a Board file name is refused there too.
- **Out:** rendering A2UI in the bootstrap UI; editing Boards in a structured form; runtime
  drafting (slice 5, ADR 0030); drafting Boards for a Dataset already on disk.

### Test command

```bash
cd explorer/agent && uv run pytest
cd explorer/bootstrap/server && uv run pytest     # Docker; no network, no model
cd explorer/bootstrap/ui && npm test && npm run build
```
