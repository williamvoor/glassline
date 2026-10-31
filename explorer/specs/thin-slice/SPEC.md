# SPEC: thin slice (build order step 1)

Status: draft for build, 2026-10-31. Parent: `explorer/SPEC.md` §8 "Build order" item 1. Where
this file is silent, the parent spec applies. Decisions: ADR 0007.

## Goal

A stub model's fixed Express reply goes through a minimal real UI pipeline, then over A2A
(`message/stream`) to a Vite + Lit client. The client renders the prose, a chart, a table, a
metric and the SQL in the browser. There is no database and no LLM. The stub server seeds
a fixture Result `r1`. This retires the A2A interop risk first.

## Files

Agent (`explorer/agent/`):
- `pyproject.toml`, `uv.lock`: take `a2ui-a2a` from the same A2UI git commit as the SDK
  (`python/a2ui_a2a`). PyPI 0.1.0 installs, but it fails to import against the pin
  (`ImportError: parse_and_fix`). Add `pytest-asyncio` (exact pin) to dev.
- `src/explorer_agent/ui.py` (new): `UiPipeline`.
- `src/explorer_agent/executor.py` (new): `Conversation`, `Conversations`,
  `ExplorerExecutor`.
- `src/explorer_agent/server.py` (new): `agent_card()` and `build_app()`. No `__main__` yet,
  because the real entry point needs `config.py` and `llm.py` (step 3).
- `src/explorer_agent/testing/{__init__.py, stub_server.py}` (new): the fixture `r1`, the
  fixed reply, the stub answerer, and `python -m explorer_agent.testing.stub_server --port N`.
- `tests/test_ui.py`, `tests/test_server.py` (new).

Client (`explorer/client/`, new): the parent spec §8 layout, minus `vite.config.ts`'s proxy
(ADR 0007), plus `test/fixtures/slice_transport.json`.

Docs: `explorer/SPEC.md` §8 (proxy → direct with CORS; `a2ui-a2a` source) and §12. ADR 0006 "Consequences" wording. ADR 0007 (new).

## Behavior

### `ui.py`
`UiPipeline(currency_symbol="$")`. `build(reply, store, surface_id) -> UiResult`, or it
raises `UiError(messages: list[str])`.

1. Parse with the Express parser for the macro inference catalog. The model's surface is
   always `main`.
2. Join the `TextPart`s, strip them, and `fill_prose` them inside `macro_context(store,
   currency_symbol=…)`. Log each bare number at WARNING (`explorer_agent.ui`), with the
   numbers only, not the prose.
3. Expand macros (`transform_to_transport`). If the context's error list is non-empty
   (macro or prose placeholder errors), raise `UiError` with those errors.
4. Rewrite `surfaceId` in every message that has one to `surface_id`.
5. Validate with a fresh `MessageProcessor([explorer_catalog()],
   STRICT_VALIDATION)`. On failure, raise `UiError` with the validation message.
6. Return `UiResult(prose: str, messages: list[dict], bare_numbers: list[str])`. A reply
   with no `<a2ui>` block is valid: it gives `messages == []`.

Any SDK error (`a2ui.core.exceptions.A2uiError`) raised while parsing or expanding becomes
`UiError`: syntax errors, dangling references, bad macro argument types, and duplicate
surfaces (two `<a2ui>` blocks in one reply). Any other exception propagates. The executor
catches it.

### `executor.py`
- `Conversation`: `id`, `store: ResultStore`, `turns: int`. `Conversations`: a dict by
  context ID. It has no LRU cap in this slice (non-goal).
- `Answerer = Callable[[Conversation, str, StatusFn], Awaitable[str]]` takes the user text
  and returns the final reply text. `StatusFn = Callable[[str], Awaitable[None]]`. This is
  the seam where `llm.py` plugs in at step 3.
- `ExplorerExecutor(answerer, conversations, pipeline, card)`. For each message:
  1. Create the task if it is new, then start work.
  2. `ui_on = try_activate_a2ui_extension(context, card)` is not None, and the message
     metadata `a2uiClientCapabilities["v0.9"]["supportedCatalogIds"]` contains
     `EXPLORER_CATALOG_ID`. Missing or malformed metadata counts as not supported.
  3. Get or create the Conversation for `context_id`. Increment `turns`. The surface ID is
     `turn-<turns>`.
  4. `reply = await answerer(conv, text, status)`. `status(text)` sends a
     `working` status update with that text.
  5. `UiPipeline.build(reply, conv.store, surface_id)`. Then complete with one message: a
     `TextPart` with the filled prose, followed by one `create_a2ui_part(msg)` per
     transport message, but only when `ui_on`. When `ui_on` is false, send the prose only.
  6. On `UiError`: complete with a text message. It starts `I couldn't build the view for
     this answer:` and lists the errors. No retries (non-goal).
  7. On any other exception: log it with its traceback, and complete with
     `Something went wrong while answering. Please try again.` The server keeps running.

### `server.py`
- `agent_card(url)`:
  - name `Explorer`, `streaming=True`, text in and out, no skills.
  - Extensions: `get_a2ui_agent_extension("0.9", supported_catalog_ids=[EXPLORER_CATALOG_ID])`.
