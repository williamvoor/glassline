# AGENTS.md

Guidance for coding agents working in `explorer/`. Read `SPEC.md` (source of truth, approved
2026-10-31, with dated amendments at the end) first. `BUILD_PROMPT.md` is the original brief and
loses to the spec. The glossary is `../CONTEXT.md` and the decisions are `../docs/adr/`. Use the
glossary's terms (Result, Visual, Surface, Turn, Conversation, Repair, Dataset, Comment overlay, …).

## What this is

A plain-English question goes to an LLM agent, which writes and runs SQL through tools
(`list_tables`, `describe_table`, `run_sql`) and replies in A2UI Express. The server compiles the
Express to A2UI v0.9 and expands Macros (`BarChart`, `LineChart`, `DataTable`, `Metric`, `Cite`)
into components using stored Results. The result goes over A2A to a Vite + Lit client.

**ADR 0001 is the central rule:** the model never retypes data. It names a Result ID in a Macro
call and the Macro builds the chart, table or sentence from the stored rows. Every number on screen
comes from the database, and every Visual shows its SQL.

**Speed is the most important requirement.** Prefer fewer model round trips and faster queries, as
long as numbers stay correct.

| Path | Role |
|---|---|
| `agent/` | Python 3.13 A2A agent (`uv`). Module map in `SPEC.md` §8 |
| `agent/src/explorer_agent/llm.py` | Tool loop. The **only** module that imports `litellm` |
| `agent/src/explorer_agent/sql.py` | SQL guard (`pglast`) and read-only execution |
| `agent/src/explorer_agent/macros.py` | Macros, value formats, labels |
| `agent/src/explorer_agent/prompt.py`, `prompts/instructions.md` | System prompt assembly |
| `agent/src/explorer_agent/testing/` | Stub model and stub server (tests and `run.sh` only) |
| `client/` | Vite + Lit chat view: `@a2ui/lit`, vendored VegaChart, `DataGrid`, `SqlDetails` |
| `catalog/explorer_custom_components.json` | Custom components merged into the one catalog (ADR 0002) |
| `datasets/<name>/` | Everything dataset-specific: `dataset.toml`, `notes.md`, `glossary.md`, `scenarios.toml`, optional `comments.toml` |
| `bootstrap/` | Dev tool that drafts a new `datasets/<name>/` (server + UI, own `SPEC.md`) |
| `specs/<step>/SPEC.md` | Sub-specs per build step. Where one is silent, `SPEC.md` applies |
| `spike/` | Phase 1 evidence (`FINDINGS.md`). Historical; don't build on its code |
| `run.sh` | Starts an agent plus the client (`--real` for Muse Spark and the database) |

## Commands

```bash
# agent/
uv sync
uv run pytest                      # all but live_db; needs Docker (Testcontainers Postgres 18)
uv run pytest -m live_db           # by hand, against the loaded dataset database
UPDATE_SNAPSHOTS=1 uv run pytest tests/test_prompt.py   # only after a deliberate prompt change
UPDATE_FIXTURES=1 uv run pytest tests/test_ui.py        # regenerates client/test/fixtures/slice_transport.json

# client/  (the interop test starts the stub agent, so `uv` must be on the PATH)
npm install && npm test && npm run build

# bootstrap/server/ and bootstrap/ui/: see their READMEs
./run.sh            # stub agent + client on http://127.0.0.1:5173
./run.sh --real     # real agent; settings from ../.env
```

## Hard rules

- **Dataset-agnostic.** No AusTender names, tables, currency or wording in code, prompts or tests'
  shared fixtures. Put them in `datasets/<name>/`. The test database comes from
  `austender.testing.build_test_db` (ADR 0010). That's the one allowed dependency on `../austender/`.
- **Models.** Live calls use only `meta/muse-spark-1.3`. Muse Wonder (`meta/muse-wonder-1.0`) is
  allowed only in `bootstrap/`, never in the agent's request path (ADR 0020). No other models.
- **Tests never call a real model or the network.** Use `testing/stub_model.py` with canned tool
  calls and Express. Database tests are marked `db` and fail loudly without Docker. They don't skip.
- **Database safety (SPEC §9).** Connect only with the dataset's read-only role. Startup refuses a
  login with write rights. Every query runs in `BEGIN READ ONLY` with `SET LOCAL statement_timeout`,
  a row cap that reports truncation, and the schema allowlist from config. The `pglast` guard is a
  layer, not the defence. Keep all of these when you touch `sql.py`.
- **Prompt caching.** The order is A2UI rules/catalog → instructions → dataset context →
  conversation and tool results. The prefix must be byte-identical across requests and processes:
  no dates, IDs or timestamps in it. Send a fixed `prompt_cache_key`, and log prompt and cached
  tokens per call. The snapshot hashes in `agent/tests/snapshots/` catch accidental changes.
- **One merged catalog** with one ID on agent and client (ADR 0002). A component outside it renders
  as nothing, so add new components to both sides.
- **Untrusted data.** Database strings reach the UI only through Macros, rendered as text.
  `Cite` escapes markdown. Filled values are escaped only where the text renders as markdown
  (`_fill(..., markdown=...)`): `h1`–`h5`, `caption` and `Tabs` titles are plain text. The
  client's markdown has images and raw HTML off.
- **Pins.** `litellm==1.106.6` exactly, with `mcp` kept as a dependency (it works around a `tools=`
  import bug). `a2a-sdk==0.3.26`. The A2UI SDK packages share one git commit with glimmer-tune
  (ADR 0006). Keep `uv.lock` and `package-lock.json` committed.
- **Express.** Wrap output in `<a2ui>…</a2ui>` (code fences aren't recognised). Express doesn't
  stream.
- **Secrets.** Settings live in the repo root's `.env` (`../.env`, or `EXPLORER_ENV_FILE`). Never
  print or log keys, DSNs, prompts, history or row data. New settings go in `.env.example` with
  placeholders.
- **Don't modify `../a2ui/`** (upstream clone, reference only) and don't depend on paths inside it
  at runtime. VegaChart is vendored in `client/src/vendor/vegachart/`, so keep its Apache-2.0
  header. Don't modify `../austender/` from here either.

## Where to look for A2UI APIs

- Installed SDK: `agent/.venv/lib/python3.13/site-packages/a2ui/`
- Macros: `../a2ui/python/a2ui_agent/src/a2ui/transformers/macros/README.md`, with a working
  example in `../a2ui/samples/community/macros/`
- VegaChart: `../a2ui/typescript/catalogs/vegachart`. Lit shell: `../a2ui/samples/client/lit/shell`

## Changing the spec

New behaviour starts as a dated amendment in `SPEC.md` (or a sub-spec in `specs/`), with
"Criteria → tests" and a decision recorded as an ADR in `../docs/adr/`. Check an amendment's
status before building it: some are drafts awaiting review.

## Commits

Conventional Commits per the repo root's `../.gitmessage`. The scopes in use are `agent`, `client`,
`bootstrap` and `spec` (e.g. `feat(agent): …`).
