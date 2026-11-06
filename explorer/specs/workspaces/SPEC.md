# Workspaces: several databases, saved Conversations, Settings

Status: **approved** (2026-11-04, the user). Phase 1 built (2026-11-04) and phase 2 built (2026-11-05); see the "Build decisions" sections. M1 passed (2026-11-05), and `bootstrap/ui/` is retired (G22, D8). It came out of a grilling session with the
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

*Superseded by "One mode" (2026-11-06): only Workspace mode remains.*

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
dataset_dir = "datasets/austender"  # relative to the repo root, or absolute
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
| M1 | Manual: two Workspaces (austender, vicprop), switching, a restart, a resumed Conversation, and a vicprop Workspace added from Settings | manual: **passed 2026-11-05** (Playwright, real databases and models; fixes under "M1 fixes") |

## Review notes (2026-11-04)

- The user approved G15–G20, including G18's simple rule: a Board that changed in any way drops
  the saved Tab's old version, with no per-query diff.
- The user approved retiring `explorer/bootstrap/ui/` once Settings covers it (G22).

## Build decisions, phase 1 (2026-11-04, Claude, while building)

These settle points the approved text leaves open. Where one reads differently from the text
above, it is a deliberate amendment, listed here.

- **B1 Routing.** The prefix router strips `/w/<name>` before calling the sub-app, so each sub-app
  keeps its A2A routes at `/` and `/.well-known/agent-card.json`. Only the card `url` carries the
  prefix. `build_app` gains `cors` (off for sub-apps: one CORS middleware wraps the router, since
  two would duplicate the header) and `saved` (the conversation routes, below). That replaces the
  `agent_card_url` and `rpc_url` options named in "Startup and reload" step 5.
- **B2 Per-Workspace settings.** A Workspace's Settings come from `load_settings` over the process
  environment, with `DATABASE_URL` taken from `dsn_env`, `DATASET_DIR` set to the absolute Dataset
  path, and the G14 overrides applied. `prompt_cache_key` is `explorer-<workspace name>`, or
  `<PROMPT_CACHE_KEY>-<workspace name>` when that variable is set, so two Workspaces never share one.
- **B3 Model rule.** The global `MODEL` has no allowlist in code today, and W1 forbids changing
  single-Dataset mode. A Workspace `model` override must be `meta/muse-spark-1.3` or
  `meta/muse-wonder-1.0`. `reasoning_effort` must be a non-empty string, and the two SQL limits
  must be positive integers.
- **B4 Reasons.** Reasons are fixed strings, plus the `ConfigError` text for "dataset invalid: …".
  That text names files and keys, never env values. A login with superuser, CREATEROLE or write
  rights gives "login can write". A malformed DSN gives "`<dsn_env>` is malformed".
- **B5 Reload.** It also answers 409 while a Workspace whose entry or Dataset files *changed* is
  running, not only a removed one, because rebuilding drops its in-memory Conversations. Change
  detection compares the entry and a SHA-256 over the Dataset directory's files.
- **B6 Level IDs.** A Level is named `<turn>-<level>` (Turn 0 is the Explorer Board). Saved Levels
  keep their surface IDs and the Conversation keeps its surface tag. The client starts a fresh
  processor on open, so no surface ID clashes.
- **B7 What a Level saves.**
  - **Answer or Ask:** the model's final reply text, the Drill Visuals it ignored, and the Result
    IDs the reply names. These are the `r<n>` tokens in the reply that the Conversation held when
    it was built, including pinned Board Results.
  - **Give-up:** a reply that never passed the checks (step limit, timeout, model failure) saves
    no reply text. It reopens as "This answer couldn't be completed when it was asked."
  - **Drill-down:** the Drill query, its view arguments, its title and its bound values.
  - **Board:** the Board name, the key, the wire-form Control values and the file hash.
  - **Saved as view text:** a Drill-down's bound values and an Entity Board's key identify the
    clicked row. They're saved like the tab title that already shows them (G12). P1 is about the
    rows of Results, which are never saved.
- **B8 Results.** A Result row is inserted once (IDs never repeat), with the hash of the rows it had
  then. A re-run compares against that first hash. A re-run Result goes into the store under its
  saved ID (`ResultStore.put`), and the ID counter moves past it.
