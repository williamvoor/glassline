#!/usr/bin/env bash
# Runs the explorer: the agent, the bootstrap server (Settings) and the client dev server. Ctrl-C
# stops all three. There is one mode (ADR 0048): Workspaces with Settings, so EXPLORER_DATA_DIR is
# required, in the environment or the repo root's .env (or EXPLORER_ENV_FILE).
#
#   ./run.sh
#
# Then open http://127.0.0.1:5173. Works from any directory.
set -euo pipefail

if [ "$#" -ne 0 ]; then echo "usage: $0" >&2; exit 2; fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_PORT="${AGENT_PORT:-10002}"
AGENT_URL="http://127.0.0.1:${AGENT_PORT}"
CLIENT_ORIGIN="http://127.0.0.1:5173" # vite.config.ts serves here (strictPort)
BOOTSTRAP_PORT="${BOOTSTRAP_PORT:-8765}"
BOOTSTRAP_URL="http://127.0.0.1:${BOOTSTRAP_PORT}"
ENV_FILE="${EXPLORER_ENV_FILE:-$HERE/../.env}"

if [ -z "${EXPLORER_DATA_DIR:-}" ] && [ ! -f "$ENV_FILE" ]; then
  echo "no settings: copy explorer/.env.example to the repo root as .env and fill it in (EXPLORER_DATA_DIR is required)" >&2
  exit 1
fi
if [ -z "${EXPLORER_DATA_DIR:-}" ] && ! grep -Eq '^[[:space:]]*(export[[:space:]]+)?EXPLORER_DATA_DIR[[:space:]]*=[[:space:]]*[\"'"'"']?[^\"'"'"'[:space:]]' "$ENV_FILE"; then
  echo "EXPLORER_DATA_DIR is required (the Workspaces directory, outside the repo; see explorer/.env.example)" >&2
  exit 1
fi

if curl -sf "$AGENT_URL/workspaces" >/dev/null; then
  echo "something is already serving on $AGENT_URL; stop it first" >&2
  exit 1
fi

if [ ! -d "$HERE/client/node_modules" ]; then
  (cd "$HERE/client" && npm install)
fi

# The port and origin set here override .env, so the agent, Settings and the client always agree.
(cd "$HERE/agent" && AGENT_HOST=127.0.0.1 AGENT_PORT="$AGENT_PORT" CLIENT_ORIGIN="$CLIENT_ORIGIN" \
  exec uv run python -m explorer_agent) &
AGENT_PID=$!
BOOTSTRAP_PID=""
trap 'kill "$AGENT_PID" $BOOTSTRAP_PID 2>/dev/null || true; wait "$AGENT_PID" $BOOTSTRAP_PID 2>/dev/null || true' EXIT INT TERM

# Settings (the explorer's #/settings) talks to the bootstrap server (ADR 0041).
if curl -sf "$BOOTSTRAP_URL/api/connections" >/dev/null; then
  echo "a bootstrap server in Settings mode is already on $BOOTSTRAP_URL; Settings will use it" >&2
elif curl -s "$BOOTSTRAP_URL/" >/dev/null; then
  echo "something else is serving on $BOOTSTRAP_URL (an old bootstrap serve?); stop it, or set BOOTSTRAP_PORT" >&2
  exit 1
else
  (cd "$HERE/bootstrap/server" && BOOTSTRAP_ENV_FILE="$ENV_FILE" \
    AGENT_PORT="$AGENT_PORT" CLIENT_ORIGIN="$CLIENT_ORIGIN" \
    exec uv run python -m explorer_bootstrap serve --port "$BOOTSTRAP_PORT") &
  BOOTSTRAP_PID=$!
fi

for _ in $(seq 1 300); do # startup checks each Workspace's login and schema: allow 60 s
  if curl -sf "$AGENT_URL/workspaces" >/dev/null; then break; fi
  if ! kill -0 "$AGENT_PID" 2>/dev/null; then echo "agent failed to start (see the message above)" >&2; exit 1; fi
  sleep 0.2
done
curl -sf "$AGENT_URL/workspaces" >/dev/null || { echo "agent not ready at $AGENT_URL" >&2; exit 1; }
echo "agent ready at $AGENT_URL; Settings at $BOOTSTRAP_URL; client at $CLIENT_ORIGIN"

cd "$HERE/client"
VITE_AGENT_URL="$AGENT_URL" VITE_BOOTSTRAP_URL="$BOOTSTRAP_URL" npm run dev
