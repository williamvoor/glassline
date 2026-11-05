# Cloud deployment plan

**Status:** draft · **Written:** 2026-11-05

This plan puts the explorer online with the client on Vercel, the Python agent on a container host
with a free or near-free tier, and the dataset on managed Postgres. Deployment was outside the v1
scope (`explorer/README.md`, ADR 0007), so this plan lists the code changes the explorer needs
before it can run anywhere other than `127.0.0.1`.

## 1. Target architecture

```
Browser ──HTTPS──▶ Vercel (static client: Vite build of explorer/client)
   │
   └──HTTPS + SSE (A2A message/stream, CORS)──▶ Agent container (explorer/agent, uvicorn)
                                                 ├─▶ Meta Model API (meta/muse-spark-1.3)
                                                 └─▶ Managed Postgres (read-only role)
```

ADR 0007 still applies: the browser calls the agent directly, and the agent allows the Vercel
origin with CORS. No proxy is involved.

## 2. Constraints from the current design

| Constraint | Source | Consequence for hosting |
|---|---|---|
| Answers stream over SSE and a Turn can take up to `TURN_TIMEOUT_S` | ADR 0007, `server.py` | Serverless functions (Vercel Python, Lambda) don't fit. Use a long-lived container. |
| Conversations, Results, tab state and caches live in memory | `explorer/README.md` | Run exactly **one** instance. A restart, redeploy or scale-to-zero loses every Conversation. |
| Speed is the top requirement | `AGENTS.md` | Avoid cold starts. Put the agent and the database in the same region (Sydney). |
| The explorer connects only with the read-only role | `AGENTS.md` | The hosted DB needs `explorer_ro` (or the vicprop equivalent) recreated. |
| No authentication | `explorer/README.md` | A public URL exposes `META_API_KEY` spend. Some form of auth is mandatory (§5.3). |
| Secrets in `genUI/.env` | `AGENTS.md` | In the cloud they become platform env vars. Never bake `.env` into an image. |
| A2UI SDK comes from git at a pinned rev | `explorer/agent/pyproject.toml` | The image build needs `git` and network access. `uv sync --frozen` installs it. |

## 3. Choosing services

### Client: Vercel (Hobby, free)
It's a static Vite build, so Vercel fits well. Hobby is for non-commercial use only. Check
whether a university project counts.

### Agent: options

| Host | Free? | Always-on | SSE | Disk | Notes |
|---|---|---|---|---|---|
| **Google Cloud Run** | Free tier on request-time; `min-instances=1` costs about US$5–15/mo | With `min=1` | Yes (set request timeout ≥ `TURN_TIMEOUT_S`) | No (ephemeral) | `australia-southeast1`. Set `max-instances=1` and "CPU always allocated" with `min=1`. |
| **Fly.io** | No real free tier any more (about US$2–5/mo for a small machine) | Yes | Yes | Volumes | `syd` region. Simplest for always-on plus a volume. |
| **Render** | Free web service | **No**: sleeps after ~15 min idle, 30–60 s cold start | Yes | Not on free | Fine for a demo link, poor for speed. |
| Koyeb / Railway | Small free/trial credit | Varies | Yes | Railway: yes | Fallbacks. |

**Recommendation:** use **Fly.io in `syd`** if a few dollars a month is acceptable, and **Render free** if
it must be $0 and cold starts are tolerable. Cloud Run is a good middle ground if you already use GCP.

### Database: options

| Host | Free storage | Region | Notes |
|---|---|---|---|
| **Neon** | ~0.5 GB | `ap-southeast-2` (Sydney) | Scales to zero, which adds a first-query wake-up delay of a few hundred ms. |
| **Supabase** | 500 MB | Sydney | Pauses after a week of inactivity on free. |
| Fly Postgres / Render Postgres | Small or paid | Sydney / Singapore | Render's free DB expires after 30 days. |

**Open question:** how big are the AusTender and vicprop databases? Check with
`SELECT pg_size_pretty(pg_database_size(current_database()));`. If either is over about 500 MB,
the free tiers are out, so you either pay (Neon Launch / Supabase Pro, about US$19–25/mo) or deploy
a trimmed dataset.

## 4. Scope of the first deploy

