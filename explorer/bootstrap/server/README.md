# explorer-bootstrap

A dev tool for adding a Postgres dataset to the explorer. Spec: [`../SPEC.md`](../SPEC.md).
The **report** shows comment coverage, planner statistics, min/max and the size of the Dataset
context; it calls no model. The **draft** step asks a model (Muse Wonder by default) to draft the
dataset files, the missing comments and checkable claims, which you review and **export** to a
new dataset directory. Nothing here writes to the database.

## Setup

```bash
uv sync
```

Set `BOOTSTRAP_DATABASE_URL` in the repo root `.env` (`genUI/.env`; see `explorer/.env.example`)
or in the environment. Use a read-only login, such as AusTender's `explorer_ro`. The report
makes every session read-only with a statement timeout anyway, but a read-only login is the
safer default.

## Report

```bash
# An existing dataset: its allowed schemas, and the exact context size
uv run python -m explorer_bootstrap report --dataset-dir ../../datasets/austender

# A database with no dataset yet: the context size covers the schema section only
uv run python -m explorer_bootstrap report --schemas public,stats --json report.json
```

Options: `--json OUT` writes the full report (the input to phase 2), `--no-minmax` skips the
min/max queries, `--timeout-ms N` sets the per-query timeout (default 5000; a min/max that hits
it shows as "skipped"), `--check` exits 1 if any table or column lacks a comment or the context
is over the 20,000-character target.

"Login can add comments: no" means the login doesn't own the objects. Comments for such a
database go in a Comment overlay (`comments.toml`, ADR 0019; format in `../../agent/README.md`).
"Never analysed" tables have no planner statistics; the report doesn't run `ANALYZE` itself, so
ask the owner to.

With `--dataset-dir` on a Dataset that has an overlay, the report applies it exactly as the
explorer does: coverage, missing comments and the context size use the overlaid comments, and a
`Comment overlay …` line counts them. In the JSON, each table and column has `comment_source`
(`overlay`, `database` or null) and, where the overlay replaced a database comment,
`database_comment`. An overlay entry for a table or column that doesn't exist exits 2.

Exit codes: 0 success, 1 `--check` failed, 2 usage, config, connection or runtime error.

Known limits: min/max runs one query per table, so one slow column marks all of that table's
ranges "skipped", and one column the login can't read marks them "error". NaN and Infinity
show as null. Min/max of high-precision `numeric` columns are JSON numbers and can lose digits.

## Draft, verify, export

```bash
# 1. Draft: runs the report, then the model; checks every claim; writes draft.json
uv run python -m explorer_bootstrap draft --schemas public --out draft.json [--model meta/muse-spark-1.3] [--effort low]

# 2. Review draft.json by hand: edit files and comments, set "accepted": true on comment
#    rewrites you want, fix a claim's sql/expect, or set "dismissed": true. Then recheck:
uv run python -m explorer_bootstrap verify --draft draft.json

# 3. Export to explorer/datasets/<name>/ with the comments as an overlay or as SQL
uv run python -m explorer_bootstrap export --draft draft.json --name vicprop --target toml
uv run python -m explorer_bootstrap export --draft draft.json --name vicprop --target sql
```

- The model sees the report and can run read-only `SELECT`s through the explorer's SQL guard,
  limited to the chosen schemas. It hands in the draft with a `submit_draft` tool call (ADR 0023).
- **Claims:** every factual statement in the draft comes with SQL and an expectation. A claim is
  `verified`, `failed`, `error` or `needs_review`. Export refuses while a claim is `failed`,
  `error` or unchecked and not dismissed, while the files don't validate, or if the directory
  exists.
- **Comment target:** `--target toml` writes `comments.toml` and points `dataset.toml` at it
  (use it when the login can't add comments). `--target sql` writes `comments.sql`: apply it as
  the objects' owner (`psql -f comments.sql`), then start the explorer. The tool never runs it.
- **Before the explorer can use a new dataset**, a database admin creates its read-only role and
  grants (`USAGE` on the schemas, `SELECT` on the tables). That's a manual step.
- Settings (env): `BOOTSTRAP_MODEL` (`meta/muse-wonder-1.0` or `meta/muse-spark-1.3` only),
  `BOOTSTRAP_REASONING_EFFORT` (`low`), `BOOTSTRAP_MAX_TOOL_ITERATIONS` (12),
  `BOOTSTRAP_MAX_TOKENS` (32000), `BOOTSTRAP_CALL_TIMEOUT_S` (300), `BOOTSTRAP_SQL_ROW_CAP` (200),
  `BOOTSTRAP_ROWS_TO_MODEL` (20). `META_API_KEY` is read by LiteLLM.
- Cost per call and in total is printed and stored in `draft.json`; prices are in
  `src/explorer_bootstrap/prices.toml`. Reasoning tokens are assumed to be billed inside the
  completion tokens (the OpenAI convention; not confirmed for Meta).

## Tests

```bash
uv run pytest      # needs Docker (Testcontainers Postgres 18); no network, no model
```
