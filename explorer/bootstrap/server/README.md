# explorer-bootstrap

A dev tool for adding a Postgres dataset to the explorer. Spec: [`../SPEC.md`](../SPEC.md).
The **report** shows comment coverage, planner statistics, min/max and the size of the Dataset
context; it calls no model. The **draft** step asks a model (Muse Wonder by default) to draft the
dataset files, the missing comments and checkable claims, which you review and **export** to a
new dataset directory. Nothing here writes to the database.

See the [bootstrap guide](../README.md) for the complete architecture and Dataset-plus-Boards
workflow. Board drafting/checking/preview/export is available through the Python library,
CLI, and HTTP API; the browser UI currently exposes Dataset drafting and review only.

## Setup

```bash
uv sync
```

Set `BOOTSTRAP_DATABASE_URL` in the repo root `.env` (see `explorer/.env.example`)
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

## The UI

```bash
cd ../ui && npm ci && npm run build && cd ../server
uv run python -m explorer_bootstrap serve      # http://127.0.0.1:8765/
```

Connect (pick a server-side dataset, or schemas) → Report (coverage, statistics, a rough cost ceiling, "Draft with …" and
its progress) → Review (edit the files and comments, re-run or dismiss claims, export). The
server binds 127.0.0.1 only, keeps one draft in memory (a restart loses it; the CLI's
`draft.json` is the durable route), and refuses requests with another `Host` or a non-JSON POST,
so other web pages can't drive it.

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

### Boards after Dataset review

From `explorer/bootstrap/server/`, after reviewing `draft.json`:

```bash
uv run python -m explorer_bootstrap boards --draft draft.json --out boards.json
uv run python -m explorer_bootstrap export --draft draft.json --boards boards.json --name my_dataset --target toml
```

The first command calls the model in a separate loop, validates TOML using the agent's Board
parser/checks, and runs query/table previews. Review its output before export. Export rechecks
the Boards against the Dataset and database. When `--force` replaces Boards, it replaces the
set and backs up old files. See [SPEC phase 3](../SPEC.md#phase-3-boards-explorer-spec-18-slice-4)
for the HTTP API and validation contract.

## Boards (phase 3)

After the dataset draft, a second loop drafts `boards/*.toml` (the Explorer Board and up to
`BOOTSTRAP_MAX_ENTITY_BOARDS` Entity Boards, default 3) from `../templates/`. Each Board is checked
by the agent's own startup check and previewed with one cold render (per-query rows and ms, per
Visual drawn / No rows / failed, against the 1.5 s target). No model runs in the preview.

```bash
uv run python -m explorer_bootstrap boards --draft draft.json --out boards.json [--model M] [--effort E]
# edit boards.json by hand if needed, then (the Boards are re-checked first):
uv run python -m explorer_bootstrap export --draft draft.json --boards boards.json --name vicprop --target toml
```

In the UI, Review has a "Boards" button. Export stays on Review and is blocked while a Board has
problems. With `--force`, an existing `boards/` is replaced whole and the old one is kept in the
backup. Open the exported Dataset with `./run.sh --real` to see the Boards before committing.
`BOOTSTRAP_BOARD_PREVIEW_ROWS` (5) sets the rows shown per query.

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
