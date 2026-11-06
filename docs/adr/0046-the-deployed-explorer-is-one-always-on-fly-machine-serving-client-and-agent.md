---
status: proposed (2026-11-05)
---

# The deployed explorer is one always-on Fly machine serving client and agent

The first public deployment runs the agent and the built client in **one container on one Fly.io
machine in `syd`**, always on (`auto_stop_machines = "off"`, exactly one machine). The agent
serves the client's static files from the same origin. The reasons:

- **The agent keeps all state in memory** (Conversations, Results, Board views, tab state), so
  there must be exactly one instance and it must not scale to zero.
- **Answers stream over SSE for up to `TURN_TIMEOUT_S`**, which rules out serverless functions.
- **Speed is the top requirement**, so cold starts and long network distances are out.
- **One origin means no CORS setup and one deploy.** It also avoids Vercel Hobby's terms, which
  are for "non-commercial personal use only" and count a project as commercial when a paid employee
  writes the code.

ADR 0007 still holds: the browser calls the agent directly, with no proxy. In this deployment the
agent's URL is simply the page's own origin.

## Considered Options

- **Vercel (client) + Fly (agent) with CORS.** This was the first draft. It costs nothing more,
  but it puts the Hobby terms at risk, needs CORS with several origins, and means two deploys. It
  stays possible later, because the CORS middleware is still there.
- **Serverless functions (Vercel, Lambda).** Blocked by three linked choices: one SSE connection
  per Turn, in-process state, and a tool loop that runs in one handler. Moving would mean polling
  plus a durable task store, external state keyed by `contextId`, and a resumable loop. That is a
  rewrite, and it gives up prompt-cache warmth and adds cold starts.
- **Render free / scale-to-zero containers.** These cost $0, but a cold start of 30–60 s and
  losing Conversations whenever the app sleeps go against the speed requirement.
- **Cloud Run with min=1.** Equivalent, but the user already has a Fly account.

## Consequences

- Every deploy or restart loses all open Conversations. Users must be told, and deploys should
  happen at quiet times.
- Fly has no free tier. A 1 GB shared-cpu machine in `syd` costs about US$8–9/month. This figure
  is derived, so check it in the dashboard.
- The static files come from one machine in Sydney, not from a CDN.
