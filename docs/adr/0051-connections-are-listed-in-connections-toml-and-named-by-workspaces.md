---
status: accepted (2026-11-07, the user)
---

# Connections are listed in connections.toml and named by Workspaces

Today a database login is only an environment variable named `<NAME>_DATABASE_URL`, and each
Workspace names its variable (`dsn_env`). The variable's name is the only label there is, and
nothing says which Workspaces share a login.

A **Connection** is one named, read-only login, and each distinct login is one Connection.
Several Datasets, and so several Workspaces, may share it. Connections are listed in
`EXPLORER_DATA_DIR/connections.toml`:

```toml
[[connection]]
name    = "vicprop"
label   = "Victorian house prices"   # optional; defaults to the name
url_env = "VICPROP_DATABASE_URL"     # the URL stays in .env
```

- **The login stays in the environment.** The file names a variable and never holds a URL.
  Two Connections may not name the same variable.
- **A Workspace names its Connection** (`connection = "vicprop"`). Datasets never name a
  Connection, so a committed Dataset works on any deployment.
- **A set `*_DATABASE_URL` variable that no Connection names is still offered** in Settings, named
  after the variable. Choosing it writes the Connection.
- **Settings migrates old Workspaces at startup.** Each `dsn_env` entry becomes `connection =`,
  and any missing Connection is created. Settings then reloads the agent. The agent treats an
  unmigrated entry as unavailable ("needs migration"), so Settings stays the only writer of the
  data directory's lists.
- **The bootstrap CLI takes `--connection NAME`**, with `BOOTSTRAP_DATABASE_URL` as a fallback.

The Connection carries no notes and no tags. Text for the model stays in the Dataset.

## Considered Options

- **Environment variables only.** Nothing to build, but no label and no explicit sharing.
- **One file per Connection** (`connections/<name>.toml`). Room to grow, but a directory of
  two-line files is overkill. The draft version of this ADR also had notes and tags; both were
  dropped.
- **The link in `dataset.toml`.** It reads naturally ("this Dataset belongs to that Connection"),
  but it ties committed Datasets to one deployment's Connection names.
- **Keep accepting `dsn_env` forever.** No migration, but two code paths in the agent for good.
- **The agent migrates the list itself.** No window where a Workspace is unavailable, but the
  agent would become a second writer of the Workspace list.
