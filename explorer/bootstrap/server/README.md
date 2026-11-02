# explorer-bootstrap

A dev tool for adding a Postgres dataset to the explorer. Spec: [`../SPEC.md`](../SPEC.md).
Phase 1 is the **report**: comment coverage, planner statistics, min/max and the size of the
Dataset context. It calls no model and never writes to the database.

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

## Tests

```bash
uv run pytest      # needs Docker (Testcontainers Postgres 18); no network, no model
```
