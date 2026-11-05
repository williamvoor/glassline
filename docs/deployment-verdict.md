# Deployment plan verdict

**Date:** 2026-11-06 · **Plan reviewed:** `docs/deployment-plan.md` (draft 2026-11-05) · **Code inspected:** `explorer/agent/src/explorer_agent/{config,server,__main__,workspaces,sql}.py`, `explorer/client/{a2a,app}.ts`, `explorer/.env.example`, ADR 0007

## Summary

Fundamentally sound target architecture. Blockers correctly identified. Not shippable as written — 8 gaps would cause a failed deploy if followed literally. Patch below before executing.

## Verdict (1–8)

### 1. Target architecture — sound
Browser → Vercel static → container agent (uvicorn) → Meta API + managed Postgres (read-only) is the only viable shape given SSE + `TURN_TIMEOUT_S=60`. Direct browser→agent with CORS (ADR 0007) correctly preserved, no proxy.

### 2. Single-instance constraint — correctly captured, but plan understates default
Plan says "Run exactly one instance" — correct: `Conversations`, `ResultStore`, `BoardViews` cache and tab `surfaceId→Turn` map are in-memory (`executor.py`, `results.py`). However `.env.example` now defaults to `EXPLORER_DATA_DIR=~/.explorer` (Workspace mode). With it set, `__main__.main()` ignores `DATABASE_URL`/`DATASET_DIR` and enters `_serve_workspaces`. Deploy must explicitly **unset `EXPLORER_DATA_DIR`** for the single-dataset Phase 1, or the container boots with no workspaces.

### 3. Host/port & public URL — blocker real, fix incomplete
`__main__._serve` builds `url=f"http://{host}:{port}"` → `server.agent_card(url)` → client fails on `0.0.0.0:8080`. Same for `workspaces.Registry.listing: f"{base_url}/w/{name}"`. `AGENT_PUBLIC_URL` (e.g. `https://explorer-agent.fly.dev`) is required. Plan omits that `PORT` fallback belongs in `config.py` (Cloud Run/Fly inject `$PORT`); mapping only in the start command is brittle. Add `PORT` fallback to `AGENT_PORT`.

### 4. Auth — design sound, ordering missing
Shared `Authorization: Bearer` with card + `OPTIONS` exempt is right for Phase 1. Must specify: auth middleware **inside** `CORSMiddleware` (otherwise preflight gets 401 before CORS headers), and `allow_headers` must explicitly include `Authorization` once credentials are used. Every fetch in `a2a.ts` (`fromCardUrl` card fetch exempt, `sendMessageStream` + Workspace routes `/workspaces`, `/conversations/*` with header) needs wiring; `localStorage` choice should note XSS exposure.

### 5. CORS — gap real
`server.build_app` and `workspaces.workspace_app` do `allow_origins=[client_origin]` — single origin only. Comma-split `CLIENT_ORIGIN` + optional `allow_origin_regex` for Vercel previews is required as plan says. No code change yet.

### 6. Client build — basically correct, one clarification
`VITE_AGENT_URL` is build-time (`import.meta.env` in `app.ts`) — set as Vercel env before build. Plan's `vercel.json` note should be explicit: Root=`explorer/client`, Build=`npm run build`, Output=`dist`, **no SPA `rewrites`** — app uses hash routing (`#/settings`, `#/workspace/cid` in `app.ts`/`workspaces.ts`).

### 7. DB & image steps — correct with path trap
`pg_dump -Fc` / `pg_restore --no-owner --no-acl` preserves `COMMENT ON` (prompt depends on it); pooled `sslmode=require` correct. Image step must respect ADR 0045: datasets live at repo-root `datasets/<name>/`, but `config.load_settings` resolves relative `DATASET_DIR` against `explorer/`. Plan's "copy `datasets/<name>/`" must land so `../datasets/austender` resolves, or set `DATASET_DIR` absolute in the image. Also add `.dockerignore` for `.env`, `__pycache__`.

### 8. Phase 2 / risks — underestimates Workspace breakage
`workspaces.Guard.for_port(host,port)` only allows `Host: 127.0.0.1|localhost:port` — any public deploy in Workspace mode gets `403` on every route. Deferring Workspaces to Phase 2 is correct, but plan should flag Guard + `base_url` + Bootstrap server (`bootstrap/server/src/explorer_bootstrap/server.py: CORS [client_origin]`) as additional blockers for Phase 2.

## What would enable serverless (replaces SSE)

True scale-to-zero FaaS (Vercel Functions / Lambda) is blocked by three coupled choices; fix all three:

1. **Replace SSE with polling.** Current `A2AStarletteApplication` holds one SSE connection for the whole Turn (up to 60s, 8 model iterations in `llm.py`). FaaS kills long connections (Vercel Hobby 10s). Enabler: `POST /message → {taskId}` + `GET /tasks/{id}` polling, or chunked NDJSON. `InMemoryTaskStore` already exists — make it durable and teach `explorer/client/src/a2a.ts` a polling fallback to `sendMessageStream`.

2. **Externalize state.** `Conversations`, `ResultStore` (50×5k rows), `DrillDowns`, `BoardViews` cache and `surfaceId→Turn/Level` are in-process. Enabler: Postgres/Redis for `contextId → history + results + tabs`, DB-backed `TaskStore`, persisting `LlmLoop` iteration so a new function invocation can resume the tool loop.

3. **Chunk the tool loop.** Today `llm.py` loops synchronously (model → tools → model) inside one handler. Enabler: state machine — one invocation = one `litellm.acompletion` + `run_sql` → save → return `running` → client re-invokes / queue triggers next. Otherwise `SQL_TIMEOUT_MS=15s` × retries blows function budgets.

Trade-off: you lose prompt-cache warmth (`PROMPT_CACHE_KEY` prefix byte-identical in `prompt.py`) and add cold-start + Neon/Supabase wake-up latency. Speed is the repo's top requirement (`AGENTS.md`), so plan's choice of always-on container (Fly `syd` `min_machines_running=1` / Cloud Run `min=1`) is the right default; serverless containers (`min=0`) are the pragmatic "serverless" without the rewrite.
