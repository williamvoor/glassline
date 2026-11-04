---
status: accepted (2026-11-04, the user)
---

# Workspaces are sub-apps of one agent process, one per Dataset

The explorer is to serve several databases, each as a Workspace: one Dataset plus its saved
Conversations (`explorer/specs/workspaces/SPEC.md`). One agent process serves them all. Each
Workspace gets its own A2A sub-app under `/w/<name>/`, built exactly as today's single agent is:
its own settings, connection pool, read-only login check, Allowed schemas, prompt prefix,
`prompt_cache_key`, Boards and Conversations. A small path-prefix router in front picks the
sub-app, so a Workspace can be added or removed at runtime by changing one dictionary. Without a
Workspace list (`EXPLORER_DATA_DIR` unset), the agent runs one Dataset at the root, as it does today.

This replaces SPEC §1's "more than one dataset per running agent" (out of scope for v1).

## Considered Options

- **One agent that picks the Dataset per request.** One executor and one pool map, but every
  per-Dataset rule (the byte-identical prompt prefix, the read-only login, the schema allowlist,
  currency, Boards, Result IDs) would have to be threaded through every request path and tested
  again. One missed spot leaks one database's schema or rows into another's answer.
- **One agent process per Workspace**, started by a launcher on separate ports. No agent change,
  but N processes and ports to manage, and Workspaces added from Settings (phase 2) would need
  process supervision.
- **Questions across Workspaces.** Out: one question would span two prompts, two logins and two
  caches.

## Consequences

- A Workspace that fails its checks (database down, or a login that can write) is listed as
  unavailable and not mounted. The others start. Today one failing check stops the agent.
- The per-Dataset prompt caches stay warm, and no request pays for routing beyond a dictionary lookup.
- Scripts that talk to the agent (`run.sh`, the scenarios and effort-check harnesses) keep working
  unchanged in single-Dataset mode. In Workspace mode they take the Workspace's URL.
