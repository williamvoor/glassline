# Glimmer server

A local sidecar that samples Muse Glimmer on Fireworks' serverless training sampler. The explorer agent uses it to write Surfaces (SPEC §19, ADR 0031 and 0042). Spec: `../specs/glimmer-server/SPEC.md`.

It is a separate `uv` project because the Fireworks training SDK's pins don't fit the agent's environment (ADR 0015).

```bash
uv sync
uv run pytest                    # stub backend only: no Fireworks, no network
uv run python -m glimmer_server  # serves on GLIMMER_URL (127.0.0.1 only)
```

**Settings** come from the environment, else from the repo root's `.env` (`EXPLORER_ENV_FILE` overrides the path):
- `FIREWORKS_API_KEY` (required);
- `GLIMMER_CHECKPOINT` (required): a sampler checkpoint path. Run 2's `s225` and base `s000` are in `glimmer-tune/runs/sft2/manifest.json`;
- `GLIMMER_URL` (default `http://127.0.0.1:8765`).

**API:**
- `GET /health` returns `{ok, checkpoint, served, errors}`.
- `POST /surface` takes `{messages, max_tokens?, grammar?, cache_key}` and returns `{text, finish, tokens, latency_s, usage, checkpoint}`.
- Sampling is greedy. The grammar is GBNF, sent as `response_format`.

**Two setups** (ADR 0042). Each server process serves one checkpoint:
- **s225 alone:** `GLIMMER_CHECKPOINT` = s225, and the agent sends no examples and no grammar.
- **Base + 3 + grammar:** `GLIMMER_CHECKPOINT` = s000, and the agent sends 3 macro examples and a grammar.

❗️ **Sampling is billed** (Glimmer 30B on the training SDK: $1.96 per 1M prefill tokens, $0.39 cached, $4.88 sampled). Starting the server costs nothing; each `/surface` call does.

❗️ Whether Fireworks allows serving from the training sampler is still unknown (SPEC §19 Unknowns).
