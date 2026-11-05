# Explorer client

Vite + Lit investigation view that talks A2A to the explorer agent and renders its A2UI surfaces with the merged catalog (basic + `VegaChart` + `DataGrid` + `SqlDetails`). Start with the [Explorer README](../README.md) for architecture, prerequisites, and a complete setup path. This project requires Node.js 20 or later and npm.

To test by hand, from `explorer/`: `./run.sh --real` starts the real agent (Muse Spark and the dataset database, settings from the repo root `.env`) and this client; `./run.sh` does the same with the stub agent. Then open http://127.0.0.1:5173.

The Sidebar holds the investigation log and composer; the Canvas holds the selected tab. A configured Explorer Board opens first. Questions, Entity Boards, Drill-downs, and Asks open views in tabs; Controls re-query their Board in place. The client also shows Dataset examples (`examples` in `dataset.toml`, sent in the agent card), progress steps and elapsed time, Retry on a failed Turn, and New conversation. Asks require confirmation before a model call.

```bash
npm ci
npm run dev      # http://127.0.0.1:5173
npm run build    # tsc + vite build
npm test         # vitest (happy-dom), including the interop test
```

- The browser calls the agent directly at `VITE_AGENT_URL` (default `http://127.0.0.1:10002`). The agent allows the client origin with CORS (ADR 0007).
- Without a real agent, run the stub from `../agent`: `uv run python -m explorer_agent.testing.stub_server --port 10002`.
- The interop test starts that stub itself, so **`uv` must be on the PATH**.
- `test/fixtures/slice_transport.json` is recorded by the agent's tests. To regenerate it, run `UPDATE_FIXTURES=1 uv run pytest tests/test_ui.py` in `../agent`.
