# Workspaces: several databases, saved Conversations, Settings

Status: **approved** (2026-11-04, the user), not built. It came out of a grilling session with the
user on 2026-11-04. Where this file is silent, `explorer/SPEC.md` and `bootstrap/SPEC.md` apply.

Decisions: ADR 0039 (Workspaces are sub-apps of one agent process), ADR 0040 (saved
Conversations keep SQL, not rows), ADR 0041 (the bootstrap UI becomes Settings). All accepted (2026-11-04).

## Why

Today one running agent serves one Dataset, and a Conversation lives only as long as the agent
(SPEC §1 put both out of scope for v1). The user wants to switch between several databases in one
explorer, come back to earlier investigations after a restart, and add a database from the
explorer itself.

## Terms

From `CONTEXT.md`: a **Workspace** is one Dataset together with its saved Conversations. It can be
**unavailable**: its database can't be reached, or its login can write. Each Workspace has exactly
one Dataset. A **Conversation** is saved in its Workspace and can be resumed. A saved Result keeps
its SQL, not its rows.

## Decisions taken in the grilling

| # | Decision | By |
|---|---|---|
| G1 | A Workspace is one Dataset plus its saved Conversations. There are no questions across Workspaces | user |
| G2 | Single user, local, no logins | user |
| G3 | One agent process, one sub-app per Workspace (ADR 0039) | user |
| G4 | Conversations can be resumed after a restart: ask on, click, change Controls | user |
| G5 | Workspaces can be added from the explorer. The bootstrap UI becomes Settings, as phase 2 of this spec | user |
| G6 | Files live in `EXPLORER_DATA_DIR`, outside the repo. Repo Datasets are referenced, not copied | user |
| G7 | A Workspace's login comes from an environment variable named in the Workspace list | user |
| G8 | A Workspace that fails its checks is listed as unavailable, and the others keep working | user |
| G9 | Saved Results keep their SQL, not their rows, and are re-run (ADR 0040) | user |
| G10 | One SQLite file per Workspace | user |
| G11 | Conversations are kept until deleted | user |
| G12 | The agent saves the client's view (Tabs, Log entries, Control values) | user |
| G13 | Settings changes only data-directory Datasets. Repo Datasets are read-only there | user |
| G14 | A Workspace may override `model`, `reasoning_effort`, `sql_timeout_ms` and `sql_row_cap` | user |
| G15 | A saved Level holds the model's reply before Macro expansion, never an expanded Surface | Claude (delegated), approved |
| G16 | A hash of each Result's rows is saved. A different hash on re-run marks the Level "data has changed" | Claude (delegated), approved |
| G17 | Results are re-run when their Tab is shown, not all on open | Claude (delegated), approved |
| G18 | A Board Tab whose Board changed shows "This Board has changed" and a button, not the old version | Claude (delegated), approved |
| G19 | On resume, the model sees each Result's SQL, columns and row count, not its rows | Claude (delegated), approved |
| G20 | The bootstrap server stays its own process. Only its UI moves into the client (ADR 0041) | Claude (delegated), approved |
| G21 | The saved model history (questions and replies, without rows) may be stored as plain text in the data directory | user |
| G22 | `explorer/bootstrap/ui/` is retired once Settings covers its steps. The bootstrap CLI stays | user |

The user delegated G15–G20 with "prefer simple solutions" and approved them (2026-11-04).

## Scope

**In, phase 1 (Workspaces and saved Conversations):**
- the Workspace list, the data directory and runtime mounting
- unavailable Workspaces
- saving and resuming Conversations
- the Workspace picker and the Conversation list in the client

**In, phase 2 (Settings):**
- the bootstrap workflow in the client, writing data-directory Datasets
- registering Workspaces and reloading them without a restart

**Out:**
- authentication or several users
- questions across Workspaces
- expiry of Conversations
- searching, renaming or exporting Conversations
- editing repo Datasets from Settings
- secrets entered in the browser
- watching Dataset files for changes (a reload picks them up)

