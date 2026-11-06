#!/bin/sh
# The agent container's command: seed the Workspace list on the first start, then run the agent.
set -eu
: "${EXPLORER_DATA_DIR:?EXPLORER_DATA_DIR must be set}"
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
