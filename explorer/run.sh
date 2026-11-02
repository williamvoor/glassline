#!/usr/bin/env bash
# Runs the stub agent (no database, no model) and the client dev server together.
# Ctrl-C stops both. Usage: ./run.sh   (from anywhere)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_PORT="${AGENT_PORT:-10002}"
AGENT_URL="http://127.0.0.1:${AGENT_PORT}"

if [ ! -d "$HERE/client/node_modules" ]; then
  (cd "$HERE/client" && npm install)
fi

(cd "$HERE/agent" && exec uv run python -m explorer_agent.testing.stub_server --port "$AGENT_PORT") &
AGENT_PID=$!
trap 'kill "$AGENT_PID" 2>/dev/null || true; wait "$AGENT_PID" 2>/dev/null || true' EXIT INT TERM

for _ in $(seq 1 100); do
  if curl -sf "$AGENT_URL/.well-known/agent-card.json" >/dev/null; then break; fi
  if ! kill -0 "$AGENT_PID" 2>/dev/null; then echo "stub agent failed to start" >&2; exit 1; fi
  sleep 0.2
done
curl -sf "$AGENT_URL/.well-known/agent-card.json" >/dev/null || { echo "stub agent not ready at $AGENT_URL" >&2; exit 1; }

cd "$HERE/client"
VITE_AGENT_URL="$AGENT_URL" npm run dev