## Phase 1: Workspaces and saved Conversations

### Modes

- **Single-Dataset mode** applies when `EXPLORER_DATA_DIR` is unset. Behaviour is exactly as today:
  `DATASET_DIR` and `DATABASE_URL`, the agent at the root URL, and nothing saved. Tests, `run.sh`
  and the harnesses keep working unchanged.
- **Workspace mode** applies when `EXPLORER_DATA_DIR` is set. The agent reads the Workspace list,
  mounts each available Workspace at `/w/<name>/`, and saves Conversations. `DATASET_DIR` and
  `DATABASE_URL` are ignored, and the agent logs a warning if they're set.

### Data directory

```
$EXPLORER_DATA_DIR/                 mode 0700, created if missing
  workspaces.toml                   the Workspace list
  workspaces/<name>/conversations.sqlite    mode 0600
  workspaces/<name>/dataset/        only for Datasets made in Settings (phase 2)
```

The directory must not be inside the repo. Startup refuses a path under the repo root, because
saved Conversations hold questions and model history.

### The Workspace list (`workspaces.toml`)

```toml
[[workspace]]
name = "austender"                       # [a-z0-9][a-z0-9_-]*, unique; the URL segment
dataset_dir = "explorer/datasets/austender"  # relative to the repo root, or absolute
dsn_env = "AUSTENDER_DATABASE_URL"       # a variable in the root .env, never the DSN itself
model = "meta/muse-spark-1.3"            # optional overrides (G14):
# reasoning_effort, sql_timeout_ms, sql_row_cap
```

- **Order:** the order in the file is the order shown in the picker. The first Workspace is the
  default.
- **Model rules:** an override is checked by the same rules as the global setting. For example,
  `model` must be Muse Spark or Muse Wonder.
- **Unknown keys** are a load error for that Workspace.
- **Writes:** phase 2's Settings is the only writer, and it writes atomically (temporary file,
  then rename). The agent only reads the file.

### Startup and reload

For each Workspace, in order:
1. Load the Dataset.
2. Read the variable named by `dsn_env`.
3. Open the pool.
4. Run the existing read-only privilege check.
5. Build the sub-app (`build_app` with `agent_card_url`, `rpc_url` and the card `url` all under
   `/w/<name>/`).

Any failure leaves the Workspace **unavailable**. It's listed, with a reason that never contains
the DSN:
- "database unreachable"
- "login can write" (that Workspace is refused outright, as single mode refuses today)
- "dataset invalid: …"
- "`AUSTENDER_DATABASE_URL` not set"

The other Workspaces start. If no Workspace is available, the agent still starts and serves
`/workspaces`.

A path-prefix router in front of the sub-apps dispatches `/w/<name>/…` by a dictionary lookup.
`POST /workspaces/reload` re-reads the list:
- it mounts new Workspaces and retries unavailable ones;
- it unmounts removed ones, but answers 409 while one of them has a model run in progress;
- it rebuilds a Workspace whose entry or Dataset files changed.

Reload answers with the new list.

### HTTP API (besides A2A)

| Route | Answer |
|---|---|
| `GET /workspaces` | `[{name, description, status, reason?, url}]` in list order |
| `POST /workspaces/reload` | the new list; 409 as above |
| `GET /w/<name>/conversations` | `[{id, title, updated_at, turns}]`, newest first |
| `GET /w/<name>/conversations/<id>` | the saved view: Tabs, Log entries, the selected Tab, Control values, and Level IDs |
| `GET /w/<name>/conversations/<id>/levels/<level>` | the Level rebuilt (below): `{prose, a2ui, changed, failed_results}` |
| `DELETE /w/<name>/conversations/<id>` | 204; 409 while it has a run in progress |
| `DELETE /w/<name>/conversations` | 204, all of the Workspace's Conversations; 409 while any is running |

