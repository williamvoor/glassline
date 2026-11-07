# One VM deploy (deploy/README.md), the same as `explorer/run.sh`: Workspaces with Settings (ADR 0048).
# Build context is the repo root: `docker compose` in deploy/ passes `..`. Two targets:
#   agent  the explorer agent and the bootstrap server (Settings), keeping the repo layout
#          (EXPLORER_ROOT is parents[3] of the agent's __main__.py, REPO_ROOT parents[5] of the
#          bootstrap's, TEMPLATES_DIR explorer/bootstrap/templates). Two venvs, one per uv project.
#   web    Caddy with the built client: HTTPS, basic auth, static files, /agent/* and /bootstrap/*

# ---- client build ---------------------------------------------------------------------------------
FROM node:22.21.1-bookworm-slim AS client
WORKDIR /app/explorer/client
COPY explorer/client/package.json explorer/client/package-lock.json ./
RUN npm ci
COPY explorer/client/ ./
# The browser reaches the agent and Settings here; AGENT_PUBLIC_URL must name the same agent URL.
ARG VITE_AGENT_URL
ARG VITE_BOOTSTRAP_URL
RUN test -n "$VITE_AGENT_URL" && test -n "$VITE_BOOTSTRAP_URL" \
    || { echo "VITE_AGENT_URL and VITE_BOOTSTRAP_URL build args are required" >&2; exit 1; }
RUN VITE_AGENT_URL="$VITE_AGENT_URL" VITE_BOOTSTRAP_URL="$VITE_BOOTSTRAP_URL" npm run build

# ---- agent ----------------------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:0.9.28-python3.13-bookworm-slim AS agent
# git: the A2UI SDK packages are git dependencies (ADR 0006).
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# austender is a path source in the agent's uv config, so it must exist for `uv sync` to resolve.
# Its src is also what deploy/ensure_roles.py imports (ensure_roles).
COPY austender/pyproject.toml austender/README.md austender/
COPY austender/src austender/src
COPY explorer/agent/pyproject.toml explorer/agent/uv.lock explorer/agent/README.md explorer/agent/
COPY explorer/bootstrap/server/pyproject.toml explorer/bootstrap/server/uv.lock explorer/bootstrap/server/README.md \
     explorer/bootstrap/server/
WORKDIR /app/explorer/agent
RUN uv sync --frozen --no-dev --no-install-project
COPY explorer/agent/src src
COPY explorer/catalog /app/explorer/catalog
COPY datasets/austender /app/datasets/austender
COPY deploy/ensure_roles.py deploy/workspaces.toml deploy/connections.toml deploy/start-agent.sh /app/deploy/
RUN uv sync --frozen --no-dev
WORKDIR /app/explorer/bootstrap/server
COPY explorer/bootstrap/server/src src
COPY explorer/bootstrap/templates /app/explorer/bootstrap/templates
RUN uv sync --frozen --no-dev
# The data directory (EXPLORER_DATA_DIR, a volume): Workspaces and saved Conversations.
RUN useradd --system --create-home --uid 10001 explorer && mkdir -p /data && chown explorer /data && chmod 700 /data
USER explorer
WORKDIR /app/explorer/agent
ENV PATH="/app/explorer/agent/.venv/bin:$PATH"
CMD ["sh", "/app/deploy/start-agent.sh"]

# ---- web ------------------------------------------------------------------------------------------
FROM caddy:2.10.2 AS web
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=client /app/explorer/client/dist /srv