- **B9 Re-run Results the model names.** On a resumed Conversation, when a final reply names a
  saved Result the store no longer holds, that Result is re-run before the reply is built. This
  avoids a Repair that can't run SQL.
- **B10 Rebuild leniency.** A rebuild uses the Board-mode rule for Macros: a Macro whose data no
  longer fits (a row index past the end, negative bars) draws "This panel couldn't be drawn from
  its rows." A Result whose query fails draws "This query no longer runs: <reason>" with its SQL.
  A prose `Cite` of a failed Result shows "—". A rebuild never raises a Repair.
- **B11 Level response.** `GET …/levels/<level>` answers
  `{prose, a2ui, changed, failed_results, board_changed, board, tab}`:
  - `board` is `{id, controls}` for a Board Level, as `explorerBoard`;
  - `tab` is the saved tab `{id, kind, title}`;
  - `board_changed` is G18's flag, and then `a2ui` is empty.
  - `POST …/levels/<level>/defaults` (a JSON POST, so the guard covers it) renders the current
    Board at its defaults in place of the saved Level, and saves it. That's the button behind
    "This Board has changed since."
- **B12 View.** The agent saves Tabs and Log entries (tab entries and in-place entries, in order)
  and each Board Level's Control values. It can't see closed Tabs or the selected Tab, so the client
  sends them as message metadata `explorerView: {closed: [tab ids], selected: tab id}`. The agent
  saves the latest. A close or select not followed by a message isn't saved. Saving a view also
  bumps `updated_at`.
- **B13 When it's saved.** From the first typed question, or the first Action other than
  `open_board`, every completed Level, Control change and view update writes the whole
  Conversation (one transaction, on one writer thread per Workspace). The write is scheduled after
  `updater.complete`.
- **B14 Loading.** `Conversations.get_or_load(id)` checks memory, then the store (off the event
  loop). A loaded Conversation has its history, counters, Tabs, Levels and surface map back, with
  no Results and no action sources. Those come back as each Level is rebuilt. A context ID neither
  place holds still starts a new Conversation marked `unknown_context`.
- **B16 Request protection (refuter review).** The Host check covers every method, not only POST and
  DELETE, because the GET routes return saved questions and re-run rows. That matches the bootstrap
  server's guard, which the approved text names. Only POST, PUT and PATCH also need a JSON body.
- **B17 Saved errors.** A tool error in the saved history keeps its SQLSTATE only, with the message
  "the error message is not saved". Postgres quotes row values in its messages, and P1 covers
  them.
- **B18 Board Control drift.** A Board Level also saves the option values its choice and multi
  indexes named (`shown`). If the current options give other values for those indexes, the Level is
  a "Board has changed" (G18), not a silent pick of another option.
- **B15 Single-Dataset mode** *(removed 2026-11-06, see "One mode")* builds no store and no routes, and the client shows no picker. Its
  code path is the same `build_workspace` with `saved=None`.
  - **Client mode:** the client's mode is set at build time by `VITE_WORKSPACES=1`. `run.sh --real`
    sets it when `EXPLORER_DATA_DIR` is in the environment or the root `.env`. Without it the
    client never calls `/workspaces`, so single-Dataset mode makes no extra request.
  - **In-place entries:** a saved in-place Log entry carries `on_tab`, its Board's tab ID, so the
    reopened log selects the right tab.

## Build decisions, phase 2 (2026-11-04, Claude, while building)

- **D1 Settings mode.** The bootstrap server runs in Settings mode when `EXPLORER_DATA_DIR` is set.
  It then starts with no database connection. `BOOTSTRAP_DATABASE_URL` is used only without
  `EXPLORER_DATA_DIR` (the CLI and the old `serve`), which is unchanged.
- **D2 Connections.** `GET /api/connections` lists the names of `*_DATABASE_URL` variables (from
  the process environment plus the root `.env`) that no Workspace's `dsn_env` uses. It never lists
  a value. `POST /api/connect {dsn_env}` opens that login.
  - It runs the agent's read-only check over every schema the login can see. A login that can
    write, a superuser or a login with CREATEROLE is refused with 403 (S2).
  - Starting a draft runs the same check again, over the report's schemas.
  - Connecting replaces any earlier connection and clears the report, draft and Boards.
