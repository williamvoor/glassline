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
(export adds it). Problems are short strings naming the field path.

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
`"glossary.md"` (the names export writes).

### Export (`export_dataset(draft, name, target, root)`)

- Refuses (an `ExportError` listing every reason) when: `name` doesn't match `[a-z][a-z0-9_]*`;
  `root/name` exists; `validate_files` has errors; `export_blockers` isn't empty; `target` isn't
  `toml` or `sql`.
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
| D6 | `validate_draft_args`: each rule above | `test_drafts.py::test_validate_draft_args` (parametrised) |
| D7 | rewrite vs new, `accepted` defaults | `test_drafts.py::test_comment_kinds` |
| C1 | Claim states per the table, incl. NaN/NULL scalar, bool, truncated set, failing SQL in a `none` claim, and a failing claim not stopping the rest | `test_claims.py` (db, parametrised) |
| C2 | Blockers: failed/error block unless dismissed; needs_review doesn't | `test_claims.py::test_export_blockers` |
| V1 | `validate_files`: a valid draft gives no errors and the exact context size (equals the agent's computation over a Dataset dir exported with the overlay); bad TOML, missing key, `[comments]`, unchosen schema → errors without temp paths | `test_drafts.py::test_validate_files` (db) |
| E1 | Export refuses: bad name, existing dir, invalid files, blockers; nothing is written | `test_export.py::test_export_refusals` |
| E2 | The same draft exports to both targets with the same comments: `comments.toml` loads as an overlay, `comments.sql` applied as the owner gives the same comments in the database | `test_export.py::test_both_targets_same_comments` (db) |
| E3 | The overlay export works through the read-only role: the explorer starts with `DATASET_DIR=<export>` (subprocess, as `test_main` does) | `test_export.py::test_explorer_starts_with_overlay_export` (db) |
| E4 | Quoting: quotes, backslashes, newlines, DEL, non-BMP characters and odd identifiers round-trip through `comments.sql` (applied in the database) and `comments.toml` (`load_overlay`) | `test_export.py::test_sql_and_toml_quoting`, `::test_overlay_file_loads_with_tricky_text` (db) |
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
