#!/usr/bin/env bash
# Runs an agent and the client dev server together. Ctrl-C stops both.
#
#   ./run.sh           stub agent: no database, no model, every answer is the same fixture
#   ./run.sh --real    real agent: Muse Spark and the dataset database, settings from genUI/.env
#                      With EXPLORER_DATA_DIR set (in the environment or genUI/.env) the agent runs
#                      in Workspace mode (specs/workspaces/SPEC.md) and the client shows the picker.
#
# Then open http://127.0.0.1:5173. Works from any directory.
set -euo pipefail

MODE=stub
case "${1:-}" in
  "") ;;
  --real) MODE=real ;;
  *) echo "usage: $0 [--real]" >&2; exit 2 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_PORT="${AGENT_PORT:-10002}"
AGENT_URL="http://127.0.0.1:${AGENT_PORT}"
CLIENT_ORIGIN="http://127.0.0.1:5173" # vite.config.ts serves here (strictPort)

# Workspace mode: the agent serves /workspaces, and each Workspace's card under /w/<name>/.
WORKSPACES=0
if [ "$MODE" = real ]; then
  ENV_FILE="${EXPLORER_ENV_FILE:-$HERE/../.env}"
  if [ -n "${EXPLORER_DATA_DIR:-}" ] || { [ -f "$ENV_FILE" ] && grep -Eq '^[[:space:]]*(export[[:space:]]+)?EXPLORER_DATA_DIR=[\"'"'"']?[^\"'"'"'[:space:]]' "$ENV_FILE"; }; then
    WORKSPACES=1
  fi
fi
READY_PATH="/.well-known/agent-card.json"
if [ "$WORKSPACES" = 1 ]; then READY_PATH="/workspaces"; fi

if curl -sf "$AGENT_URL$READY_PATH" >/dev/null; then
  echo "something is already serving on $AGENT_URL; stop it first" >&2
  exit 1
fi

if [ ! -d "$HERE/client/node_modules" ]; then
  (cd "$HERE/client" && npm install)
fi

if [ "$MODE" = real ]; then
  if [ ! -f "$HERE/../.env" ] && [ -z "${EXPLORER_ENV_FILE:-}" ]; then
    echo "no settings: copy explorer/.env.example to the repo root as .env and fill it in" >&2
    exit 1
  fi
  # The port and origin set here override .env, so the agent and client always agree.
  (cd "$HERE/agent" && AGENT_HOST=127.0.0.1 AGENT_PORT="$AGENT_PORT" CLIENT_ORIGIN="$CLIENT_ORIGIN" \
    exec uv run python -m explorer_agent) &
  WAIT_TICKS=300 # startup reads the schema and checks the role: allow 60 s
else
  (cd "$HERE/agent" && exec uv run python -m explorer_agent.testing.stub_server \
    --port "$AGENT_PORT" --client-origin "$CLIENT_ORIGIN") &
  WAIT_TICKS=100
fi
AGENT_PID=$!
trap 'kill "$AGENT_PID" 2>/dev/null || true; wait "$AGENT_PID" 2>/dev/null || true' EXIT INT TERM

for _ in $(seq 1 "$WAIT_TICKS"); do
  if curl -sf "$AGENT_URL$READY_PATH" >/dev/null; then break; fi
  if ! kill -0 "$AGENT_PID" 2>/dev/null; then echo "$MODE agent failed to start (see the message above)" >&2; exit 1; fi
  sleep 0.2
done
curl -sf "$AGENT_URL$READY_PATH" >/dev/null || { echo "$MODE agent not ready at $AGENT_URL" >&2; exit 1; }
echo "$MODE agent ready at $AGENT_URL; client at $CLIENT_ORIGIN"

cd "$HERE/client"
VITE_AGENT_URL="$AGENT_URL" VITE_WORKSPACES="$WORKSPACES" npm run dev