- **D3 Export in Settings mode.**
  - **Name:** the export `name` is the Workspace name. It must match the export rule
    (`[a-z][a-z0-9_]*`), which also satisfies the Workspace rule.
  - **Target:** the comment target is always `toml`.
  - **Files:** they go to `$EXPLORER_DATA_DIR/workspaces/<name>/dataset/` (`export_dataset` with
    root `workspaces/<name>` and name `dataset`).
  - **Registering:** the entry `{name, dataset_dir (absolute), dsn_env}` is appended with an atomic
    write. A name already in the list is refused with 409 before anything is written, unless this
    is a regenerate.
  - **Reload:** the server then calls the agent's `POST /workspaces/reload` at
    `http://127.0.0.1:<AGENT_PORT>`. The answer says whether the reload worked. A failed reload
    doesn't undo the export, and Retry in the picker reloads again.
- **D4 Workspaces in Settings.** `GET /api/workspaces` lists the list's entries with
  `kind: "data"` (the Dataset is under the data directory) or `"repo"`. Deletion is
  `POST /api/workspaces/<name>/delete` with a JSON body, because the guard needs JSON on every
  non-GET.
  - The server removes the entry (atomic rewrite), then reloads the agent. On 409 it puts the entry
    back and answers 409 (a run in progress).
  - Then it removes `$EXPLORER_DATA_DIR/workspaces/<name>/`. For a repo Dataset that folder holds
    only the Conversations. The path is resolved and must sit inside `workspaces/`, so a repo
    Dataset's files are never touched.
  - Rewriting the list drops comments in it. Settings is its only writer.
- **D5 Regenerate.** `POST /api/workspaces/<name>/regenerate` (data-directory Datasets only)
  connects with the entry's `dsn_env` and reports on that Dataset. It marks the session so the next
  export goes to the same folder with `force` (with a backup), without appending to the list. The
  usual draft, review and Boards steps run in between, then the agent is reloaded.
- **D6 Client.**
  - **Location:** Settings is the `#/settings` route, opened by a "Settings" button in the header
    (Workspace mode only). The bootstrap views are copied into `client/src/settings/` and talk to
    `VITE_BOOTSTRAP_URL` (default `http://127.0.0.1:8765`).
  - **Workspaces screen:** it adds a first screen, which lists the Workspaces with Delete (after a
    confirmation naming what is deleted) and, for data-directory Datasets, Regenerate. It also has
    "Add a Workspace", which offers the unused connection names.
  - **Model calls:** starting a Dataset draft or a Boards draft asks for confirmation first,
    naming the model and the cost ceiling (S5).
  - **After export or delete:** the picker is refreshed.
- **D7 CORS.** The bootstrap server allows the client's origin (`CLIENT_ORIGIN`, default
  `http://127.0.0.1:5173`), outside its guard, as the agent does (B1).
- **D10 Review fixes (phase 2 refuter, 2026-11-05).**
  - **Names:** the approved text's `BOOTSTRAP_URL` is `VITE_BOOTSTRAP_URL` in the client, set by
    `run.sh` from `BOOTSTRAP_PORT` (default 8765).
  - **Data directory:** `EXPLORER_DATA_DIR` must be absolute (or start with `~`), because the agent
    and the bootstrap server run from different directories.
  - **Delete:** the folder is deleted only when the agent's reload answers 200, or the agent isn't
    running. Any other answer puts the entry back and deletes nothing.
  - **Reload:** Settings has "Reload the agent" (`POST /api/reload`), the retry for an export
    whose reload failed. The picker's Retry covers only unavailable Workspaces.
  - **Settings element:** once opened, it stays mounted (hidden) while closed, so a draft
    survives switching back to the explorer.
  - **Regenerate:** the target is cleared by any new connection and set only by a regenerate that
    succeeds. Its "Re-run report" regenerates again.
- **D9 Test files.**
  - **Server:** S1–S4 live in `bootstrap/server/tests/test_settings.py`, not `test_server.py`. S3's
    reload runs a real agent Workspace registry in process.
  - **Client:** S5 is `client/test/settings.test.ts`. The copied views keep their own tests, ported
    from `bootstrap/ui/test/` to `client/test/settings/`.
- **D8 Retiring `bootstrap/ui/`.** `run.sh` starts the bootstrap server in Workspace mode. The old
  UI was removed on 2026-11-05, after M1 passed and the user confirmed. The bootstrap server no
  longer serves static files: `/` answers with a pointer to the explorer's Settings, and the API
  and the CLI are unchanged.

