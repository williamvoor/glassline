---
status: proposed (2026-11-05)
---

# A shared bearer token gates the public agent

A public explorer without auth lets anyone spend the `META_API_KEY`. The agent therefore requires
`Authorization: Bearer <AGENT_ACCESS_TOKEN>` on every request except the agent card, the client's
static files, and CORS preflights. The client asks for the token on an inline card when the agent
returns 401, and keeps it in `localStorage`. **The agent refuses to start when it binds a
non-loopback address without a token**, so a missing secret can't silently publish an open agent.
With the token unset on `127.0.0.1`, nothing changes for local use or tests.

This is a gate for a small, trusted audience, not user authentication. Everyone shares one token,
nothing is logged per user, and an XSS bug could read the token from `localStorage`. A spend cap on
`META_API_KEY` stays mandatory.

## Considered Options

- **Cloudflare Access.** Needs no code and gives real per-user identity, but it needs a domain on
  Cloudflare, configuration for preflights, and a check that SSE streams through it. It is the
  likely next step if the audience grows.
- **No auth, only a spend cap.** Any visitor could burn the monthly cap in minutes.
- **Vercel password protection.** Doesn't apply, since the client isn't on Vercel (ADR 0046). Even
  if it were, it wouldn't protect the agent.