- `build_app(answerer, *, url, client_origin)`:
  - A2AStarletteApplication with DefaultRequestHandler and InMemoryTaskStore. RPC at `/`.
  - `CORSMiddleware(allow_origins=[client_origin], allow_methods=["*"], allow_headers=["*"])`.

### `testing/stub_server.py`
- Fixture `r1`, purpose "top suppliers (fixture)", with SQL text shown but never run:
  - Columns `supplier text`, `contracts bigint`, `value numeric`.
  - Three rows with neutral names: "Supplier A", "Supplier B" and "Supplier C".
- The stub answerer:
  - On the first turn of a Conversation, it seeds `r1` if the store is empty.
  - It sends one status, `Running SQL: top suppliers (fixture)`.
  - It returns `FIXED_REPLY`.
- `FIXED_REPLY`:
  - Prose: one sentence with `{r1[0].supplier}` and `{r1[0].value|currency_m}`.
  - `<a2ui>` block: a `Metric`, a `BarChart` (`currency_m`) and a `DataTable`, in a `Column`.
- `main()`:
  - Takes `--host` (default `127.0.0.1`), `--port` (required) and `--client-origin`
    (default `http://127.0.0.1:5173`).
  - Runs uvicorn with log level warning.
  - Prints `READY <url>` on stdout once the server is listening, so the interop test can
    wait for it.

### Client
- Built with Vite + Lit and the parent spec §8 dependencies. The agent URL comes from
  `VITE_AGENT_URL`, default `http://127.0.0.1:10002`.
- `a2a.ts`:
  - `connect(agentUrl, capabilities, fetchImpl?)` uses `A2AClient.fromCardUrl`, with a fetch wrapper that
    sets `X-A2A-Extensions: https://a2ui.org/a2a-extension/a2ui/v0.9` on every request.
  - `ask(text, contextId?)` is an async generator over `sendMessageStream`.
    - It sends `metadata.a2uiClientCapabilities = processor.getRendererCapabilities({versions:['v0.9']})`.
    - It yields `{kind:'status', text}` for working updates that carry text, then one
      `{kind:'final', contextId, prose, a2ui: object[]}`.
  - A2UI parts are data parts whose `metadata.mimeType` is `application/a2ui+json` or
    `application/json+a2ui`.
- `catalog.ts`: the merged catalog `urn:explorer:catalog:v1`, made of basic components and
  functions plus `VegaChart`, `DataGrid` and `SqlDetails`. The zod schemas match
  `catalog/explorer_custom_components.json`.
- `components/data-grid.ts`: a `<table>` with header labels, `align:end` → right-aligned,
  an optional `<caption>`, and cells as text (Lit escaping).
- `components/sql-details.ts`: `<details><summary>SQL — {summary}</summary><pre>{sql}</pre></details>`.
  The summary is `SQL` alone when no summary is given.
- `vendor/vegachart/`: copied from `spike/client-probe/src/vegachart/` with the
  Apache-2.0 header kept, plus `LICENSE` and `NOTICE` (imports changed for `@a2ui/*@0.14.0`).
- `markdown.ts`: `@a2ui/markdown-it`'s `renderMarkdown`, with `<img>` elements removed from
  the output. It is provided as `Context.markdown`, and the turn's prose uses it too.
- `app.ts` (`<explorer-app>`):
  - An input box and a list of turns. Each turn shows the user text, the latest status
    while running, the prose (markdown) and its `<a2ui-surface>`.
  - Earlier turns stay rendered, because each turn has its own surface ID.
  - The context ID from the first reply is reused.
  - Errors (network or agent) show as a turn error line.

## Criteria → tests

Agent: `cd explorer/agent && uv run pytest`.

