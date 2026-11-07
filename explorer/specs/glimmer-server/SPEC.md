# Glimmer server (SPEC §19, Stage A1, first slice)

Status: **built and stopped** (2026-11-05). The server was built and committed (`c67e396`), then the split was stopped: the server measured 31–117 s to the first token (old issue 11, closed; `docs/issues/11.md` in git history). The code is kept, and nothing in the agent calls it. The user chose **option (a)** for s225 (ADR 0042). §19 and ADR 0031 are still draft/proposed. Stage 0, the paper check on speed, has not been run. This slice is only the server, so it is useful whatever Stage 0 shows. The agent client, Brief, grammar builder and fallback are later slices. Where this file is silent, `explorer/SPEC.md` §19 applies.

## Why

The public inference API doesn't serve Muse Glimmer. The explorer reaches it on Fireworks' serverless training sampler, by checkpoint path, through the Fireworks training SDK. That SDK's pins don't fit the agent's environment (ADR 0015), so a small local process, the **Glimmer server**, holds the sampler, and the agent calls it over HTTP.

The two Glimmer setups (ADR 0042) differ only in what the agent sends: the checkpoint, the examples and the grammar. The server is the same for both.

## Files

| Path | Role |
|---|---|
| `explorer/glimmer/pyproject.toml`, `uv.lock` | Separate `uv` project, Python 3.13, flat layout (`package = false`, like the trainer). Pins `fireworks-ai[training]==1.2.30` and the cookbook commit `57968a2`, the same as `glimmer-tune/trainer` |
| `explorer/glimmer/glimmer_server/app.py` | HTTP handling, request validation and logging. No Fireworks imports |
| `explorer/glimmer/glimmer_server/settings.py` | Reads the settings from `.env` and the environment |
| `explorer/glimmer/glimmer_server/fireworks.py` | The real backend: renderer, tokenizer, sampler. Imported only by `__main__` |
| `explorer/glimmer/glimmer_server/__main__.py` | `python -m glimmer_server`: load the settings, build the backend, serve |
| `explorer/glimmer/tests/` | Stub backend only. No test calls Fireworks or loads the SDK |
| `explorer/glimmer/README.md` | How to run it |
| `explorer/.env.example` | `GLIMMER_URL`, `GLIMMER_CHECKPOINT` (placeholders) |

## Behavior

- **Startup:**
  - reads `FIREWORKS_API_KEY`, `GLIMMER_CHECKPOINT` and `GLIMMER_URL` (default `http://127.0.0.1:8765`);
  - takes them from the environment, else from the repo root's `.env` (`EXPLORER_ENV_FILE` overrides the path). A value already in the environment wins.
  - builds the backend **before** it listens: it loads the tokenizer and renderer, and creates **one** sampling client for the checkpoint. That client lives as long as the process.
  - A missing setting, a non-loopback `GLIMMER_URL` host, or a failed backend build exits 1, with a message that never contains the key.
- **Binding:** the server listens only on `127.0.0.1`, on the port from `GLIMMER_URL`. A host other than `127.0.0.1` or `localhost` is refused at startup.
- **`GET /health`:**
  - returns `200 {"ok": true, "checkpoint": "<label>", "served": <n>, "errors": <n>}`;
  - `label` is the last path segment of the checkpoint (e.g. `s225-2939ff3b`), never the full path.
- **`POST /surface`:**
  - **Body:** `{"messages": [{"role", "content"}...], "max_tokens"?: int, "grammar"?: str, "cache_key": str}`.
    - Roles are `system`, `user` and `assistant`. The content is a string.
    - The first message is `system`, and it contains "Reasoning strength: low" (case-insensitive). Without a system message the `muse_glimmer` renderer adds a default one with today's date, which breaks the cached prefix. Without a reasoning line it appends "high". So the server fails closed (refuter round 1).
    - The last message is `user`.
    - `max_tokens` is between 1 and 4096, default 1536 (`GLIMMER_MAX_TOKENS` in §19).
    - `grammar` is non-empty GBNF text. When present it is sent as `response_format: {"type": "grammar", "grammar": ...}`.
    - `cache_key` is required, 1–128 characters, and sent as `prompt_cache_key`.
  - **What it does:** renders the messages with the `muse_glimmer` renderer and samples **greedily** (temperature 0) with the renderer's stop tokens. It parses the reply with the renderer and returns its text content.
  - **200:** `{"text", "finish": "stop"|"length", "tokens": <completion tokens>, "latency_s", "usage": {"prompt_tokens", "cached_prompt_tokens", "completion_tokens"}, "checkpoint": "<label>"}`.
    - `usage` comes from the request's own server metrics (`sample_with_prompt_tokens_result`), never from `drain_metrics`.
    - A count the server didn't report is `null`, not 0. The one exception is `completion_tokens`: when the server doesn't report it, it is filled from the sampled tokens, because that count is known locally. The SDK says serverless may omit the metrics headers. Whether prompt and cached counts arrive is checked live at Stage 0.
  - **Errors:** a JSON body `{"error": "<kind>", "detail": "<text>"}`.
    - 400 for bad JSON or a body that breaks the rules above;
    - 404 for an unknown path;
    - 405 for a wrong method;
    - 413 for a body over 4 MB;
    - 502 when the backend raises. `detail` is redacted (no key) and cut to 300 characters.
    - Every error response closes the connection, so an unread body can't corrupt the next request on a keep-alive connection.
    - A stalled client socket times out after 30 s.
