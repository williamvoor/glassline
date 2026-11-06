# explorer-bootstrap

CLI and HTTP API for [Dataset bootstrap](../README.md). The browser workflow is Explorer
Settings, including Board drafting and export. This guide covers the CLI; all commands run
from `explorer/bootstrap/server/`.

## Setup

```bash
uv sync --locked
```

Set `BOOTSTRAP_DATABASE_URL` to a read-only login in the root `.env` or environment.
Model commands also require `META_API_KEY`. `BOOTSTRAP_ENV_FILE` selects another settings
file; existing environment values win. See [the settings example](../../.env.example).
`draft` and `boards` call the model immediately, without an interactive confirmation.

## Report

```bash
# An existing dataset: its allowed schemas, and the exact context size
uv run python -m explorer_bootstrap report --dataset-dir ../../../datasets/austender

# A database with no dataset yet: the context size covers the schema section only
uv run python -m explorer_bootstrap report --schemas public,stats --json report.json
```

Options: `--json OUT` writes the full report (the input to phase 2), `--no-minmax` skips the
min/max queries, `--timeout-ms N` sets the per-query timeout (default 5000; a min/max that hits
it shows as "skipped"), `--check` exits 1 if any table or column lacks a comment or the context
is over the 20,000-character target.

"Login can add comments: no" means the login doesn't own the objects. Comments for such a
database go in a Comment overlay (`comments.toml`; format in `../../agent/README.md`).
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
uv run python -m explorer_bootstrap draft --schemas public --out draft.json

# 2. Review draft.json by hand: edit files and comments, set "accepted": true on comment
#    rewrites you want, fix a claim's sql/expect, or set "dismissed": true. Then recheck:
uv run python -m explorer_bootstrap verify --draft draft.json

# 3. Export with a comment overlay (default root: ~/.glassline, created if missing)
uv run python -m explorer_bootstrap export --draft draft.json --name my_dataset --target toml
```

Use `--root /absolute/path/to/datasets` to export elsewhere. Add `--model meta/muse-spark-1.3`
or `--effort low` to a drafting command to override its defaults. Choose `--target sql` instead
of `toml` to export comments for a database owner to apply.

- The model sees the report and can run read-only `SELECT`s through the explorer's SQL guard,
  limited to the chosen schemas. It hands in the draft with a `submit_draft` tool call.
- **Claims:** every factual statement in the draft comes with SQL and an expectation. A claim is
  `verified`, `failed`, `error` or `needs_review`. Export refuses while a claim is `failed`,
  `error` or unchecked and not dismissed, while the files don't validate, or if the directory
  exists. `--force` exports into the existing directory anyway: only the draft's files
  (`dataset.toml`, `notes.md`, `glossary.md`, plus the comments file) are replaced, anything
  else there is left alone, and replaced files are kept in a dot-prefixed sibling backup.
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

## Boards after Dataset review

To include Boards, run these commands instead of the export above:

```bash
uv run python -m explorer_bootstrap boards --draft draft.json --out boards.json
# Review/edit boards.json before export; export rechecks the Boards.
uv run python -m explorer_bootstrap export --draft draft.json --boards boards.json --name my_dataset --target toml
```

The second model loop drafts an Explorer Board and up to `BOOTSTRAP_MAX_ENTITY_BOARDS`
Entity Boards (default 3), guided by `../templates/`. Agent checks validate the Boards;
model-free cold previews report query rows/timing and whether each Visual renders.
`BOOTSTRAP_BOARD_PREVIEW_ROWS` (default 5) limits preview rows. Review the full rendering
in Explorer after export.

When exporting with both `--boards` and `--force`, the old Board set is backed up and replaced whole.
Without `--boards`, existing Boards are left alone. See [SPEC phase 3](../SPEC.md#phase-3-boards-explorer-spec-18-slice-4)
for validation and API details.

To regenerate a whole Dataset and its Boards from one of the repo's databases, the repo root's
`./regen_dataset.sh <db-dir>` runs report → draft → verify → boards → export with review pauses
in between. It derives the read-only login from `<db-dir>/.env`, keeps work files outside the
repo, and can resume a stopped run (`--work-dir`, `--from`). `--keep notes.md,glossary.md`
restores reviewed files from the export backup, so only the Boards are new. `--help` lists the
rest.

## Tests

```bash
uv run pytest      # needs Docker (Testcontainers Postgres 18); no network, no model
```
