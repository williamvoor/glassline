---
status: accepted (2026-11-04, the user)
---

# The bootstrap UI becomes the explorer's Settings; its server stays a separate process

Workspaces are added from the explorer itself (the user, 2026-11-04). So the bootstrap's browser
workflow (connect, report, draft, review, Boards, export) moves into a Settings section of the
explorer client, as phase 2 of `explorer/specs/workspaces/SPEC.md`. That ends the bootstrap SPEC's
"separate local development tool" boundary for the UI. Three limits keep it safe:

- **The bootstrap server stays its own process.** It already imports `explorer-agent` to reuse
  Dataset loading, the SQL guard and the Board checks. Putting it inside the agent would make that
  import circular. The client calls it directly, as it calls the agent (ADR 0007).
- **Settings writes only into `EXPLORER_DATA_DIR`**: Datasets it creates, and the Workspace list.
  Repo Datasets such as `explorer/datasets/austender` are listed read-only and change only through
  a commit. `regen_dataset.sh` remains the way to regenerate them.
- **No secret passes through the browser.** A Workspace names an environment variable in the root
  `.env` (`dsn_env`), and Settings offers only variables that are declared and not yet used.
  Creating the read-only role stays a manual step for the database's owner.

After an export, Settings asks the agent to reload the Workspace list, and the new Workspace
mounts without a restart (ADR 0039).

## Considered Options

- **Keep the bootstrap a separate app** and add Workspaces by editing `workspaces.toml`. Simpler,
  but the user wants one place to add a database.
- **Merge the bootstrap server into the agent process.** One process, but a circular package
  dependency, and the paid drafting runs would share the agent's event loop.
- **Type the DSN into Settings** and keep it in a git-ignored secrets file. It's convenient, but it
  breaks "secrets only in `.env`" and puts credentials through a web form.