## M1 fixes (2026-11-05, Claude, after the manual check)

M1 was run with Playwright against both real databases and real models: two Workspaces, vicprop
added and regenerated from Settings (draft and Boards), switching, a restart and resumed
Conversations. It passed. These changes fix what it found.

- **M1a Saved Result hashes.** Whole `numeric` values are now stored as integers (see
  `specs/names-periods-currency/SPEC.md`, amendment 2026-11-05). `row_hash` hashes a `numeric`
  or `decimal` column's integers as floats, as they were stored before, so a Conversation saved
  earlier doesn't reopen as "the data has changed". `row_hash` takes the Result's columns and
  types for this.
- **M1b Boards in Settings.**
  - `GET /api/boards` answers 200 with `null` before any Boards, not 404 (no console error).
  - Once Boards exist, the Boards step has "Continue to Export". It returns to Review's Export
    section. Review stays mounted (hidden) on the Boards step, because its Workspace name and
    dismissed claims live only in the component until Export.
  - The Boards text no longer points to `./run.sh --real`.
- **M1c Logging.** `serve` logs the `explorer_bootstrap` INFO lines (each model call with its
  cost, exports, deletes) to stderr, once per process. The CLI commands keep their own output.
- **M1d Client.** A selected Workspace or Settings button is inverted (readable in both themes).
  Settings' tab strips are one row.
- **M1e Row labels.** Boolean columns are never a row's label, so an Entity Board opened from a
  row whose only text column is its key is titled with the key's value, not `false · false`.
  Default `DataTable` columns still show boolean columns.

## Phase 3: New Workspace wizard (amendment 2026-11-05, agreed with the user in a grilling)

**Why.** The phase 2 steps share one step bar with the Workspace list, and only "Workspaces" in
that bar can be clicked. Export sits inside Review, so the real order is Review → Boards → back to
Review → Export. You can't step back except through one-off buttons. The terms **Draft**, **New
Workspace** and **Regenerate** are in `CONTEXT.md`. "Bootstrap" stays the name of the server and
the CLI only, and never appears in the UI.

**No pop-ups.** The client uses no `window.confirm` and no modal dialogs. Each confirmation is an
inline card in the flow. This covers every confirmation in the client, including the explorer's
"Delete all saved conversations".

### Settings (`#/settings`)

- Settings is only the Workspace list, with Regenerate (data-directory Datasets), **Reload the
  agent**, and Delete.
- **Delete** opens a card inside that Workspace's row. The card names what is deleted, with
  **Delete** / **Cancel**.
- A **New Workspace** button opens the wizard. The explorer's "No Workspaces yet" screen opens the
  wizard directly.

### The wizard (`#/settings/new`)

It has its own step bar: **1 Database → 2 Draft → 3 Review → 4 Boards → 5 Export**.

1. **Database.** Pick an unused connection, test it, and choose its schemas. **Next** runs the
   report. The report is SQL only, with no model call.
2. **Draft.**
   - The step shows the report, the model and the effort.
   - Its button carries the ceiling: **Draft (at most $X)**, with the cost card (first call and run
     ceiling, the "these are ceilings" wording) always visible above it. One click starts the run.
     That satisfies S5 with no pop-up.
   - Progress shows on this step. When the Draft is done, the wizard moves to Review by itself.
3. **Review.** The step shows the files, comments and claims. Its **Next** saves pending edits (as
   `leaveForBoards` does today) and is disabled while the files don't validate or claims block.
   The blockers are listed on this step.
4. **Boards.**
   - **Draft Boards (at most $X)** works as step 2 does. The wizard stays here after the run,
     because the preview is the review.
   - **Skip Boards** goes to Export with no Boards.
   - In a Regenerate the button is **Keep current Boards** instead. The kept Boards are checked
     against the new Draft with the existing Board check, and their problems block Next like a
     drafted Board's do. Export already keeps `boards/` when no new Boards are sent.
   - Board problems are listed on this step and disable Next.
5. **Export.**
   - The step has the Workspace name field. In a Regenerate it shows instead "Regenerating
     **\<name>** (replaced files are backed up)".
   - The only blocker left at this step is an invalid name.
   - Success shows a card: "**\<name>** is ready", with **Open \<name>** (switches the explorer to
     it) and **Back to Workspaces**. If the agent didn't reload, the card says so and offers
     **Reload the agent**.