The routes for an unavailable Workspace answer 503 with its reason.

**Request protection:**
- The agent binds to loopback.
- POST and DELETE need a matching `Host` and, for POST, a JSON body. That's the bootstrap server's
  existing protection.
- CORS is limited to the client's origin, as today.

### What is saved (G9, G15–G19)

There's one SQLite database per Workspace, in WAL mode. Writes run off the event loop, after the
reply has been sent, so saving never delays an answer.

| Saved | Contents | Never |
|---|---|---|
| Conversation | ID (the A2A `contextId`), title (the first typed question, cut to 80 characters), created and updated times | — |
| Result | Result ID, SQL, columns and kinds, row count, `truncated`, row hash (SHA-256 of the canonical JSON of the stored rows) | rows |
| Level | kind (answer, Drill-down, Ask, Board), the input it was built from: the model's reply text with its Macro calls, a Drill-down's Action, or a Board's name, key, Control values and the SHA-256 of its Board file | an expanded Surface |
| Model history | the messages, with every `rows_preview` and Board-pair row list replaced by `{"rows_omitted": true}`. Columns, row count and SQL are kept | rows |
| View | Tabs (order, closed, the selected one), Log entries, each Board Tab's Control values | — |

A Conversation is saved only once it has a typed question, or an Action beyond opening the
Explorer Board at its defaults. Otherwise every visit would leave an empty Conversation behind.

### Resuming

- **Open:** the client fetches the saved view and rebuilds Tabs and Log entries with no agent work.
  Then it fetches the selected Tab's Level. Other Tabs' Levels are fetched when they're first shown
  (G17).
- **Rebuilding a Level:**
  - For an answer, Ask or Drill-down Level, re-run its Results through the normal `SqlRunner`, with
    a read-only transaction, timeout, row cap and allowlist. Store them in the Conversation's
    `ResultStore` under their saved IDs, then compile and expand the saved reply as if it had just
    arrived. Action sources are registered again, so clicks work.
  - For a Board Level, render the Board through the normal Board path with the saved key and
    Control values.
- **Changed data (G16):** a re-run whose hash differs from the saved one sets `changed`. The client
  shows "The data has changed since this answer; its wording may be out of date." above the Level.
- **A query that no longer runs:** the Result is listed in `failed_results`, and each Macro that
  names it renders the error and SQL in its place. The rest of the Level renders.
- **A Board that changed (G18):** if the Board file's hash differs, or the Board is gone, the Tab
  shows "This Board has changed since." with a button that opens the current Board at its defaults.
- **Asking on (G19):** the client sends the saved `contextId`. When the agent doesn't hold that
  Conversation in memory, it loads it from the Workspace's store instead of starting a new one. Today
  that case is marked `unknown_context` and a new Conversation starts. The model gets the saved
  history, with rows omitted. A new question works as in any Conversation.
- **Memory:** the in-memory LRU (`MAX_CONVERSATIONS`) becomes a cache over the store. An evicted
  Conversation is reloaded on demand, not lost.

### Client

- **Workspace picker** in the header, shown only in Workspace mode. It's fed by `/workspaces`.
  Unavailable Workspaces are greyed out, with their reason and a Retry button (which calls reload).
  Switching Workspace works like New conversation in the target Workspace. Each Workspace has its
  own A2A client, from its card.
- **Conversations list:** a button in the Sidebar opens the list for the current Workspace: title,
  date, open, delete, and "Delete all" with a confirmation. It's hidden for an unavailable
  Workspace.
- **URLs:** `#/w/<name>` opens a Workspace, and `#/w/<name>/c/<id>` opens a saved Conversation.
  Reloading the page reopens the same Conversation.
- In single-Dataset mode, the client looks exactly as it does today.

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `EXPLORER_DATA_DIR` | unset | Workspace mode and the data directory (must be outside the repo) |
| `<NAME>_DATABASE_URL` | — | One per Workspace, named by its `dsn_env`, in the root `.env` |

