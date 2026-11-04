# Dataset bootstrap

Bootstrap prepares a Postgres database for Explorer: inspect its schema and comments, draft
Dataset descriptions with checkable claims, review the output, and export Dataset files.
A second model loop can draft Boards from the reviewed Dataset context and validate them
with the same code used by the Explorer agent.

This is a separate local development tool, not part of the Explorer request path. It never
writes to the database. An administrator creates the read-only role/grants and applies any
exported comment SQL. The behavioral contract is [SPEC.md](SPEC.md).

## Architecture and implementation status

```text
Read-only Postgres → report (schema, comments, statistics, min/max)
                  → Dataset draft loop → SQL-backed claims → human review
                  → optional Boards loop → agent checks + query/table preview
                  → export datasets/<name>/
```

The Python server offers a CLI and HTTP API. The Lit UI currently exposes Connect → Report
→ Review for Dataset drafting/export. Board drafting, checking, preview, and export are
implemented in the Python library, CLI, and HTTP API; the browser UI does not yet expose
that step. Use the CLI below for the complete Dataset-plus-Boards workflow.

| Path | Responsibility |
|---|---|
| `server/src/explorer_bootstrap/report.py`, `stats.py` | Schema/comment coverage, planner estimates, value ranges, context budget |
| `server/src/explorer_bootstrap/draft.py`, `draft_schema.py` | Shared tool loop and `submit_draft` contract |
| `server/src/explorer_bootstrap/drafts.py`, `claims.py` | Draft files, validation, accepted comments, checkable SQL claims |
| `server/src/explorer_bootstrap/boards.py` | Separate `submit_boards` loop, agent parser/checks, table preview |
| `server/src/explorer_bootstrap/export.py` | Reviewed files, comment targets, backup/overwrite handling |
| `server/src/explorer_bootstrap/server.py`, `__main__.py` | Local HTTP API and CLI |
| `server/src/explorer_bootstrap/settings.py`, `prices.toml` | Model/limit settings and estimated pricing |
| `server/src/explorer_bootstrap/prompts/` | Dataset and Boards drafting instructions |
| `templates/` | Dataset-agnostic Explorer/Entity Board templates and Control guidance |

The server imports `explorer-agent` as an editable local dependency to reuse Dataset loading,
SQL safety, prompt context, Macros, and Board checks. Its browser UI is the explorer client's
Settings (`#/settings`); the old `ui/` project was retired on 2026-11-05. Board previews are
query tables, and visual review happens in the Explorer once a Workspace is exported.

## Setup and browser workflow

Install Python 3.13/`uv`, Node.js 20+/npm, and prepare a database with a read-only login.
Model drafting also needs `META_API_KEY`. `BOOTSTRAP_ENV_FILE` can select another settings file.
Existing environment values win.

The browser workflow is the explorer's Settings (`explorer/specs/workspaces/SPEC.md`, phase 2).
In the root `.env`, set `EXPLORER_DATA_DIR` (an absolute path outside the repo) and one
`<NAME>_DATABASE_URL` read-only login per database, then from the repository root:

```bash
cd explorer
./run.sh --real      # agent :10002, bootstrap server :8765, client :5173
```

Open **http://127.0.0.1:5173/#/settings**. Under "Add a Workspace", pick a login, select
schemas, inspect the report, draft with the chosen model, review and recheck the draft, draft
Boards, then export. Export adds the Workspace and reloads the agent. Report generation uses no
model; drafting asks for confirmation, makes live model calls and logs each call's cost.

Run alone, `uv run python -m explorer_bootstrap serve` serves only the API (with
`BOOTSTRAP_DATABASE_URL`, or in Settings mode with `EXPLORER_DATA_DIR`); its `/` points to
Settings.

## CLI workflow, including Boards

Run these commands from `explorer/bootstrap/server/`. Replace schema and export names to
match your database. The example exports a new directory named `my_dataset`.

```bash
# No model: inspect schema/comment coverage and save the report.
uv run python -m explorer_bootstrap report --schemas mart --json report.json

# Calls the model; saves Dataset files, comments, and checked claims.
uv run python -m explorer_bootstrap draft --schemas mart --out draft.json

# After reviewing/editing draft.json, rerun its SQL-backed claims.
uv run python -m explorer_bootstrap verify --draft draft.json

# Optional second model loop; validates Boards and previews their queries.
uv run python -m explorer_bootstrap boards --draft draft.json --out boards.json

# After reviewing both files, recheck Boards and export with a comment overlay.
uv run python -m explorer_bootstrap export --draft draft.json --boards boards.json --name my_dataset --target toml
```

Without Boards, omit the `boards` command and `--boards boards.json` argument. `draft.json`
and `boards.json` are local review artifacts and can contain database-derived context; treat
them accordingly. The CLI files persist across server restarts, unlike browser state.

Review every factual claim and proposed comment rewrite. Claims must be checked or explicitly
dismissed under the export rules; failing/error/unchecked claims block export unless dismissed.
Board files are parsed and checked against the drafted Dataset, allowed schemas, query output,
Controls, and Macro references. Previews show query data and cold-render timings, not a full
browser rendering. Open the exported Dataset in Explorer for visual review.

`--target toml` writes `comments.toml` and its Dataset reference. `--target sql` writes
`comments.sql` for an object owner to apply manually. Export does not grant privileges or
apply SQL. Without `--force`, an existing Dataset directory is refused. With `--force`,
replaced files are backed up; when supplying Boards, the previous Board set is replaced as
a set so stale entity definitions cannot remain. Review the reported backup before cleanup.

## Configuration, tests, and limits

Muse Wonder (`meta/muse-wonder-1.0`) is the default draft model; Muse Spark
(`meta/muse-spark-1.3`) is the supported alternative. `BOOTSTRAP_MODEL` and
`BOOTSTRAP_REASONING_EFFORT` select the defaults. `BOOTSTRAP_MAX_TOOL_ITERATIONS`,
`BOOTSTRAP_MAX_TOKENS`, `BOOTSTRAP_CALL_TIMEOUT_S`, `BOOTSTRAP_SQL_ROW_CAP`, and
`BOOTSTRAP_ROWS_TO_MODEL` bound drafting. Board limits are
`BOOTSTRAP_MAX_ENTITY_BOARDS` and `BOOTSTRAP_BOARD_PREVIEW_ROWS`.
See [the settings example](../.env.example) and [server reference](server/README.md).

```bash
# explorer/bootstrap/server/; database tests need Docker
uv run pytest
```

Settings' own tests run with the client's (`explorer/client`, `npm test`).

Tests stub model calls and external network responses. The server binds to loopback and
checks Host and POST content type. It keeps one in-memory draft and permits one model run
at a time. Restarting loses the Settings draft; export or use CLI artifacts for durability.
The report uses planner estimates rather than exact row counts, and min/max can time out.
It does not run `ANALYZE`. Missing grants must be fixed by the database owner.

For detailed CLI options, exit codes, overlay handling, and report precision limits, see
[server/README.md](server/README.md). For decisions, see
[ADR 0023](../../docs/adr/0023-bootstrap-drafts-arrive-as-a-tool-call.md) and
[ADR 0032](../../docs/adr/0032-boards-are-drafted-in-a-second-bootstrap-loop.md).