**Navigation.**
- Visited steps in the step bar are clickable, and looking back costs nothing.
- Some upstream changes discard later work: another connection, other schemas, a new Draft, or new
  Boards over edited ones. Before any of them, a warning card on that step names what is lost (for
  example, "This replaces the Draft and its Boards. Your edits are lost.") and asks for confirmation.
- Review's "Re-run report" and "Boards" buttons are removed, as are Report's "Back to the draft"
  and Boards' "Back to Review" and "Continue to Export". The step bar and Back/Next replace them.

**Regenerate** opens the wizard at step 2. Step 1 is shown done and locked, and the report is
already loaded.

**Discard and resume.**
- The wizard's **Discard Draft** (in place of Cancel) shows a warning card when a Draft exists,
  because it cost money. Without a Draft it just returns to Settings.
- Leaving for the explorer and coming back resumes at the same step, because the Settings element
  stays mounted (D10).
- After a browser reload, the wizard rebuilds itself from the server. It resumes at the furthest
  step the server's state supports. The Review step's typed name and dismissed claims live only in
  the client, so a browser reload loses them.
- If the bootstrap server restarted, the wizard starts at step 1 with the note "The server
  restarted, so the Draft is gone."

**Server additions.**
- `GET /api/session` answers `{dsn_env, report, regenerating, draft: bool, boards: bool}`, with no
  DSN values. Every field is null or false when nothing is held.
- `POST /api/discard` clears the report, Draft, Boards and the Regenerate target. It answers 409
  while a run is in progress.

### Models on show

The user wants every model call to be visible, to show how each model is used.

- **Identity.**
  - Each allowed model has a display name (Muse Wonder, Muse Spark) and its own accent colour.
    The colours are theme tokens for both themes.
  - The mark is a coloured dot and the name, with no vendor logos. The raw model ID is in the
    chip's tooltip.
  - The same chip is used everywhere: the wizard, the run cards and the explorer's Turns. An
    unknown model ID gets a neutral chip showing the ID.
- **Wizard step bar.** Each step is tagged with what it uses: **SQL** for Database, Review and
  Export, or the chosen model's chip for Draft and Boards.
- **Live run card** (Draft and Boards), fed by the run's `call` events:
  - the model chip pulses while the run is going;
  - a call counter;
  - a token bar per call, with the cached part shaded lighter;
  - the running cost against the ceiling ("$0.08 of at most $1.90").
- **Run summary.** When a run is done, its card collapses to one line kept on that step, for
  example "Muse Wonder · 7 calls · 81k tokens, 62% cached · $0.11".
- **Explorer Turns.**
  - The Turn's status line shows the model chip in place of the raw ID. A Turn with no model
    (Drill-down) shows an **SQL** chip.
  - A saved Level keeps the model name it was answered with, so a reopened Turn shows the chip
    too. Levels saved before this change show no chip.

### Look

- **Glassline glass.** Translucent frosted cards sit over a soft gradient backdrop, built from the
  existing theme tokens (ADR 0043 keeps the look in the client), in both themes.
- **Motion.** Steps slide and fade into each other, and the run cards animate. All motion is off
  under `prefers-reduced-motion`.
- **Build and check.** The work is built with the frontend-design skill and checked with
  Playwright screenshots of each step in both themes before it is called done.

**Removed.** Review's non-Workspace export branch (the "Comments go to" choice and the `force`
checkbox) is dead code in Settings and goes.

### Build decisions, phase 3 (2026-11-05, Claude, before building; ADR 0044)

- **E1 Routes.**
  - `#/settings` shows the list, and `#/settings/new` shows the wizard.
  - `<explorer-settings>` has a `view` (`list` or `wizard`). It fires `settings-view`, and the app
    writes the hash.
  - The wizard element stays mounted (hidden) under the list, so its state survives the list and
    the explorer.
- **E2 Wizard element.** It is `<explorer-wizard>` (`client/src/settings/wizard.ts`), which holds
  the flow state. Step 1 is the connection picker plus `bs-connect`.
- **E3 Review.** `bs-review` loses its Export section (name, target, force, Export, Re-run report,
  Boards). It fires `review-blockers` with its own blockers: validation and claims, as
  `reviewBlockers()`. It exposes `leave()` (save pending edits, true when saved) and
  `exportPayload()` (files, comments, dismissed). It stays mounted (hidden) from Review to Export.