- **Single-dataset mode** (`DATABASE_URL` + `DATASET_DIR`), Workspaces **off**. Workspace mode
  needs a persistent disk for saved Conversations (`EXPLORER_DATA_DIR`) and a second process
  (the bootstrap server for Settings). That's phase 2 (§8).
- One dataset (AusTender, or whichever fits the DB tier).
- Production URL only. Vercel preview deploys stay off or unwired until multi-origin CORS exists.

## 5. Code changes (agent)

Each change gets tests first, per the repo rules.

### 5.1 Public URL for the agent card ❗️ blocker
`__main__.py` builds the card URL as `f"http://{agent_host}:{agent_port}"`. The client loads the
card (`a2a.ts`, `A2AClient.fromCardUrl`) and then calls the card's `url`. Behind a host, that would
be `http://0.0.0.0:8080`, so every request would fail. The Workspace listing (`workspaces.py`,
`base_url`) has the same problem.

- Add `AGENT_PUBLIC_URL` (e.g. `https://explorer-agent.fly.dev`), defaulting to the current
  `http://host:port`.
- Use it for the agent card and the Workspace listing URLs.
- Test: with `AGENT_PUBLIC_URL` set, `/.well-known/agent-card.json` returns it.

### 5.2 Bind address and port
- In the container, set `AGENT_HOST=0.0.0.0`.
- Cloud Run and Render inject `$PORT`. Either have the start command map it
  (`AGENT_PORT=$PORT`) or teach `config.py` to fall back to `PORT`. Prefer the start-command
  mapping, because it doesn't touch config.

### 5.3 Authentication gate
Pick one:
- **(a) Shared access token** (recommended for a first deploy): the agent requires
  `Authorization: Bearer <token>` on every route except `/.well-known/agent-card.json` and CORS
  preflight. The client asks for the token once on an inline card (no pop-ups, per the UI
  rule) and keeps it in `localStorage`. New setting: `AGENT_ACCESS_TOKEN`, with a placeholder in
  `.env.example`.
- **(b) Edge auth:** Cloudflare Access in front of both the client and the agent. No code changes,
  but you need a domain on Cloudflare, and the SSE and CORS behaviour through Access must be checked.
- Vercel password protection covers only the client, so on its own it isn't enough.

Whichever you pick, also set a **spend limit on `META_API_KEY`** and consider a per-IP rate limit.

Tests: no token gives 401; a wrong token gives 401; the right token streams; preflight (OPTIONS)
passes without a token; the agent card stays public.

### 5.4 CORS for more than one origin
`server.py` passes `allow_origins=[client_origin]`. Allow `CLIENT_ORIGIN` to be a comma-separated
list (production URL plus, optionally, a custom domain). Leave preview deploys out unless needed,
and if they are, use `allow_origin_regex` limited to your Vercel project's preview pattern.
Keep `allow_headers` covering `X-A2A-Extensions` (ADR 0007). Extend the existing preflight test.

### 5.5 Health check
Platforms need a cheap liveness URL. `/.well-known/agent-card.json` works and needs no
code. Point the health check at it.

### 5.6 Logging
Logs go to stdout already. Confirm that no secret or DSN reaches the logs in the cloud start path
(the `check_dsn` error messages, for example).

## 6. Code changes (client)

