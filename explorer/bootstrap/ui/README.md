# explorer-bootstrap-ui

The browser UI of the dataset bootstrap tool: Connect → Report → Review. Spec:
[`../SPEC.md`](../SPEC.md) (phase 2c). It talks only to the bootstrap server's `/api`, on the
same origin; it doesn't import from `explorer/client`.

```bash
npm ci
npm run build                 # type check + bundle into dist/, which the server serves
npm test                      # vitest + happy-dom; fetch is stubbed, no server needed
npm run dev                   # Vite on http://127.0.0.1:5174, proxying /api to the server on 8765
```

Run the server first (`cd ../server && uv run python -m explorer_bootstrap serve`), then open
http://127.0.0.1:8765/ (built UI) or the Vite dev URL.