- **E4 Boards.** `bs-boards` loses "Back to Review" and "Continue to Export". Skip Boards and Keep
  current Boards are the wizard's own buttons.
- **E5 Report invalidates.** `POST /api/report` now clears the server's Draft and Boards. A new
  report makes them stale. Only Settings calls that route.
- **E6 Keep current Boards.** `regenerate` remembers the Workspace's `boards/*.toml`.
  `POST /api/boards/keep` loads them into the Boards state, with the `why` "Kept from the current
  Workspace.", and checks and previews them against the Draft, as a validate does. It answers 404
  when there's no Draft or nothing to keep.
- **E7 Session.** `GET /api/session` answers
  `{dsn_env, report, regenerating, draft, boards, run}`:
  - `draft` and `boards` are booleans;
  - `run` is `{id, kind}` while a run is in progress, else null;
  - `report` is the report itself, so the Draft step can be rebuilt.
- **E8 Discard.** `POST /api/discard` clears the report, Draft, Boards and the Regenerate target.
  It keeps the connection, and answers 409 during a run.
- **E9 Models.** The Boards state also carries `model` (the run's model). A kept set has
  `model: null`.
- **E10 Saved model.** An answer Level's record keeps `model`, and a Drill-down's keeps `null`. The
  Level response (B11) carries `model` only when the record has the key, so Levels saved earlier
  show no chip. `STATE_VERSION` stays 1, because the key is optional.
- **E11 Chips and cards.** These are template helpers, not elements:
  - `modelChip(id)` in `client/src/model-chip.ts`;
  - `confirmCard(...)` in `client/src/cards.ts`;
  - `runCard(...)` in `client/src/settings/run-card.ts`.
  The token colours are `--model-wonder` and `--model-spark`.
- **E13 Regenerate over work in progress (refuter).** While a New Workspace is in progress (a
  login, report or Draft held, or a run going), the list's Regenerate is disabled, with the hint
  "Finish or discard the New Workspace first", and its button reads "Continue the New Workspace".
  A Regenerate replaces the wizard's state only once the server has answered.
- **E14 Ceilings are never stale (refuter).** A Draft button shows a ceiling only when the estimate
  is for the model now selected. Only the newest estimate request's reply is kept.
- **E15 Review edits recheck the Boards (the user, 2026-11-05).** Leaving Review saves its edits.
  This happens through Next or through the step bar. Arriving on the Boards step with Boards held
  then rechecks and re-previews them against the saved files (`POST /api/boards/validate` with the
  current Board files). So the Boards step never shows checks from before an edit.
- **E16 Left as they are (the user, 2026-11-05).**
  - There is no "Remove Boards": held Boards are fixed or drafted again.
  - The step bar may jump past Review's blockers. Export's server-side check (it re-validates the
    files and claims) is the backstop.
- **E12 Test files.** N16 (reduced motion) is in `client/test/index-html.test.ts`, because the rule
  lives in `index.html`. N10's server half is in `bootstrap/server/tests/test_settings.py`.

### Criteria → tests (phase 3)

| # | Criterion | Test |
|---|---|---|
| N1 | Settings lists Workspaces only; New Workspace opens `#/settings/new`; "No Workspaces yet" opens the wizard | `client/test/settings.test.ts`, `app.test.ts` |
| N2 | The step bar has the five steps; visited steps are clickable, unvisited ones aren't | `client/test/settings/wizard.test.ts` |
| N3 | Database's Next runs the report and lands on Draft; no model call happens before a Draft button press | `wizard.test.ts` |
| N4 | The Draft and Draft Boards buttons show the run ceiling; one click starts the run; a finished Draft moves to Review | `wizard.test.ts` |
| N5 | Review's Next is disabled by validation errors and blocking claims, and saves pending edits before leaving | `wizard.test.ts` |
| N6 | Boards: Skip Boards reaches Export with no Boards; in a Regenerate, Keep current Boards checks the kept Boards and their problems block Next | `wizard.test.ts`, `bootstrap/server/tests/test_settings.py` |
| N7 | A change that discards later work shows a warning card first; Cancel on the card changes nothing | `wizard.test.ts` |
| N8 | Regenerate opens at step 2 with step 1 locked; Export shows the fixed name | `wizard.test.ts` |
| N9 | Export success shows Open \<name> (switches Workspace) and Back to Workspaces; a failed reload shows Reload the agent | `wizard.test.ts`, `app.test.ts` |
| N10 | `GET /api/session` reflects the held state and never a DSN; `POST /api/discard` clears it and gives 409 during a run; after a server restart the wizard starts at step 1 with the note | `test_settings.py`, `wizard.test.ts` |
| N11 | No `window.confirm`, `alert`, `prompt` or `<dialog>` anywhere in `client/src`; Delete Workspace and Delete all saved conversations use inline cards | `client/test/model-chip.test.ts`, `settings.test.ts`, `workspaces.test.ts` |
| N12 | A model chip renders the display name and token colour for each allowed model, and a neutral chip with the raw ID for an unknown one | `client/test/model-chip.test.ts` |
| N13 | The step bar tags Database, Review and Export as SQL, and Draft and Boards with the chosen model | `wizard.test.ts` |
| N14 | The run card counts the `call` events, sums tokens and cost against the ceiling, and collapses to the summary line when the run is done | `wizard.test.ts` |
| N15 | A Turn shows its model chip, or SQL for no model; a saved Level keeps the model, and a reopened Turn shows the same chip | `app.test.ts`, `agent/tests/test_saved.py` (db) |
| N16 | Motion is off under `prefers-reduced-motion` | `client/test/index-html.test.ts` |
| N17 | A new report clears the server's Draft and Boards (E5) | `test_settings.py` |
| N19 | After Review edits, arriving on Boards (by Next or the step bar) saves the edits, then rechecks the held Boards; their new problems block Next (E15) | `wizard.test.ts` |
| N18 | Regenerate is disabled while a New Workspace is in progress, and a ceiling for another model never shows (E13, E14) | `settings.test.ts`, `report.test.ts` |
| M2 | Manual: create a Workspace and regenerate one end to end through the wizard against a real database (stub or real model), with screenshots of each step in both themes | manual |

## One mode (amendment 2026-11-06, the user; ADR 0048)

The explorer was deployed without Settings because the client was built without
`VITE_WORKSPACES=1`. Single-Dataset mode is removed so that can't happen again. This replaces
"Modes" in phase 1 and B15.

- **O1 Client.** No build-time mode. The client always loads `/workspaces`, shows the picker,
  saves Conversations and offers Settings. `VITE_WORKSPACES` is not read.
- **O2 Agent.** `python -m explorer_agent` requires `EXPLORER_DATA_DIR`; without it, it exits 1
  with `explorer: EXPLORER_DATA_DIR is required …`. `DATASET_DIR` and `DATABASE_URL` are not
  read. `load_settings` takes the Dataset directory and login as arguments.
- **O3 Dev tools.** `python -m explorer_agent boards <workspace>` and
  `scripts/scenarios.py run|boards --workspace <name>` use that Workspace's entry. An unknown
  name exits 1 naming it.
- **O4 run.sh.** It always starts the real agent, the bootstrap server and the client, and refuses
  without `EXPLORER_DATA_DIR`. No stub mode, no `--real`. Its callers (`run_demo.sh`,
  `regen_dataset.sh`, the bootstrap server's help text) say plain `run.sh`.
- **O5 Stub server.** Test-only. It serves `GET /workspaces` with one Workspace, `stub`, and the
  A2A agent at `/w/stub/`, and prints `READY <workspace URL>`.

| ID | Criterion | Test |
|---|---|---|
| O1 | The app lists Workspaces with no build flag | `workspaces.test.ts`, `app.test.ts` |
| O2 | No `EXPLORER_DATA_DIR`: one-line failure; `DATASET_DIR` alone doesn't start an agent | `test_main.py` |
| O3 | `boards <workspace>` checks that Workspace; an unknown name fails | `test_main.py` |
| O4 | `run.sh` takes no flags and refuses without `EXPLORER_DATA_DIR`; no `VITE_WORKSPACES` in the client, `run.sh` or `deploy/` | `test_run_sh.py` |
| O1 | Retry on the banner reloads the Workspace list when it failed to load | `workspaces.test.ts` |
| O5 | The stub lists `stub` and serves its card under `/w/stub/` | `test_stub_server.py`, `interop.test.ts` |

## Test command

```bash
cd explorer/agent && uv run pytest
cd explorer/client && npm test
cd explorer/bootstrap/server && uv run pytest     # phase 2
```