- `VITE_AGENT_URL` is read at **build** time (`app.ts`). Set it in Vercel's project env.
- Add the token card from §5.3(a), and send the header from `a2a.ts` (the A2A client's fetch) and
  from any other fetches (saved Conversations, Boards).
- `vercel.json`: framework Vite, root `explorer/client`, build `npm run build`, output `dist`.
  Add an SPA fallback rewrite only if the client uses path routing (check this; it may not).

## 7. Step-by-step rollout

### Step 1: Database
1. Measure DB sizes (§3) and pick the tier.
2. Create the hosted DB in Sydney.
3. `pg_dump -Fc` locally, then `pg_restore --no-owner` to the host.
   Check that the `COMMENT ON`s survive (the schema prompt depends on them).
4. Recreate the read-only role with `austender/`'s role SQL, using a new password.
5. Check: connect as `explorer_ro`, run a write and see it fail, run a read and see it succeed,
   and compare row counts with local.
6. Use the provider's **pooled** or SSL connection string with `sslmode=require`.

### Step 2: Agent image
1. Add `explorer/agent/Dockerfile`: `python:3.13-slim`, install `git`, copy `uv` from
   `ghcr.io/astral-sh/uv`, `uv sync --frozen --no-dev`, copy `src/` and the dataset dir
   (`datasets/<name>/` at the repo root, ADR 0045), and copy the catalog if the agent reads it at runtime (check
   paths in `catalog.py`; the image must not depend on `a2ui/`).
2. Start command: `uv run python -m explorer_agent` with `AGENT_HOST=0.0.0.0`.
3. Add a `.dockerignore` covering `.venv`, `tests`, `.env` and `__pycache__`.
4. Build and run it locally against the hosted DB, with the client at `VITE_AGENT_URL=http://127.0.0.1:<port>`.

### Step 3: Agent host (Fly example)
1. `fly launch --no-deploy` in `explorer/agent`, region `syd`, one machine,
   `auto_stop_machines = false`, `min_machines_running = 1`.
2. `fly secrets set META_API_KEY=… DATABASE_URL=… AGENT_ACCESS_TOKEN=…`.
3. Env: `MODEL`, `DATASET_DIR`, `AGENT_HOST=0.0.0.0`, `AGENT_PORT=8080`,
   `AGENT_PUBLIC_URL=https://<app>.fly.dev`, `CLIENT_ORIGIN=https://<project>.vercel.app`,
   plus the limits from `.env.example`.
4. Health check: `GET /.well-known/agent-card.json`.
5. Check that the Fly proxy doesn't buffer SSE: run `curl -N` on a `message/stream` request and
   watch status events arrive one by one.

(For Cloud Run, set `--min-instances=1 --max-instances=1 --no-cpu-throttling --timeout=<≥ TURN_TIMEOUT_S + margin>`.
For Render, use a Docker web service with the same env, and accept cold starts.)

### Step 4: Client on Vercel
1. Import the repo, set the root directory to `explorer/client`, and set `VITE_AGENT_URL` to the agent's public URL.
2. Deploy, then set the agent's `CLIENT_ORIGIN` to the final Vercel domain and redeploy the agent.

### Step 5: Acceptance
- Ask a scenario question from `docs/scenarios/`. The chart, table and SQL details render, and statuses stream live.
- Check a drill-down and a Board.
- In DevTools, the CORS preflight passes and there are no mixed-content errors.
- Requests without the token are rejected.
- Agent logs show prompt and cached tokens per call (prompt caching still works).
- Latency: compare time-to-first-status and total Turn time with local. If the gap is big,
  check the region and the DB wake-up delay.

## 8. Phase 2 (later)

- **Workspaces:** a persistent volume for `EXPLORER_DATA_DIR` (Fly volume or Railway), one DSN
  env var per Workspace, the bootstrap server as a second process (or service) with its own
  public URL (`VITE_BOOTSTRAP_URL`) and the same auth. Bootstrap calls Muse Wonder and writes
  files, so decide whether Settings should be reachable in a public deploy at all.
- **Durable Conversations:** only needed if more than one instance or restart survival becomes a
  requirement. That's a design change (ADR).
- **CI deploys:** build the image on push to `main`.
- **Real user auth** (OAuth / Cloudflare Access) instead of a shared token.

## 9. Costs (rough, monthly)

| Setup | Cost |
|---|---|
| Vercel Hobby + Render free + Neon free | $0 (cold starts; DB ≤ 0.5 GB) |
| Vercel Hobby + Fly small machine + Neon free | ~US$3–6 |
| Vercel Hobby + Cloud Run min=1 + Supabase Pro | ~US$30–40 |
| Plus Meta Model API usage | Depends on traffic. Cap it. |

## 10. Decisions needed

1. Agent host: Fly (small cost, always-on) or Render (free, cold starts) or Cloud Run.
2. DB tier, which depends on the measured sizes.
3. Auth: shared token (code change) or Cloudflare Access (no code, needs a domain).
4. Which dataset goes first.
5. Is Vercel Hobby's non-commercial term acceptable for this project?

## 11. Risks

- **SSE buffering** by a platform proxy would make answers appear all at once. Check it in Step 3.
- **In-memory state** is lost on every deploy. Tell users, or deploy at quiet times.
- **Neon or Supabase pauses** add latency to the first query after idle.
- **Key exposure or spend** if auth is skipped.
- **Image depends on GitHub** for the pinned A2UI rev. If that rev disappears, builds break.
  Consider vendoring wheels later.
