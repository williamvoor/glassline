---
status: accepted
---

# The browser calls the agent directly, with CORS

The client sends A2A requests straight to the agent's URL (`VITE_AGENT_URL`), and the agent allows the client's origin with CORS (`CLIENT_ORIGIN`). This replaces the earlier plan, where the Vite dev server would proxy `/a2a` to the agent. Answers stream over SSE (`message/stream`). We have no evidence that SSE passes through Vite's proxy unbuffered, and the upstream sample shell avoids that proxy too. A direct call is also the path the Node interop test takes: the same client code, URL and SSE stream. Node does not enforce CORS, so the agent's CORS setup is covered separately, by a preflight test and the manual browser check.

## Considered options

- **Vite `server.proxy`:** rejected. SSE through it is unverified, and the agent card's absolute `url` would have to be rewritten to the proxy's origin.
- **A Vite middleware that calls the agent server-side (as in the upstream shell):** rejected. It uses non-streaming `message/send`, so it would lose live status updates, and it adds a second A2A client.

## Consequences

- The agent must keep the CORS middleware, with `allow_headers` covering `X-A2A-Extensions`.
- A deployment (out of scope for v1) needs either the same CORS setup or a reverse proxy that is known to stream.
