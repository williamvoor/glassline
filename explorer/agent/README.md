# explorer agent

The A2A agent behind the explorer: plain-English question → SQL through tools → prose plus an
A2UI Surface built from stored Results. `../SPEC.md` is the source of truth. Step specs are in
`../specs/`.

Start with the [Explorer README](../README.md) for the current architecture, Board/Control
paths, prerequisite versions, Dataset preparation, and a complete stub or real startup.

## Run

Configure a database and root `.env` using the [Explorer setup](../README.md#run-against-a-real-dataset)
or [Workspace setup](../bootstrap/README.md#setup-and-browser-workflow). Then, from `explorer/agent/`:

```bash
uv sync --locked
uv run python -m explorer_agent
```

Single-Dataset startup checks the read-only login, Boards and prompt; invalid configuration
exits 1. Workspace mode checks each Workspace separately and marks failures unavailable.
Settings come from root `.env` (or `EXPLORER_ENV_FILE`), with exported variables taking precedence.

Use `../run.sh --real` for the agent and client together, including Settings in Workspace mode.
For a stub agent without a model or database:

```bash
uv run python -m explorer_agent.testing.stub_server --port 10002
```

### Example questions

`examples = ["…", "…"]` in `dataset.toml` is optional. The agent card carries the examples, and the client shows them before the first question. They never go into the prompt. Keep them apart from `scenarios.toml`, which is evaluation data.

### Comment overlay

For a database we can't add comments to, a Dataset can keep table and column descriptions in a
Comment overlay file (ADR 0019, `../SPEC.md` §13). Name it in `dataset.toml`:

```toml
[comments]
overlay = "comments.toml"      # relative to dataset.toml
```

```toml
["mart.contract"]              # quote "schema.table"
comment = "One row per contract notice."
["mart.contract".columns]
notice_id = "Contract notice number. Primary key."
```

Each entry replaces the database's comment for that table or column. Everything else keeps its
database comment. The overlay is read once at startup, so **an edit takes effect on restart**.
An entry that names a table or column that doesn't exist in the allowed schemas stops startup.

## Test

```bash
uv run pytest                    # everything except live_db; needs Docker (Testcontainers Postgres 18)
uv run pytest -m live_db         # checks against the loaded dataset database, reads the repo root's .env
UPDATE_SNAPSHOTS=1 uv run pytest tests/test_prompt.py   # after a deliberate system prompt change
```

Ordinary tests stub model calls and external APIs. `live_db` deliberately reads the configured
database. The stub model is `testing/stub_model.py`, and the database
is built by `austender.testing.build_test_db` (ADR 0010).

## Logs

- **Per model call:** model, prompt/cached/completion/reasoning tokens, latency and the
  Conversation's cache hit rate.
- **Per query:** SQL text and row counts.
- **Per Turn:** a WARNING for any Turn slower than `SLOW_TURN_S`.
- **Never logged:** prompts, history, row data or secrets.
