# explorer agent

The A2A agent behind the explorer: plain-English question → SQL through tools → prose plus an
A2UI Surface built from stored Results. `../SPEC.md` is the source of truth. Step specs are in
`../specs/`.

## Run

```bash
uv sync
cp ../.env.example ../../.env    # repo root; set META_API_KEY and DATABASE_URL (the dataset's read-only login)
uv run python -m explorer_agent  # prints "explorer: listening on http://127.0.0.1:10002"
```

Startup checks that the login is read-only and builds the system prompt once. It then serves on
`AGENT_HOST:AGENT_PORT`. Any configuration or database problem prints one `explorer: …` line and
exits 1.

- `.env` is read from the repo root, or from the path in `EXPLORER_ENV_FILE`. It never
  overrides variables already set. All settings are listed in `../.env.example`.
- The client (`../client`) calls the agent directly (ADR 0007). For a UI without a model or
  database, use the stub server: `uv run python -m explorer_agent.testing.stub_server --port 10002`.

To run it with the client in one command, use `../run.sh --real`.

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

Tests never call a model or the network. The model is `testing/stub_model.py`, and the database
is built by `austender.testing.build_test_db` (ADR 0010).

## Logs

- **Per model call:** model, prompt/cached/completion/reasoning tokens, latency and the
  Conversation's cache hit rate.
- **Per query:** SQL text and row counts.
- **Per Turn:** a WARNING for any Turn slower than `SLOW_TURN_S`.
- **Never logged:** prompts, history, row data or secrets.
