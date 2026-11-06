# Deployment: the explorer on one Fly machine

Status: **draft** (2026-11-05), not yet approved. Decisions: ADR 0046 (one always-on Fly machine
serves client and agent), ADR 0047 (a shared bearer token gates the public agent). Both are
proposed. Where this file is silent, `explorer/SPEC.md` applies. It supersedes
`docs/deployment-plan.md`.

## Why

The explorer runs only on `127.0.0.1` (ADR 0007, v1 scope). The user wants AusTender online for a
small, trusted audience, kept fast, and as cheap as possible.

## Shape

```
Browser ──HTTPS──▶ Fly.io, app in syd, exactly 1 always-on machine
                    uvicorn: agent (A2A + SSE) + the client's static files, one origin
                    ├─▶ Meta Model API (meta/muse-spark-1.3)
                    └─▶ hosted Postgres (read-only role, TLS)
```

## Decisions

| # | Decision | By |
|---|---|---|
| D1 | Agent host: Fly.io, region `syd`, one machine, `auto_stop_machines = "off"`, deploys with `--ha=false` | user (accounts) |
| D2 | The client is built into the image and served by the agent from the same origin. No Vercel, no CORS for production (ADR 0046) | user |
| D3 | Auth: a shared bearer token (ADR 0047) | user |
| D4 | ~~Dataset: AusTender, single-dataset mode. `EXPLORER_DATA_DIR` is **unset**, so Workspaces and Settings are off~~ **Superseded 2026-11-06 by ADR 0048:** there is no single-dataset mode. A deploy is the full stack (Workspaces, Settings, bootstrap server), with AusTender as a Workspace. This decision is how the EC2 deploy came up without Settings | user |
| D5 | Database host: open. It must meet the requirements under "Database requirements" below. The leading candidate is AWS RDS PostgreSQL `db.t4g.micro` in `ap-southeast-2` | user to pick |
| D6 | Only the `mart` schema is restored (the dataset's `allowed_schemas`). It is about 0.86 GB with indexes | Claude |
| D7 | Fly doesn't set `PORT`. `fly.toml` sets `AGENT_PORT=8080` and `internal_port = 8080`, and `config.py` doesn't change | Claude (research) |
| D8 | SSE keepalive: no code. `sse-starlette` pings every 15 s by default, and the a2a-sdk doesn't override it. That is inside Fly's 60 s idle cut. M4 confirms it | Claude (code read) |
| D9 | Machine: shared-cpu-1x, 1 GB. Downsize to 512 MB if steady-state RSS stays under 300 MB (M8) | Claude |
| D10 | Deployment files live in `deploy/` at the repo root, because the deployment is the owner's data (ADR 0045). The build context is the repo root | Claude |
| D11 | In a production build with `VITE_AGENT_URL` unset, the client uses `window.location.origin`. The dev default stays `http://127.0.0.1:10002` | Claude |

### Non-goals

- ~~Workspaces, saved Conversations and Settings in a public deploy.~~ Superseded by ADR 0048:
  they are always on, so a deploy needs the volume, the public Host guard and the bootstrap server
  (`deploy/compose.yaml` has them).
- CORS with several origins, and Vercel.
- Surviving restarts, or running more than one instance (that would need a new ADR).
- CI deploys. Deploys are manual with `fly deploy`.
- Per-user identity and rate limiting (beyond the spend cap).

## Database requirements (D5)

Any host must:

1. Run PostgreSQL 15 or later, with at least 1.2 GB of storage (0.86 GB `mart` plus headroom).
2. Be reachable from Fly over TLS (`sslmode=require`), either on public IPv4 or on IPv6. Fly's
   outbound IPs are not static, so expect to allow `0.0.0.0/0` and rely on TLS plus a strong
   password.
3. Allow a custom `LOGIN` role with its own password, and
   `ALTER ROLE … SET default_transaction_read_only = on` (as in `austender/src/austender/db.py`,
   `explorer_ro`).
4. Keep `COMMENT ON` after `pg_restore --no-owner --no-acl`, because the schema prompt depends on it.
5. Not pause, or wake in under 1 s. Speed is the top requirement.
6. Be in Sydney or close to it.

Research on 2026-11-05:

| Option | Fit | Cost |
|---|---|---|
| RDS PostgreSQL `db.t4g.micro`, Single-AZ, 20 GB gp3, public | Meets 1–6 (3 and 4 are unchecked on RDS; rehearse) | Free for 12 months on an account opened **before 2025-07-15** (750 h, 20 GB). On a newer account, free-plan credits run out and **the account closes at 6 months** unless upgraded. After that, about US$25/month including US$3.65 for public IPv4 |
| Aurora Serverless v2 | Pauses: about 15 s to resume, 30 s or more after 24 h. Fails 5 | About US$73/month at a 0.5 ACU floor |
| Aurora DSQL | 1 GB free, a 3,000-row transaction limit (breaks `pg_restore`), `COMMENT ON` only for routines. Fails 1 and 4 | — |
| Supabase free | 500 MB, pauses after a week. Fails 1 and 5 | Pro is US$25/month |
| Postgres on a second Fly machine with a volume | Meets 1–6, in the same region | About US$3–5/month, plus your own backups |

## Files

| Path | Change |
|---|---|
| `explorer/agent/src/explorer_agent/config.py` | `Settings`: `agent_public_url`, `access_token` (hidden in `repr`), `client_dist_dir`. `load_settings` (and the second server-settings reader) read `AGENT_PUBLIC_URL`, `AGENT_ACCESS_TOKEN` and `CLIENT_DIST_DIR`, and refuse a non-loopback `AGENT_HOST` without a token |
| `explorer/agent/src/explorer_agent/__main__.py` | `_serve` and `_serve_workspaces` use `agent_public_url` for the card and `Registry` base URL in place of `http://{host}:{port}` |
| `explorer/agent/src/explorer_agent/server.py` | `build_app`: token middleware **inside** `CORSMiddleware`. When `client_dist_dir` is set, mount `StaticFiles(html=True)` at `/` **after** the A2A routes |
| `explorer/agent/src/explorer_agent/auth.py` (new) | ASGI token gate: `hmac.compare_digest`. Exempt: `OPTIONS`, the card path, static files |
| `explorer/client/src/app.ts` | Resolve the agent URL per D11 (a pure function). Show the token card on 401 |
| `explorer/client/src/a2a.ts` | The `fetchImpl` wrapper adds `Authorization` when a token is stored. It keeps `X-A2A-Extensions` |
| `explorer/client/src/workspaces.ts` | `httpApi` sends the same header (no route is used in D4, but keep it consistent) |
| `explorer/client/src/token-card.ts` (new) | Inline card (no pop-up): token field, a submit button, an error line. Stores the token in `localStorage` inside try/catch |
| `explorer/.env.example` | Placeholders: `AGENT_PUBLIC_URL`, `AGENT_ACCESS_TOKEN`, `CLIENT_DIST_DIR` |
| `deploy/Dockerfile` | Stage 1 `node:22-slim`: `npm ci`, `npm run build` in `explorer/client`. Stage 2 `python:3.13-slim` + `git` + `uv` (pinned): copy `austender/` (path dep), `explorer/agent`, `explorer/catalog`, `datasets/austender`, and the client `dist`, keeping the repo layout under `/app` (`EXPLORER_ROOT = parents[3]`). Then `uv sync --frozen --no-dev`. Non-root user. `CMD uv run --no-sync python -m explorer_agent` |
| `.dockerignore` (repo root) | Allow-list: deny `*`, re-include only the paths above. This keeps out `a2ui/`, `.env`, `.venv`, `node_modules`, tests and `glimmer-tune/` |
| `deploy/fly.toml` | `primary_region = "syd"`, `[build] dockerfile`, `[env]` (non-secret settings), `[http_service]` `internal_port = 8080`, `force_https = true`, `auto_stop_machines = "off"`, a health check on `GET /.well-known/agent-card.json` |
| `deploy/README.md` | Runbook R1–R6 below |

## Behaviour and criteria → tests

Agent tests use `uv run pytest` in `explorer/agent`. They make no network or model calls. The
existing stub model and test DB apply.

| # | Criterion | Test |
|---|---|---|
| C1 | `AGENT_PUBLIC_URL` unset → the card `url` is `http://{AGENT_HOST}:{AGENT_PORT}`, as now | `test_config.py::test_public_url_defaults_to_host_and_port` |
| C2 | Set → the card `url` equals it, with a trailing `/` removed. Not `http(s)://…` → `ConfigError` | `test_config.py::test_public_url_override_and_validation`, `test_server.py::test_agent_card_uses_the_public_url` |
| C3 | Workspace mode builds listing URLs from the public URL | `test_workspaces.py::test_listing_uses_the_public_url` |
| C4 | Token set: a request with no `Authorization`, or with a wrong one, gets 401 with a JSON body. The right one gets the normal response, and `message/stream` streams | `test_auth.py::test_missing_token_401`, `::test_wrong_token_401`, `::test_right_token_streams` |
| C5 | `OPTIONS` preflight passes without a token, and the response allows `Authorization` | `test_auth.py::test_preflight_without_token_allows_authorization` |
| C6 | A 401 carries `Access-Control-Allow-Origin` for the configured origin (middleware order) | `test_auth.py::test_401_has_cors_headers` |
| C7 | The card and the static files are public. A wrong-token request to them still gets 200 | `test_auth.py::test_card_and_static_are_public` |
| C8 | Token unset → no gate (the existing test suite passes unchanged) | the existing suite + `test_auth.py::test_no_token_no_gate` |
| C9 | A non-loopback `AGENT_HOST` (e.g. `0.0.0.0`) without a token → `ConfigError` naming `AGENT_ACCESS_TOKEN`. Loopback without a token is fine | `test_config.py::test_public_bind_requires_a_token` |
| C10 | The token never appears in `repr(Settings)`, the startup output or the 401 body | extend `test_config.py::test_settings_repr_hides_the_dsn`; `test_auth.py::test_401_does_not_echo_the_token` |
| C11 | `CLIENT_DIST_DIR` set: `GET /` → `index.html`, and `GET /assets/<file>` is served. `POST /` is still A2A JSON-RPC. The card is still served. A missing directory → `ConfigError` | `test_server.py::test_serves_the_client_dist`, `::test_post_root_is_still_a2a`, `test_config.py::test_missing_client_dist_is_a_config_error` |
| C12 | Unset → no static mount (`GET /` doesn't return HTML) | `test_server.py::test_no_client_dist_no_static` |
| C13 | Client agent URL: `VITE_AGENT_URL` wins. Otherwise production uses `location.origin` and dev uses `127.0.0.1:10002` | `explorer/client/test/agent-url.test.ts` |
| C14 | With a stored token, every agent fetch carries `Authorization: Bearer …` and `X-A2A-Extensions`. Without one, no `Authorization` | `explorer/client/test/a2a.test.ts` (new cases) |
| C15 | A 401 from the agent shows the inline token card (no `window.prompt`/`confirm`). Submitting stores the token and reconnects. A wrong token shows the card again with an error. A `localStorage` that throws still works for the session | `explorer/client/test/token-card.test.ts` |
| C16 | The image builds from the repo root and contains no `.env`, `a2ui/` or tests | `deploy/check-image.sh`: `docker build`, then `docker run … ls` assertions (manual, needs Docker) |
| C17 | The container starts against the local test DB with the stub model, and serves the card (at the public URL) plus `index.html` | `deploy/check-image.sh` (second half) |

Commands: `uv run pytest` in `explorer/agent`, `npm test` in `explorer/client`, and
`deploy/check-image.sh` from the repo root.

## Runbook (manual, in `deploy/README.md`)

- **R1 Database:** create the host per D5. Then `pg_dump -Fc -n mart` locally, followed by
  `pg_restore --no-owner --no-acl`. Create `explorer_ro` with a new password, `default_transaction_read_only`,
  and `USAGE`/`SELECT` on `mart`.
- **R2 Secrets:** set a spend cap on `META_API_KEY`. Run
  `fly secrets set META_API_KEY DATABASE_URL AGENT_ACCESS_TOKEN` with values taken from your shell,
  never from the repo.
- **R3 Launch:** `fly launch --no-deploy --config deploy/fly.toml`, then
  `fly deploy --config deploy/fly.toml --ha=false` from the repo root.
- **R4 Check one machine:** `fly scale count 1` and `fly machine list`.
- **R5 Redeploy:** this drops every open Conversation (ADR 0046), so deploy at quiet times.
- **R6 Rollback:** `fly deploy --image <previous>` (`fly releases` lists them).

## Acceptance on the live deploy

| # | Check |
|---|---|
| M1 | `/.well-known/agent-card.json` shows `url = https://<app>.fly.dev` |
| M2 | Without a token: the page loads, the token card appears, and `POST /` gives 401. With the token: answers work |
| M3 | A scenario question from `datasets/austender/scenarios` renders its chart, table and SQL details. A drill-down and a Board work |
| M4 | Streaming: `curl -N` on `message/stream` shows status events arriving one by one, not in one batch. A Turn of ≥ 60 s isn't cut off |
| M5 | As `explorer_ro` on the hosted DB, a write fails and a read works. Row counts match local |
| M6 | Agent logs show prompt and cached tokens per call (caching still works) and no secrets or DSNs |
| M7 | Time to first status and total Turn time are within about 20% of local for the same scenario. If not, check the region and the DB |
| M8 | `fly machine status` memory after 10 Turns decides D9 |

## Open

1. The D5 database host. If it's RDS, check whether the AWS account was opened before 2025-07-15.
2. The Fly cost (about US$8–9/month at 1 GB in `syd`) is not free. Is that acceptable?
3. `[build] dockerfile` path resolution with `--config deploy/fly.toml` is undocumented. The R3
   rehearsal settles it, and the fallback is `fly deploy --dockerfile deploy/Dockerfile`.
