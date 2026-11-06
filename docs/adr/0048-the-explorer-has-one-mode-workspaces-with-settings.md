---
status: accepted (2026-11-06, the user)
---

# The explorer has one mode: Workspaces with Settings

On 2026-11-05 the explorer was deployed to EC2 without its Workspace picker or Settings. The
client was built without `VITE_WORKSPACES=1`, so it came up in single-Dataset mode. There was no
way to create a Workspace, and the mistake only showed when someone used the site. A build flag
that silently picks a smaller app is a trap, whoever sets it.

So the explorer has one mode, the full stack: the agent serving Workspaces, the bootstrap server
behind Settings, and a client that always shows the picker and Settings.

- **The client has no mode switch.** `VITE_WORKSPACES` is gone. The client always lists
  `/workspaces` and always offers Settings, whose server is at `VITE_BOOTSTRAP_URL`.
- **The agent requires `EXPLORER_DATA_DIR`.** Without it, `python -m explorer_agent` exits with
  `explorer: EXPLORER_DATA_DIR is required …`. `DATASET_DIR` and `DATABASE_URL` are no longer
  settings. A Workspace's Dataset and login come only from its `workspaces.toml` entry
  (`dataset_dir`, `dsn_env`).
- **The dev tools name a Workspace.** `python -m explorer_agent boards <workspace>` and
  `scripts/scenarios.py run|boards --workspace <name>` read the Workspace list in
  `EXPLORER_DATA_DIR`.
- **`run.sh` always runs the real full stack**: the agent, the bootstrap server and the client.
  It has no stub mode and no `--real` flag, and it refuses to start without `EXPLORER_DATA_DIR`.
- **The stub server stays, for tests only.** The client interop test needs an agent that calls
  no model. It now serves one fake Workspace, `stub`, at `/w/stub/` with a `/workspaces` listing.

This supersedes B15 (single-Dataset mode) in `explorer/specs/workspaces/SPEC.md`, and the ADR
0045 lines "`DATASET_DIR` (single-dataset mode) accepts absolute and `~` paths" and
"Single-dataset mode stays for tests and `run.sh`".

## Considered Options

- **Keep both modes and guard the deploy** (a check in the Dockerfile or compose that the flag is
  set). It fixes this deploy, but leaves the trap everywhere else: `run.sh`, a new Dockerfile, a
  hand build.
- **Default the flag on and keep single-Dataset mode as an opt-out.** It is safer, but the mode
  still exists and still needs its own tests, docs and code paths.
- **Keep a stub mode in `run.sh`** (offline UI work with no database or model key). It was
  dropped: the user wants one app. The stub server survives only as a test fixture.

## Consequences

- Local development needs a database, a model key and `EXPLORER_DATA_DIR`, as the deploy does.
- Tests that built Settings from `DATASET_DIR` now pass the Dataset directory and login directly
  to `load_settings`.