They go into `explorer/.env.example` with placeholders.

## Phase 2: Settings

- **A Settings section** in the client (`#/settings`), shown in Workspace mode. It holds the
  bootstrap workflow:
  - connect, report, draft (with the cost ceiling, and a confirmation before any model call),
    review of claims and comments, Boards draft and review (the existing table preview), then
    export;
  - plus a list of Workspaces with delete and, for data-directory Datasets, regenerate.
- **The bootstrap server stays a separate process** (G20, ADR 0041) at `BOOTSTRAP_URL` (default
  `http://127.0.0.1:8765`). It adds CORS for the client's origin. `run.sh` starts it in Workspace
  mode. `explorer/bootstrap/ui/` is retired once Settings covers it, and the CLI stays.
- **Connections:** Settings lists the variables matching `*_DATABASE_URL` in the root `.env` that no
  Workspace uses yet, by name only. The bootstrap server connects with the one chosen, per draft,
  instead of the single `BOOTSTRAP_DATABASE_URL`, which stays for the CLI. Before drafting, it runs
  the agent's read-only privilege check and refuses a login that can write.
- **Export** goes to `$EXPLORER_DATA_DIR/workspaces/<name>/dataset/` (export's existing `--root`),
  with the comment target `toml`. Then:
  1. Settings appends the Workspace to `workspaces.toml` (atomic write; a duplicate name is refused).
  2. Settings calls the agent's `POST /workspaces/reload`.
  3. The new Workspace appears in the picker.
- **Regenerate** (data-directory Datasets only) re-runs draft and Boards, and exports with `force`
  into the same folder. Export keeps a backup. Then Settings reloads.
- **Delete:**
  - A data-directory Workspace: remove its list entry and its whole folder (Dataset and
    Conversations), after a confirmation that names what's deleted.
  - A repo-Dataset Workspace: remove the list entry and its Conversations. The repo files are never
    touched.
  - It's refused while the Workspace has a run in progress.
- **Repo Datasets** are listed read-only, with a hint to use `regen_dataset.sh`.
- **Models:** the bootstrap's own `BOOTSTRAP_MODEL` (Muse Wonder by default, or Spark), as in the
  bootstrap today.

## Files

| File | Change |
|---|---|
| `agent/src/explorer_agent/config.py` | `EXPLORER_DATA_DIR`, Workspace list loading, overrides (G14) |
| `agent/src/explorer_agent/workspaces.py` (new) | registry, startup checks, prefix router, reload |
| `agent/src/explorer_agent/saved.py` (new) | SQLite store: schema, save, load, delete, row hash |
| `agent/src/explorer_agent/executor.py` | load an unknown `contextId` from the store; save after each Level and Control change |
| `agent/src/explorer_agent/server.py` | `build_app` path options; conversation routes |
| `agent/src/explorer_agent/__main__.py` | Workspace mode start-up |
| `agent/src/explorer_agent/llm.py` | history with rows omitted on load |
| `client/src/workspaces.ts` (new), `app.ts`, `a2a.ts` | picker, Conversation list, hash routes, rebuilding saved Levels |
| `client/src/settings/` (new) | phase 2: the bootstrap views moved from `bootstrap/ui/src/` |
| `bootstrap/server/src/explorer_bootstrap/server.py` | phase 2: CORS, per-draft connection by `dsn_env`, privilege check, registry write |
| `explorer/.env.example`, `run.sh` | new settings; Workspace mode starts the bootstrap server |
| `explorer/SPEC.md` §1, `bootstrap/SPEC.md` | pointers to this spec, added on approval |
| `CONTEXT.md` | Workspace (added), Dataset and Conversation (changed); done in the grilling |

## Criteria → tests

`(db)` needs Docker. All model calls are stubbed.

| # | Criterion | Test |
|---|---|---|
| W1 | Without `EXPLORER_DATA_DIR`, nothing changes: the existing suite passes unchanged | whole suite |
| W2 | The Workspace list loads; a bad name, an unknown key, a duplicate or a disallowed `model` fails that Workspace only | `test_workspaces.py` |
| W3 | A data directory inside the repo is refused at startup | `test_workspaces.py` |
| W4 | Two Workspaces mount under `/w/<name>/`, each with its own card URL, pool, `prompt_cache_key` and byte-identical prefix; a question in one never reaches the other's database | `test_workspaces.py` (db) |
| W5 | A Workspace whose login can write, whose variable is unset or whose database is unreachable is listed as unavailable with a reason that holds no DSN; the others serve | `test_workspaces.py` (db) |
| W6 | Reload mounts a new Workspace, retries an unavailable one, unmounts a removed one, and gives 409 while a removed one is running | `test_workspaces.py` (db) |
| W7 | POST and DELETE refuse a foreign `Host` and a non-JSON POST | `test_workspaces.py` |
| P1 | A saved Conversation's file holds no row values: a sentinel value in the test rows never appears in the SQLite file | `test_saved.py` (db) |
| P2 | Opening the Explorer Board at its defaults saves nothing; a typed question saves the Conversation with its title | `test_saved.py` (stub model, db) |
| P3 | After an agent restart, a saved answer Level rebuilds to the same A2UI as before, with `changed` false | `test_saved.py` (stub model, db) |
| P4 | Changed rows set `changed`; a query that no longer runs is in `failed_results`, and its Macros show the error and the rest renders | `test_saved.py` (db) |
| P5 | A Board Level re-renders with its saved Control values; a changed or removed Board file gives "This Board has changed" | `test_saved.py` (db) |
| P6 | Asking on a restored `contextId` loads the Conversation (not `unknown_context`), and the model's history has `rows_omitted` in place of rows | `test_saved.py` (stub model, db) |
| P7 | Clicks on a rebuilt Level work: Drill-down and Entity Board Actions resolve | `test_saved.py` (stub model, db) |
| P8 | An LRU-evicted Conversation reloads from the store | `test_saved.py` (db) |
| P9 | Delete one, delete all, and 409 while running | `test_saved.py` (db) |
| P10 | Saving happens after the reply is sent: a slow store doesn't delay the final A2A event | `test_saved.py` (stub model) |
| C1 | Picker, unavailable entries with Retry, switching, the Conversation list, delete, and the hash routes | `client/test/workspaces.test.ts` |
| C2 | Reopening a saved Conversation rebuilds Tabs and Log entries, fetches only the selected Level, and fetches others on first show; the "data has changed" and "Board has changed" notices show | `client/test/workspaces.test.ts` |
| S1 | Phase 2: Settings lists only declared and unused `*_DATABASE_URL` names, and never a value | `bootstrap/server/tests/test_server.py` |
| S2 | Phase 2: a login that can write is refused before drafting | `test_server.py` (db) |
| S3 | Phase 2: export goes to the data directory, appends the list entry atomically, refuses a duplicate name, and the agent reload mounts it | `test_server.py` (stub model, db) |
| S4 | Phase 2: delete removes the list entry and the folder for a data-directory Workspace, and never touches a repo Dataset's files | `test_server.py` |
| S5 | Phase 2: the Settings views cover the bootstrap UI's steps plus Boards, and ask for confirmation before any model call | `client/test/settings.test.ts` |
| M1 | Manual: two Workspaces (austender, vicprop), switching, a restart, a resumed Conversation, and a vicprop Workspace added from Settings | manual |

## Review notes (2026-11-04)

- The user approved G15–G20, including G18's simple rule: a Board that changed in any way drops
  the saved Tab's old version, with no per-query diff.
- The user approved retiring `explorer/bootstrap/ui/` once Settings covers it (G22).

## Test command

```bash
cd explorer/agent && uv run pytest
cd explorer/client && npm test
cd explorer/bootstrap/server && uv run pytest     # phase 2
```
