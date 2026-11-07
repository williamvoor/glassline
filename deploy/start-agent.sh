#!/bin/sh
# The agent container's command: seed the Workspace and Connection lists on the first start, then
# run the agent.
set -eu
: "${EXPLORER_DATA_DIR:?EXPLORER_DATA_DIR must be set}"
CONNS="$EXPLORER_DATA_DIR/connections.toml"
if [ ! -e "$CONNS" ]; then
  cp /app/deploy/connections.toml "$CONNS"
  echo "explorer: seeded $CONNS"
elif ! grep -q '^name = "austender"' "$CONNS"; then
  # The austender Workspace names this Connection, so it comes back with it (see below). If its
  # variable is already listed under another name, the duplicate makes this entry invalid, and the
  # Workspace says so (fix it in Settings by deleting the other Connection).
  grep -v '^#' /app/deploy/connections.toml >> "$CONNS"
  echo "explorer: re-added the austender Connection to $CONNS"
fi
LIST="$EXPLORER_DATA_DIR/workspaces.toml"
if [ ! -e "$LIST" ]; then
  cp /app/deploy/workspaces.toml "$LIST"
  echo "explorer: seeded $LIST"
elif ! grep -q '^name = "austender"' "$LIST"; then
  # The seeded Workspace is repo kind: Settings can delete it but can't recreate it, so a restart
  # puts it back (README, "Workspaces and Settings"). Other entries are left as they are.
  grep -v '^#' /app/deploy/workspaces.toml >> "$LIST"
  echo "explorer: re-added the austender Workspace to $LIST"
fi
exec python -m explorer_agent
