# Explorer client

Vite + Lit chat view that talks A2A to the explorer agent and renders its A2UI surfaces with the merged catalog (basic + `VegaChart` + `DataGrid` + `SqlDetails`).

```bash
npm install
npm run dev      # http://127.0.0.1:5173
npm run build    # tsc + vite build
npm test         # vitest (happy-dom), including the interop test
```

- The browser calls the agent directly at `VITE_AGENT_URL` (default `http://127.0.0.1:10002`). The agent allows the client origin with CORS (ADR 0007).
- Without a real agent, run the stub from `../agent`: `uv run python -m explorer_agent.testing.stub_server --port 10002`.
- The interop test starts that stub itself, so **`uv` must be on the PATH**.
- `test/fixtures/slice_transport.json` is recorded by the agent's tests. To regenerate it, run `UPDATE_FIXTURES=1 uv run pytest tests/test_ui.py` in `../agent`.