- **Concurrency:** one thread per request (`ThreadingHTTPServer`). Every request uses the one sampler, and the SDK runs them on its own event loop with adaptive concurrency.
- **Logging:** one line per request: method, path, status, seconds, completion tokens.
  - Never logged: the prompt, the reply text, the grammar, the key or the checkpoint path.
  - The standard library's default access log is replaced.
  - The root logger is at WARNING, and only `glimmer_server` logs at INFO. The SDK logs the sampler's model route at INFO, and the checkpoint path must not reach the log.
- **Startup failures** (port in use, backend build) exit 1 with a message that has no key or path. A built backend is closed.

## Criteria → tests

| # | Criterion | Test |
|---|---|---|
| G1 | A valid request reaches the backend with the parsed messages, `max_tokens`, grammar and cache key, and the reply has the documented shape | `test_app.py::test_surface_returns_the_backend_reply` |
| G2 | `max_tokens` defaults to 1536; a missing grammar sends none | `test_app.py::test_surface_defaults` |
| G3 | Bad requests return 400 and never reach the backend: bad JSON, no messages, no first system message, no "Reasoning strength: low" line, an empty grammar, wrong role, non-string content, last message not `user`, `max_tokens` out of range or not an integer (including `true`), grammar not a string, missing or oversize `cache_key` | `test_app.py::test_surface_rejects_bad_requests` (parametrized) |
| G4 | A body over 4 MB is refused with 413 before it is read | `test_app.py::test_surface_refuses_a_huge_body` |
| G5 | A backend failure returns 502 with the key redacted and the detail cut short; `/health` counts it | `test_app.py::test_backend_failure_is_a_redacted_502` |
| G6 | `/health` reports the label, never the full path; unknown paths are 404 and wrong methods are 405 | `test_app.py::test_health_and_routing` |
| G7 | The log has no prompt, reply text, grammar, key or path | `test_app.py::test_log_has_no_prompt_text_or_secrets` |
| G8 | Concurrent requests are served at the same time, not one after another | `test_app.py::test_requests_run_concurrently` |
| G9 | Settings: environment over `.env`, `EXPLORER_ENV_FILE` honoured, a missing key or checkpoint refused, a non-loopback host refused, the default URL and port | `test_settings.py` |
| G10 | The real backend turns an SDK result into the reply: the text from `parse_response`, completion tokens from `full_tokens[prompt_len:]`, `length` finish, `null` usage when the metrics are absent, and grammar and cache key forwarded to `sample_with_prompt_tokens_result` | `test_fireworks.py`, with fake renderer and sampler objects; the SDK is not imported |
| G11 | The label is the last path segment | `test_settings.py::test_checkpoint_label` |
| G12 | An error response on a keep-alive connection doesn't corrupt the next request | `test_app.py::test_early_errors_keep_the_connection_usable_or_close_it` |
| G13 | Startup: a missing setting, a failed build or a port in use exits 1 with no key or path, and the backend is closed. Third-party INFO logs are off | `test_main.py` |
| G14 | `completion_tokens` falls back to the sampled count when the server doesn't report it | `test_fireworks.py::test_length_finish_no_grammar_and_missing_usage_is_null` |

## Decisions

- **Standard library HTTP** (`http.server.ThreadingHTTPServer`), with no web framework. The server is local, has one user and two routes. One fewer dependency next to the SDK's pins.
- **Usage per request** comes from `DeploymentSampler.sample_with_prompt_tokens_result` (the researcher's finding, 2026-11-05). The trainer's `drain_metrics` is global and can't attribute usage when requests run concurrently.
- **The server is the same for both setups.** Examples and grammar come from the agent in every request. The server doesn't know about macros, Briefs or "Reasoning strength: low", which is in Glimmer's system prompt (the agent's job, §19 "Glimmer's prompt").
- **The private SDK hook stays.** `sampler._submit(...)` and `sampler.deployment_sampler` are the trainer's proven path. Both are behind the exact `fireworks-ai` pin. A pin change must recheck them.
- **No server-side timeout.** The SDK retries 408/429/5xx up to 7 times, and a missing checkpoint can retry for about 50 s. The agent's `GLIMMER_TIMEOUT_S` decides when to fall back to Spark. A request the agent abandons may still finish and be billed. That is accepted for a measurement setup.
- **Deferred (refuter round 1, MINOR):**
  - an SDK finish other than `length` maps to `stop`;
  - `GLIMMER_URL` with port 0 falls back to 8765.
- **Greedy only.** No temperature, top_p or n: the agent needs one Surface, and §19 says greedy.
- **Non-goals in this slice:** the agent's HTTP client, the Brief, the grammar builder, Glimmer's prompt and examples, Drill and FollowUp injection, the fallback, and A2. Also: authentication (loopback only), TLS, and the Fireworks terms question (§19 Unknowns, needs the user).

## Test command

```bash
cd explorer/glimmer && uv sync && uv run pytest
```