| # | Criterion | Test |
|---|---|---|
| A1 | `FIXED_REPLY` + `r1` → transport with `createSurface.catalogId == EXPLORER_CATALOG_ID`, every `surfaceId == "turn-1"`, no `main`, `VegaChart`/`DataGrid`/`SqlDetails`/`Card` present and no macro names left; prose has the filled supplier and `$…M` value | `test_ui.py::test_fixed_reply_builds_turn_surface` |
| A2 | A model that names `surface("main")` or omits `surface(...)` → same rewrite | `test_ui.py::test_surface_is_rewritten_whatever_the_model_names` |
| A3 | Unknown result ID in a macro → `UiError` whose message names `r9` and the valid IDs | `test_ui.py::test_macro_error_raises_ui_error` |
| A4 | Bad placeholder in prose → `UiError` | `test_ui.py::test_prose_placeholder_error_raises_ui_error` |
| A5 | Malformed Express → `UiError` (not a crash), including dangling child, two blocks, bad macro argument types | `test_ui.py::test_malformed_express_raises_ui_error`, `test_ui.py::test_any_sdk_error_in_the_reply_raises_ui_error` |
| A6 | Prose only, no `<a2ui>` → `messages == []` | `test_ui.py::test_prose_only_reply` |
| A7 | Bare number in prose → WARNING logged with the number; build still succeeds | `test_ui.py::test_bare_number_is_logged` |
| A8 | Strict validation failure → `UiError` (catalog-invalid component reaching validation) | `test_ui.py::test_validation_failure_raises_ui_error` |
| A9 | Agent card: streaming, A2UI v0.9 extension URI, `supportedCatalogIds == [EXPLORER_CATALOG_ID]` | `test_server.py::test_agent_card` |
| A10 | `message/stream` with header + capabilities → a `working` update with text `Running SQL: top suppliers (fixture)`, then `completed` whose message has a text part (filled prose) then data parts with an A2UI mimeType whose messages carry `turn-1` | `test_server.py::test_stream_returns_status_then_prose_and_a2ui` |
| A11 | Same `contextId` twice → second reply's surface is `turn-2`; `r1` still resolves | `test_server.py::test_second_turn_gets_its_own_surface` |
| A12 | No extension header, or capabilities without our catalog → prose only, no data parts | `test_server.py::test_without_a2ui_support_prose_only` (parametrized) |
| A13 | Answerer returns a reply with a macro error → completed with the "couldn't build the view" text listing the error | `test_server.py::test_ui_error_completes_with_explanation` |
| A14 | Answerer raises → completed with the generic message; a following request still works | `test_server.py::test_answerer_exception_does_not_kill_server` |
| A15 | CORS preflight from `client_origin` with `X-A2A-Extensions` is allowed | `test_server.py::test_cors_preflight` |
| A16 | The committed client fixture equals the stub's pipeline output for turn 1 (`UPDATE_FIXTURES=1` rewrites it) | `test_ui.py::test_client_fixture_is_current` |

Client: `cd explorer/client && npm run build && npm test`.

| # | Criterion | Test |
|---|---|---|
| C1 | Catalog has basic components plus `VegaChart`, `DataGrid`, `SqlDetails`; custom names and property names match the JSON file | `test/catalog.test.ts` |
| C2 | The fixture transport renders: text, an `a2ui-vega-chart` element (embed stubbed), a `<table>` with 3 body rows and the right headers, a `<details>` with the SQL | `test/render.test.ts` |
| C3 | `DataGrid` shows `<b>x</b>` in a cell and caption as text; `align:end` right-aligns; no caption when absent | `test/data-grid.test.ts` |
| C4 | `connect`/`ask` send the extension header on card and RPC requests, and `a2uiClientCapabilities` with our catalog ID (stub fetch) | `test/a2a.test.ts` |
| C5 | Markdown renderer drops `<img>`, keeps links and emphasis, no raw HTML | `test/markdown.test.ts` |
| C6 | **Interop**: spawn `uv run python -m explorer_agent.testing.stub_server --port <free>` in `../agent`, `ask` through `@a2a-js/sdk` in Node → a status event with the fixture text, a final event with prose and A2UI messages for `turn-1`; the A2UI messages feed a `MessageProcessor` with our catalog without error | `test/interop.test.ts` |
| C7 | `npm run build` (tsc + vite build) passes | the command |

Manual check (not a test): run the stub server on 10002 and `npm run dev`, then ask
anything. The chart, table, metric and SQL should render in a browser. I'll take a
screenshot.

## Decisions

- **Direct, not proxied:** the browser calls the agent directly with CORS. There is no Vite
  proxy (ADR 0007).
- **`a2ui-a2a` from the git pin**, not PyPI 0.1.0, which can't be imported.
- **Surface ID:** the parser always uses `main`, and the pipeline rewrites it afterwards
  (parent §6). It does not parse with the turn ID, because a model that names its own
  surface would bypass that.
- **Version string:** whatever the SDK's Express compiler emits by default. It is not
  forced.
- **Answerer seam:** in place of `llm.py`. The stub answerer is the only implementation in
  this slice.
- **Client test DOM:** happy-dom. If `<a2ui-surface>` or Lit can't render there, switch to
  jsdom and amend this line. That is not a defect.
- **Non-goals** (later steps):
  - The UI repair retries and `MAX_TOOL_ITERATIONS`.
  - The unknown-prop check (beyond STRICT validation).
  - The `MAX_CONVERSATIONS` LRU and the unknown-`contextId` note.
  - `config.py`/`dataset.toml` (currency stays `$`).
  - `python -m explorer_agent`.
  - Status text for SQL failures.
  - Any database or LLM.
- **Ambiguities resolved during the build:**
  - `connect` takes the renderer capabilities as an argument. The processor lives in the app,
    and the wrapper stays testable without it.
  - Two `<a2ui>` blocks in one reply are an error (`UiError`), even when they name
    different surfaces. A turn has one surface.
  - The empty transport (a prose-only reply) is not validated, because the SDK processor
    rejects an empty message list.
  - happy-dom works for the render tests, so the client tests stay on happy-dom.
  - `SqlDetails` wraps the SQL in `<pre><code>`.
- **Deferred from review:**
  - After an agent restart, the client still holds its `contextId` and surfaces. The new
    Conversation restarts at `turn-1`, which collides with the old surface. This belongs
    with the unknown-`contextId` handling in step 3.
  - `cancel` is not supported.
  - `httpx` and `starlette` are used as transitive dependencies.
