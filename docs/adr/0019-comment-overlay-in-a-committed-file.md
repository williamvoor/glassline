---
status: proposed
---

# Read-only databases get their Data dictionary from a Comment overlay in a committed file

Until now the Data dictionary came only from `COMMENT ON` in the dataset database, so a database we can't add comments to (one we don't own) couldn't be described to the agent. We keep the database's own comments and lay a Comment overlay over them: a `comments.toml` file in the Dataset's directory, read once at startup, whose entries replace the database's description of the same table or column. Objects without an entry keep their database comment. The overlay applies everywhere comments reach the model, both in the cached prompt prefix and in `describe_table`.

## Considered Options

- **Database comments only (today).** Rejected: it excludes every database we can read but not alter.
- **An external comments database** (a separate Postgres table, read at startup). Deferred: it is not reviewable in git, adds a second database the explorer depends on at startup, two more secrets and a third export target in the bootstrap tool. Its only advantage, sharing comments between people or deployments, has no current user. Revisit when a second consumer of the comments exists.
- **Overlay replaces the database's comments entirely.** Rejected: a read-only database whose owner already commented it would lose those comments.

## Consequences

- The prompt prefix stays byte-identical across requests, because the overlay is read once at startup. Editing `comments.toml` takes effect on restart.
- An overlay entry that names a table or column missing from the Allowed schemas is a startup error, which catches drift after a schema change.
- With no overlay configured, the explorer's behaviour and prompt bytes are unchanged.
