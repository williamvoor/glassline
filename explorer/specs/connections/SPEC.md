# Connections: named read-only logins

Status: **approved** (2026-11-07, the user). Its decisions came out of a grilling session on 2026-11-07. Decision: ADR 0051 (accepted). Where this file is silent,
`specs/workspaces/SPEC.md` and `bootstrap/SPEC.md` apply. Loading files into a database is a
separate spec, `../../dataload/SPEC.md`.

## Why

Adding an existing Postgres database takes four READMEs today, plus a read-only role that no
doc explains how to create, plus an environment variable whose name is the only label. The user
wants one simple path and named database logins.

## Terms (`CONTEXT.md`)

A **Connection** is one named, read-only login to a Postgres database. Each distinct login is one
Connection, and several Datasets may share it. A Workspace names its Connection; a Dataset never
does.

## Decisions

| # | Decision | By |
|---|---|---|
| C1 | One list, `EXPLORER_DATA_DIR/connections.toml`, of `[[connection]]` entries with `name`, `label` and `url_env` | user |
| C2 | The URL stays in `.env`. The list names its variable | user |
| C3 | No notes and no tags on a Connection | user |
| C4 | A Workspace names its Connection (`connection = "<name>"`), replacing `dsn_env` | user |
| C5 | A set `*_DATABASE_URL` variable that no Connection names is offered in Settings, named after the variable | user |
| C6 | Settings migrates `dsn_env` Workspaces at startup, then reloads the agent. Until then the agent marks them unavailable | user |
| C7 | The bootstrap CLI takes `--connection NAME`, with `BOOTSTRAP_DATABASE_URL` as the fallback | user |
| C8 | The read-only role is set up with `dataload grant`, or with the SQL in the docs. The explorer never writes to a database | user |
| C9 | `label` defaults to the name. Deleting a Connection that a Workspace still names is refused | Claude, stated in the grilling, not objected to |
| C10 | No admin UI for loading or grants. Those stay in the CLI | user |
| C11 | The Connection that New Workspace is connected with can't be deleted either (refuter, 2026-11-07: its export would name a missing Connection) | Claude, from review |
| C12 | A legacy `dsn_env` that can't be a Connection's login (not `*_DATABASE_URL`, or `BOOTSTRAP_DATABASE_URL`) is left alone by the migration; the agent's reason says to rename the variable | Claude, from review |

## File format

```toml
# EXPLORER_DATA_DIR/connections.toml: written by Settings
[[connection]]
name    = "vicprop"                  # [a-z0-9][a-z0-9_-]*, unique
label   = "Victorian house prices"   # optional, 1–80 characters; default: name
url_env = "VICPROP_DATABASE_URL"     # [A-Z][A-Z0-9_]*_DATABASE_URL, unique, not BOOTSTRAP_DATABASE_URL
```

- Unknown keys are refused.
- A `url_env` that holds a URL, or any value containing `://`, is refused with "logins go in
  .env, not in the file".
- The name derived from a variable is its prefix, lower-cased, with `_` kept
  (`VICPROP_DATABASE_URL` → `vicprop`, `MY_DATA_DATABASE_URL` → `my_data`). If that name is
  taken, `_2`, `_3` and so on are appended.

## Behaviour

**Agent**
- `load_workspace_list` resolves `connection = "<name>"` through `connections.toml` to
  `url_env`, then reads the URL. A missing Connection, an unset variable or a malformed URL
  makes that Workspace unavailable with the reason (Workspaces G8).
- An entry that still has `dsn_env` is unavailable with "needs migration". An entry with both
  keys is refused.
- `WORKSPACE_KEYS` swaps `dsn_env` for `connection`. The agent never writes either list.

**Settings (bootstrap server and client)**
- **Startup migration:** before serving, the server reads `workspaces.toml`. For each `dsn_env`
  entry it finds or creates the Connection with that `url_env`, and rewrites the entry as
  `connection =`. It writes both lists atomically (Connections first), then triggers the agent's
  existing reload. This is idempotent: a second start changes nothing. If the variable isn't
  set, the migration still writes the Connection, and the Workspace shows as unavailable for the
  usual reason.
- **Database step:** lists the Connections by label only (the user's choice, 2026-11-07: no
  variable names on that page), then the unclaimed variables, named after their prefix and
  marked *new*. A Connection whose variable is unset is disabled and marked *not set*, and its
  tooltip names the variable to set (C5). It never returns a variable's value.
- **Invalid entries** (hand-edited) don't appear in Settings; the agent's reason for a Workspace
  that names one says what's wrong. Fixing them is a hand edit (deferred from review).
- **Connection actions:** rename the label, and delete a Connection that no Workspace names
  (C9, refused otherwise, with the Workspaces named). Both are whole-file atomic writes, like
  `registry.write_entries`. Confirmations are inline cards, never pop-ups.
- **Export and Regenerate** write `connection = "<name>"`. Choosing an unclaimed variable writes
  its Connection first.

**Bootstrap CLI**
- `report`, `draft`, `verify`, `boards` and `export` accept `--connection NAME`. The CLI reads
  `connections.toml` from `EXPLORER_DATA_DIR`, and the variable from the environment or `.env`.
  Without the flag, `BOOTSTRAP_DATABASE_URL` is used, as today.
- `regen_dataset.sh` is unchanged. It stays a maintenance script for the repo's own databases.

**Docs** (README rule: no ADR numbers)
- `explorer/bootstrap/README.md` gets one section, "Add an existing Postgres database":
  1. (Optional) load files with `dataload` (link).
  2. Create the read-only login: run `dataload grant`, or have an owner run the SQL below.
  3. Add `<NAME>_DATABASE_URL` to the root `.env`.
  4. Start the explorer. Settings offers the new variable. Pick it (that makes it a Connection),
     follow the New Workspace steps, and rename its label in the Connections list.
  The root README and the explorer README link to this section and don't repeat it.
- The SQL, for an owner who isn't using `dataload grant`:
  ```sql
  CREATE ROLE explorer_ro LOGIN PASSWORD '…';
  GRANT CONNECT ON DATABASE <db> TO explorer_ro;
  GRANT USAGE ON SCHEMA <schema> TO explorer_ro;
  GRANT SELECT ON ALL TABLES IN SCHEMA <schema> TO explorer_ro;
  ALTER DEFAULT PRIVILEGES FOR ROLE <owner> IN SCHEMA <schema> GRANT SELECT ON TABLES TO explorer_ro;
  ```
- `explorer/.env.example` and `deploy/workspaces.toml` move to the new form.
  `deploy/workspaces.toml` gets a sibling, `deploy/connections.toml`.

## Out of scope

- An admin UI for loading files, owner logins or grants (C10).
- Secrets in files or entered in the browser.
- Notes or tags on Connections (C3).
- `regen_dataset.sh` reading Connections.

## Acceptance (tests to write with the build)

| # | Check |
|---|---|
| A1 | `connections.toml` validation: a valid list loads. Each refused case (unknown key, URL-looking value, bad or duplicate name, duplicate or `BOOTSTRAP_` `url_env`, bad label) fails with its reason |
| A2 | A Workspace with `connection` resolves its URL. An unknown Connection or an unset variable makes it unavailable with the reason, and the other Workspaces still mount |
| A3 | A `dsn_env` entry is unavailable with "needs migration", and an entry with both keys is refused |
| A4 | Startup migration: `dsn_env` entries become `connection =`, and Connections are created or reused. Running it twice changes nothing. A failure mid-write leaves both files as they were |
| A5 | Settings lists Connections plus unclaimed variables, and never returns a value. Deleting a Connection in use is refused, with the Workspaces named |
| A6 | Export and Regenerate write `connection =`, and choosing an unclaimed variable creates its Connection |
| A7 | CLI `--connection` uses that Connection's variable. Without it, `BOOTSTRAP_DATABASE_URL` is used. An unknown name fails with the known names listed |
| A8 | The prompt prefix is unchanged by this work (the existing snapshot hashes don't move) |

## Build order

1. Agent: `connections.toml` loading, `connection =` resolution and "needs migration" (A1–A3, A8).
2. Bootstrap server: startup migration and the Settings list and actions (A4–A6), plus the
   client's Database step.
3. CLI `--connection` (A7).
4. Docs, `.env.example` and the deploy files.
5. `dataload grant` (in `dataload/`), which the docs then link to.
