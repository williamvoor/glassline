# Explorer maintainability and extensibility review

## Implementation status (2026-11-07)

The work was implemented in two batches, with the correctness fixes committed before the
library replacements. The original review and pre-fix recheck below remain dated evidence.

- **`9a63b3e` — correctness:** fixes all six findings. Control rejection/settlement identify
  their request sequence and preserve newer intent; Board and Review saves are serialized;
  Board navigation waits for running and pending saves and stays put on failure; export
  captures Draft files, comments, claims, Boards and connection/target inputs before awaiting
  checks, then writes that snapshot. Static Macro argument/format rules are shared with
  startup, CLI rendering resolves Control defaults, and empty Results use scanner-aware SQL
  wrapping. New regression tests cover these paths, including both export modes.
- **`485d508` — libraries:** replaces structural tool/edited-claim checks with `jsonschema`,
  bootstrap identifier/literal formatting with Psycopg composition, and per-line SSE decoding
  with `httpx-sse`. Adds Zod validation at selected Settings response boundaries (connection,
  model/Workspace lists, estimates and validation) and A2A metadata fields. Successful non-JSON
  Settings responses now fail explicitly. Complex Draft/report/Board responses still use the
  existing interfaces; this was a targeted Zod migration, not whole-envelope validation.
  Python imports are declared directly and both lockfiles were refreshed offline without
  upgrading packages. No new browser dependency was added.

Schema semantics remain explicit: object/column membership, blanks, duplicates, finite
values, tolerances, SQL requirements and Board-specific rules. Edited claims retain optional
`dismissed`/`result` fields. Schema diagnostics remain bounded lists with field paths, but
their wording intentionally changes and malformed values are not echoed. A 262-case
old/new acceptance comparison found one difference: the demonstrated extra nested file
field is now rejected. Provider tool schemas and prompt snapshots were not changed.

Verification after the library batch: **1,094 agent unit tests** (`not db and not live_db`),
**271 bootstrap tests** (full suite with disposable Postgres), **246 client tests** (including
stub-agent interop), and **72 SSE/prompt tests** (including database-backed deterministic
prefix/snapshot checks) passed. The correctness batch also passed **39 SQL/CLI integration
tests**. The client production build passed; its main JavaScript chunk changed from 218.74
to 219.25 kB gzip between the two batches. Existing client tests still emit localhost
connection errors from Settings mounts, and the build retains its large-chunk warning.
No live model or deployment acceptance run was made.

The optional Pydantic/settings, Lit Task, debounce and TOML-writer migrations were not adopted:
the fixes use the existing settings contracts, request counters, native timers and serializers.
Task/debounce would not replace the newly required persistence ordering. No maintenance or
bundle benefit was demonstrated that warranted those additional migrations in this batch.
The larger shared-render/interface/module refactor recommendations remain future work.

Reviewed 2026-11-04 against working-tree code at HEAD `57e1a34`.

This is a review of the current module, as requested, rather than a regression review against a base branch. Existing working-tree changes were present before the review. Scope: the agent runtime, Lit client, bootstrap server and UI, their relevant tests, and their configuration/catalog boundaries. No application code was changed. Priorities describe current impact; they do not attribute defects to a particular commit.

## Current-state recheck (2026-11-06)

Checked against HEAD `f056df4` and the working tree. **All six defect findings remain valid.**
The implementation and coverage claims need the corrections below, particularly because the
standalone bootstrap UI has been retired. This section supersedes current-state assertions in
the original review; the original execution records remain historical evidence. The original
`57e1a34` object is unavailable in this checkout, so its provenance and historical test totals
could not be independently reconstructed.

### Defect verification

| Original finding | Current implementation and fresh evidence | Verdict |
|---|---|---|
| Control failure/request sequence | `client/src/app.ts::ExplorerApp.sendControls` still calls sequence-free `revert()` and `settled()`. A fresh check using the real `DataModel` and `BoardControls` sent choices 1 and 2, reverted/settled request 1, then applied/settled request 2. `applied(2)` returned true, the input remained `all`, and pending was false. | **Confirmed.** |
| Export the validated snapshot | `bootstrap/server/src/explorer_bootstrap/server.py::build_app` still mutates draft files in `apply_edits` outside `boards_lock`; `recheck_boards` captures a Dataset before awaiting, and both export paths subsequently read `state.draft`. A fresh in-process HTTP check exercised **Settings export**, with a stub connection/check and real file validation/export. While the Board check was paused on Allowed schema `fixture`, `/api/validate` accepted an edit to `other`; export then returned 200 and wrote `other`. Both schemas were in the synthetic report. | **Confirmed in the current full-stack export path.** |
| Static/runtime Macro validation | `agent/src/explorer_agent/macros.py::check_columns` now checks `weight` and Metric `trend`, but still does not reject `format="typo"`. With the real parser, `check_board`, renderer and pipeline plus a stub runner, startup accepted an integer Metric and rendering produced `PANEL_UNDRAWABLE`. | **Confirmed; some newer static rules exist, but the demonstrated gap remains.** |
| Flush Board edits before navigation | Current owners are `client/src/settings/boards.ts::BsBoards` and `client/src/settings/wizard.ts::ExplorerWizard`. `goTo` awaits saves only when leaving Review; the Boards Next button calls `go('export')` directly. A fresh fake-clock DOM check edited a Board, clicked Next, and exported before 400 ms: export used the old text. Advancing the clock saved the new text from the disconnected editor. | **Confirmed, with updated ownership and a direct Boards → Export reproduction.** The old Back event no longer exists. |
| Board CLI defaults | `agent/src/explorer_agent/__main__.py::_boards_check` still calls `outcomes(board, fresh=True)` and `build_board` without resolved options/state. A valid number Control (`min=0`, `max=10`, `default=5`) passed startup and built using the runtime sequence, while the CLI's omitted-options build sequence raised `TypeError`. | **Confirmed by a stubbed render-sequence check; no live CLI/database run.** |
| Empty Result SQL normalization | `agent/src/explorer_agent/sql.py::SqlRunner._run` still uses `strip().rstrip(';').rstrip()` in the empty-stream fallback. `pglast` accepted `SELECT 1 AS n WHERE false; -- trailing explanation` and `drill.wrap`'s version; the current fallback wrapper raised `ParseError`. | **Confirmed at the parser level; no live Postgres execution.** |

### Recommendations and library claims

The refactoring evidence remains: initial rendering differs across CLI/runtime/preview;
Macro rules and classification are repeated; `UiPipeline.build` and `MacroContext.store`
still annotate `ResultStore` despite `ResultView`/`_Named` consumers; Board/Drill runners use
`Any`; `llm.py` imports `Conversation` and `StatusFn` from the executor; and
`visual_verdicts` and the CLI still classify failures using rendered English text.
The proposed small interfaces, shared rendering and validators, explicit panel outcomes and
draft revision boundary therefore remain reasonable recommendations. Their exact design and
maintenance benefit have not been measured or proven by this audit.

| Candidate | Current assessment |
|---|---|
| `jsonschema` | **Still applicable.** Manual shape checks remain. Fresh comparison: `files.unexpected` passes `validate_draft_args` but fails `Draft202012Validator(DRAFT_PARAMETERS)`. `jsonschema` remains locked transitively; preserve semantic checks and the separate edited-claims contract. |
| Psycopg SQL composition | **Still applicable.** `quote_ident`, `quote_literal` and both `_ident` helpers remain. Installed `Literal` emits an explicit escape string for a backslash. Consolidation remains useful; the hand-written literal assumes `standard_conforming_strings=on`. This audit did not execute generated SQL under either setting. |
| `httpx-sse` | **Still applicable.** `_exchange` still parses each `data:` line separately. A fresh multiline mock event failed with `JSONDecodeError` in the current helper and decoded correctly with installed `httpx-sse`. The pinned A2A transport still imports `aconnect_sse` and `SSEError`. |
| Zod | **Still applicable, with corrected dependency cost.** `Api.call<T>` moved to `client/src/settings/api.ts` and still casts successful responses. A fresh test returned null for a non-JSON HTTP 200 instead of validating a models response. Settings now shares the client's existing Zod dependency, so it needs **no separate bootstrap-UI dependency**. Metadata casts/manual checks also remain. |
| Pydantic/settings | **Still a scoped evaluation.** Manual Control and settings parsing remain, and both packages are transitively locked. No new finding establishes that replacing settings parsing pays off. Strictness, environment conversion, precedence and sanitized diagnostics remain requirements. |
| `@lit/task` | **Still a scoped evaluation.** Estimates and editor validation use manual request counters/state. The wizard already suppresses stale successful estimates; the suggestion concerns simplifying this machinery and covering its lifecycle. Task cannot order server saves. |
| Debounce | **Still optional.** Timers now live in Settings `review.ts`/`boards.ts` and client `controls.ts`. The demonstrated missing flush/disposal behavior persists. A helper would still need to await running saves and preserve Control policy. |
| TOML writer | **Still optional.** `_toml_string`/manual overlay generation remain. Settings also has `registry.py::render` for generated Workspace TOML, an additional serialization site outside the original candidate. No new serialization defect was established. |

The original distinctions between generic library mechanics and application policy still
hold for the SQL guard, model loop, Board cache/prefetch, formatting/charts, DataGrid,
tabs/actions, Markdown, native HTTP/CLI and filesystem operations. These are design judgments,
not assertions that those areas are defect-free or that alternative libraries were benchmarked.
Existing issues 5, 6, 7 and 10 (now [#17](https://github.com/williamvoor/glassline/issues/17), [#18](https://github.com/williamvoor/glassline/issues/18), [#19](https://github.com/williamvoor/glassline/issues/19) and [#21](https://github.com/williamvoor/glassline/issues/21)) remain marked open; their individual reproductions were not rerun.

Primary documentation was rechecked for [JSON Schema validation](https://python-jsonschema.readthedocs.io/en/stable/validate/),
[Psycopg composition](https://www.psycopg.org/psycopg3/docs/api/sql.html),
[HTTPX SSE](https://github.com/florimondmanca/httpx-sse),
[Zod 3](https://github.com/colinhacks/zod/tree/v3),
[Pydantic strictness](https://docs.pydantic.dev/latest/concepts/strict_mode/),
[Lit Task](https://lit.dev/docs/data/task/),
[Lodash debounce](https://lodash.com/docs/#debounce) and
[Tomli-W](https://github.com/hukkin/tomli-w). This confirms the cited capabilities, not a
completed migration, compatibility certification or performance benefit.

### Coverage corrections and current execution

- The old `bootstrap/ui/` code and test paths are historical. Active UI tests are under
  `client/test/settings/`; wizard tests now own navigation, save failure, polling and export.
  `BsReview.leaveForBoards` has become `BsReview.leave` plus `ExplorerWizard.goTo`.
  Separate bootstrap-UI test/build commands no longer apply.
- The SSE matrix overstates one gap: `test_effort_check.py::test_transport_failures`
  **already tests premature EOF without a final event**. Retain that coverage. Multiline
  events, comments/keepalives, CRLF/chunk/UTF-8 boundaries, malformed JSON, JSON-RPC errors,
  wrong content type and explicit HTTP timeout cases remain missing from that helper's suite.
- The principal coverage gaps remain: mixed Control success/failure ordering; pending Board
  edits and running saves during navigation/disconnect; concurrent draft edit/export snapshots;
  nested schema parity; malformed successful Settings responses; stale estimate failures and
  abort/disconnect behavior; environment-source precedence; and the export edge cases listed
  below. Existing valid/stale editor-response tests do not establish server write ordering.
- `test_drafts.py`, `test_draft.py` and `test_export.py` still have module-level `db` markers,
  hiding database-independent validation/settings/serialization cases from a marker-filtered run.
  The current configuration tests also cover the newer public URL and daily model-run cap;
  the original coverage summary is consequently incomplete rather than an exhaustive inventory.
- Original counts, the original startup socket retry and the original reproduction history
  remain dated records. In particular, `test_unreachable_database_fails_cleanly` is no longer
  a current entrypoint test after the one-mode/Workspace changes.

Fresh runs used the installed environments, `/dev/null` env-file overrides and the local
LiteLLM cost map. No dependencies, application code or committed tests were changed.

| Command / check | Result on this recheck |
|---|---|
| Agent: `.venv/bin/python -m pytest -q -p no:cacheprovider -m 'not db and not live_db' tests/test_config.py tests/test_board_controls.py tests/test_boards.py tests/test_entity_boards.py tests/test_effort_check.py tests/test_prompt.py tests/test_drill.py` | **307 passed, 23 deselected.** |
| Bootstrap server: `.venv/bin/python -m pytest -q -p no:cacheprovider -m 'not db'` | **50 passed, 163 deselected.** |
| Bootstrap server: explicit settings, overlay round-trip and shape selections from the earlier coverage audit | **7 passed** (some overlap the preceding run). |
| Client: `npm test -- --exclude test/interop.test.ts` | **216 passed, 20 test files.** Includes Settings. Existing tests emitted localhost HTTP/CORS errors from Settings mounts: a passing total does not establish full network isolation. |
| Temporary Python reproductions | Confirmed Macro, CLI-sequence, SQL-wrapper and Settings-export defects, schema drift and multiline SSE failure. Stubs/mock HTTP only. |
| Temporary Vitest checks in a `/tmp` copy of the client | **3 passed:** reproduce Control mismatch, navigation/export before Board save plus detached save, and unchecked successful API response. |

The temporary checks assert the observed defects; they are not regression fixes. Their files
were `/tmp/explorer-review-recheck.py` and `/tmp/genui-review-client/test/recheck.test.ts` during
this session. Database integration, live models, interop, builds and browser layout checks were
not run. The six finding verdicts follow from source inspection and the scoped reproductions
above; they do not certify all possible response orderings or migration equivalence.

## Revision assessment (2026-11-04)

A subsequent source review checked the affected implementations, selected tests and library documentation against the current working tree. All six defect findings remain supported by the code. This pass did not rerun the original reproductions or test suites; execution results below belong to the earlier review and coverage audit.

Fix the demonstrated defects with focused regression tests first. JSON Schema validation, Psycopg SQL composition and SSE framing are recommended library changes; Zod is a targeted boundary improvement. Pydantic and Lit Task need a scoped evaluation, while a debounce dependency and TOML writer remain optional. Library availability alone does not establish a net maintenance benefit.

## Findings

### [P2] Make Control failure handling respect the request sequence — explorer/client/src/app.ts:307

`sendControls` calls `ctrl.revert()` for every failed request, without identifying its sequence. If changes 1 and 2 are in flight and request 1 fails, the rollback resets the input values to the last completed render. When request 2 subsequently succeeds, `BoardControls.applied(2)` accepts its panels and updates `displayedRaw`, but never restores those values to the data model. The user sees panels for change 2 with inputs showing the earlier filters; `lastSent` also reflects the rollback. A local reproduction sent option indices 1 and 2, failed 1, then accepted 2: the visible choice remained `all` with no pending request. Make rejection and settlement sequence-aware and reconcile the accepted state with any newer user intent. Cover mixed success/failure response orders, beyond the existing successful stale-response tests in `client/test/controls.test.ts`.

### [P2] Export the same draft snapshot that passed Board validation — explorer/bootstrap/server/src/explorer_bootstrap/server.py:406

`export` awaits `recheck_boards()` under `boards_lock`, but `apply_edits` in `/api/validate` and other exports mutates `state.draft` outside that lock. `recheck_boards` captures a Dataset before awaiting database checks, while `export_dataset` later reads the mutable current draft. Consequently an overlapping edit can change `allowed_schemas` or other files after the Board check starts, and export writes files that were never checked together with those Boards. An in-process HTTP reproduction paused the Board check, submitted an edit, then resumed: the check used version A, the edit returned 200, and export returned 200 with version B. Capture draft files, comments, Boards, claims/dismissals and relevant validation inputs together, then validate and export that exact snapshot; alternatively use one state/revision boundary shared by edits and export. Locking only Board validation does not protect the draft. Snapshot consistency does not establish freshness: separately reject or serialize stale saves so an older request cannot silently replace newer reviewed state.

### [P2] Share structural Macro validation between startup and rendering — explorer/agent/src/explorer_agent/macros.py:1499

`check_columns` duplicates a subset of the runtime Macro rules, and `check_board` relies on it for layout checks. A Board containing `Metric("totals", "n", "Count", format="typo")` passes `check_board` with a valid integer output, but rendering replaces the Metric with “This panel couldn't be drawn from its rows.” This error is independent of the data and should be rejected during authoring/startup; bootstrap also uses that same incomplete check to accept submitted Boards. Extract common validation for static arguments, formats and column roles, then call it from both paths. Retain panel fallback for actual data-dependent failures. The reproduction used the real parser, `check_board`, renderer and UI pipeline with a stub SQL runner; no database or model was called.

### [P2] Flush Board edits before navigating back to Review — explorer/bootstrap/ui/src/boards.ts:133

The Back button immediately dispatches `back`, while edits are saved only by a delayed `validate()` call. Returning to Review before that timer fires leaves the server holding the previous Board text, so an immediate export can write that previous version. The timer and any pending validation also outlive the detached editor, allowing saved Boards to change after the user has moved to Review or exported. A DOM reproduction edited a Board and clicked Back before debounce: navigation fired with zero validation requests and the saved text still unchanged. Introduce an awaited save/flush operation before navigation, navigate only after the save succeeds, and manage pending work on disconnect. Account for already-running saves as well as the debounce timer. Serialize writes or use server revisions so an earlier request cannot overwrite the flushed edit; ignoring stale responses in the client does not order server writes. `BsReview.leaveForBoards` already follows the needed flush-before-leaving pattern in the opposite direction.

### [P2] Use the runtime's resolved Control defaults in the Board CLI — explorer/agent/src/explorer_agent/__main__.py:109

`_boards_check` renders through `renderer.outcomes(board, fresh=True)` and then calls `build_board` without options or state. Runtime opening instead calls `renderer.defaults(board)` and passes the resolved options, state and bound values through both operations. The CLI path supplies empty `Options()` to `default_state`: even a valid number Control with `min=0`, `max=10`, `default=5` raises `TypeError` because its option bounds are `None`. Choice options are also omitted, and range/default resolution is not measured. A stub reproduction built this Board successfully through the runtime sequence and failed through the CLI build sequence. Reuse a small initial Board-render operation across the CLI, runtime opening and bootstrap preview; `test_main.py`'s cold-render tests currently exercise Boards without these Controls.

### [P2] Reuse scanner-aware SQL normalization for empty Results — explorer/agent/src/explorer_agent/sql.py:303

The empty-stream fallback removes a semicolon only when it is the final character. Valid SQL such as `SELECT 1 AS n WHERE false; -- trailing explanation` passes the guard, but the fallback wraps the remaining semicolon inside a subquery and fails to parse. An empty Result is therefore turned into a SQL failure, potentially causing another model round trip. `drill.normalise` and `drill.wrap` already handle semicolons before trailing comments correctly. Move this generic SQL utility to a shared lower-level module and use it here, preserving native parameter binding. The local check confirmed that the original query and the shared wrapper parse, while the current fallback raises a syntax error at the semicolon; this was a parser-level reproduction, not a live Postgres run.

## Overall assessment

The core design uses good patterns: Results remain the source of displayed data; SQL has a central execution boundary; model completion, clocks and sleep are injectable; `SqlOk`/`SqlFail` make ordinary SQL failures explicit; and the UI pipeline separates compilation/validation from the model's Repair loop. Board caching, single-flight tasks and background prefetch address the project's speed requirement directly. Those boundaries are worth preserving.

Maintainability is weakest where later Board features cross those boundaries. Several entry points implement slightly different versions of the same render sequence, validation rules are repeated, and async operations share mutable state without one consistent revision model. The findings above demonstrate consequences of these patterns. Focused refactoring is justified; a framework replacement or broad rewrite is not supported by this review.

## Refactoring and extension priorities

These are design recommendations, not additional defect findings.

| Priority | Refactor | Concrete benefit and boundary |
|---|---|---|
| First | Model async Control requests explicitly | Let one controller own request sequence, optimistic input state, accepted state, failure and settlement. `ExplorerApp` should send events and apply approved transport updates. Resolves the first finding without spreading more flags across the app. |
| First | Give bootstrap draft edits, validation and export a revision or snapshot | Keep route handlers thin and move state transitions into a small service. A Board check should identify the complete snapshot it checked, and export should consume it. Define stale-save rejection or serialization separately from snapshot consistency. This also makes overlapping requests testable without database fixtures. |
| Next | Share a Board render operation | Resolve options/defaults, bind queries, run them, assign instance Result IDs and build the Surface through one explicit sequence. Keep cache policy and A2A reply construction outside it. Do not force partial Control updates, refresh and prefetch through a flag-heavy universal operation. The CLI, runtime opening and bootstrap preview currently repeat this sequence differently. |
| Next | Put static validation beside each Macro's implementation | `MACROS`, `VISUAL_MACROS`, `check_columns`, Board dependency discovery and bootstrap panel classification each encode aspects of Macro behavior. Extract shared validators first. Add a small explicit definition only for metadata demonstrably needed by multiple consumers; the defect alone does not justify a new Macro framework. Keep row-dependent rendering separate. |
| Next | Introduce narrow shared interfaces | `UiPipeline.build` and `MacroContext.store` declare `ResultStore`, but runtime also passes `ResultView` and Board `_Named` lookups. A `ResultLookup` protocol with `get`/`ids` describes the actual contract. A runner protocol would replace `Any` across Board and Drill code. |
| Later | Separate domain state from transport orchestration | `llm.py` imports `Conversation` and `StatusFn` from the A2A executor. Put these contracts in a transport-independent module. Split Board parsing/Controls from rendering/cache at those existing responsibility boundaries; do not split files merely by size. |
| Next, alongside shared rendering | Return panel outcomes as structured metadata | Bootstrap `visual_verdicts` infers failed/empty status from rendered Text, including fixed English messages; the Board CLI also counts fixed fallback text. Explicit panel outcomes would let display wording and layout evolve without changing diagnostic classification. |

An incremental order is preferable: add the missing behavioral checks, fix the six findings, then extract the shared operations and interfaces while preserving those checks. Existing issues [#17](https://github.com/williamvoor/glassline/issues/17), [#18](https://github.com/williamvoor/glassline/issues/18), [#19](https://github.com/williamvoor/glassline/issues/19) and [#21](https://github.com/williamvoor/glassline/issues/21) also intersect Macro metadata, Result lifetime, model outcomes and display bounds. They remain existing issues and are not counted as new findings here.

## Verification and limits

| Check | Result |
|---|---|
| Agent: `.venv/bin/python -m pytest -q -m 'not db and not live_db'` | 860 passed; one startup test was blocked by sandbox socket permissions; 89 deselected. |
| Retry of `tests/test_main.py::test_unreachable_database_fails_cleanly` with local socket access | 1 passed. All 861 selected agent tests therefore passed across the two invocations. |
| Client: `npm test -- --exclude test/interop.test.ts` | 84 passed. |
| Bootstrap server: `.venv/bin/python -m pytest -q -m 'not db'` | 39 passed; 152 deselected. |
| Bootstrap UI: `npm test` | 38 passed. |
| Targeted local reproductions | Confirmed Control rollback mismatch, export snapshot mismatch, static/runtime Macro validation mismatch, navigation before Board save, controlled-Board CLI build failure, and empty-query wrapper parse failure. Used stubs, a DOM environment and in-process HTTP; no model or external service calls. |

No tests were added to the repository. The reproductions were temporary inline scripts; their scenarios and observed outcomes are recorded above. Passing existing tests does not cover these missing cases.

Database-marked tests, live-database tests and the socket-based client interop test were not run. No live model acceptance, production-browser check or build was performed. Historical spike code, upstream `a2ui/`, generated dependencies and evaluation artifacts were outside the detailed review; scenario tooling was only sampled. This is a focused maintainability review of the current implementation, not an exhaustive security or performance audit.

## Addendum: library reuse and “reinventing the wheel”

Requested and scanned 2026-11-04. **Yes: several generic mechanisms should use established libraries, especially ones already present.** The strongest opportunities are input validation, SQL quoting and SSE framing. Domain rules, Result provenance and Board behavior should remain application code. The following are adoption recommendations, not an instruction to install every package.

The scan covered runtime and bootstrap Python, both Lit applications, and the Python scenario/effort transport helper. Library capabilities were checked against primary documentation and, for the pinned A2A SDK, installed source. “Already present” means available in this workspace's dependencies or lockfiles; a package used directly should still be declared as a direct dependency of its consuming project.

### Replacement candidates and adoption judgments

| Order | Language | Current custom implementation | Replacement | Dependency cost / assessment |
|---|---|---|---|---|
| 1 | Python | Generic object/key/type checks in bootstrap `validate_draft_args`, `claim_problems`, `shape_problems` | `jsonschema` against the existing tool schemas | Already locked. Strong recommendation; removes a second implementation of the declared shape. |
| 2 | Python | `quote_ident`, `quote_literal`, and two bootstrap `_ident` helpers | `psycopg.sql.Identifier`, `Literal`, `SQL` | Psycopg already directly used. Strong recommendation; small, isolated change. |
| 3 | Python | Per-line SSE decoding in `effort_check._exchange`, reused by scenario tooling | `httpx-sse`; optionally the pinned A2A client's JSON-RPC transport | Already locked through A2A. Strong recommendation for framing; retain measurement/scoring code. |
| 4 | TS | Response casts and repeated metadata shape checks | Zod schemas and inferred types | Already in the explorer client; would be a new direct dependency in bootstrap UI. Good targeted extension of an existing pattern. |
| 5 | Python | Repeated settings parsing and structural Board/Control validation | Pydantic models; optionally `pydantic-settings` | Both already locked transitively. Conditional. Control shapes are a stronger candidate than settings, whose integer parsing already uses shared helpers. Preserve source/error contracts. |
| 6 | TS | Async status/error flags and latest-response counters for single requests | `@lit/task` | New dependency. Evaluate for estimates, schema loading and validation result display. It does not order persistence writes. |
| 7 | TS | Repeated timer/debounce implementations | Lodash `debounce`, preferably a narrow ESM import | New dependency plus matching TS declarations if needed. Optional. Existing timers are small; repeated timer use alone does not justify a dependency, and domain lifecycle logic remains. |
| 8 | Python | `_toml_string` and hand-built Comment overlay TOML | `tomli-w` | New dependency. Optional serialization cleanup during export work; no demonstrated serialization defect makes it urgent. |

### Python: what to replace and what to retain

**1. Validate declared JSON shapes with `jsonschema`.** In `bootstrap/server/src/explorer_bootstrap/draft_schema.py`, `DRAFT_PARAMETERS` already defines required fields, additional-property rules and expectation alternatives. `validate_draft_args` and `claim_problems` then implement many of those checks again. The duplication has drifted: adding `files.unexpected` to an otherwise acceptable argument object produces no manual-validation errors, while `Draft202012Validator(DRAFT_PARAMETERS).iter_errors(...)` rejects it at `files`. Use a validator constructed once, then run the remaining semantic checks: reported object/column membership, duplicate comment targets, finite values, tolerances and file-content rules. Keep the provider-compatible schema shape required by ADR 0023. `claim_problems` also validates human-edited claims with permitted `dismissed` and `result` fields through `extra_keys`; preserve that separate input contract rather than applying the model-tool schema unchanged. Bound and sanitize diagnostics while retaining useful field paths. [Validator API](https://python-jsonschema.readthedocs.io/en/stable/validate/).

**2. Compose SQL with Psycopg's existing utilities.** Bootstrap `export.py` has `quote_ident`/`quote_literal`; `stats.py` and `boards.py` each have another `_ident`. Replace these formatting mechanics with `psycopg.sql`; `agent/drill.py::display_sql` already uses `Literal`. A local comparison preserved ordinary and embedded-quote identifiers, while a string containing a backslash was emitted as an explicit escape string by Psycopg. The current literal helper instead assumes `standard_conforming_strings`. Keep the allowed object-kind mapping and the generated transaction. Identifier quote-doubling is not itself a demonstrated bug; the benefit there is consolidation. Composition does not resolve qualified-name ambiguity: `name.split(".", 1)` still assumes the schema name contains no dot. Define the supported identifier contract before expanding it. This is for SQL composition and exported scripts; executable query values must continue to use bound parameters. [Psycopg SQL composition](https://www.psycopg.org/psycopg3/docs/api/sql.html).

**3. Delegate SSE framing.** `agent/scripts/effort_check.py::_exchange` calls `json.loads` on each individual line starting with `data:`. SSE is an event framing protocol, so a valid event can contain several data lines. A local mock HTTP response with one JSON event spread over two data lines failed that loop's parsing, while `httpx_sse.aconnect_sse(...).aiter_sse()` reconstructed it correctly. The installed A2A SDK already uses `httpx-sse` in `a2a.client.transports.jsonrpc.JsonRpcTransport`. Start by replacing only the frame reader, preserving the existing HTTP client, headers, wall-clock measurement, final-event checks and failure classification. Explicitly catch/map `httpx_sse.SSEError` (including wrong content type); the current handler catches only HTTPX and JSON decoding errors. Do not implicitly add reconnection or replay behavior. The full SDK transport is an option if typed A2A errors/events also simplify the helper; avoid its legacy client API for a new refactor. [HTTPX SSE usage](https://github.com/florimondmanca/httpx-sse).

**4. Evaluate typed validation for Control shapes and selected configuration/HTTP inputs.** `agent/config.py` and bootstrap `settings.py` have repetitive field declarations, defaults and plumbing, but already centralize integer parsing in helpers. No configuration defect in this review establishes that replacing those helpers is necessary. `boards.py` also manually validates the shape of each Control kind. Pydantic models can express constrained fields, forbidden extra keys and Control alternatives, while custom validators retain SQL/schema rules and dynamic bounds. This complements JSON Schema validation of the existing model-tool contract; do not run both libraries over every payload. Use strict validation where booleans must not become integers. Do not apply strict integer fields directly to raw environment strings without an explicit conversion policy; environment settings and typed Control values have different input contracts. Keep errors sanitized rather than printing raw validation objects. [Pydantic strict mode](https://docs.pydantic.dev/latest/concepts/strict_mode/), [error configuration](https://docs.pydantic.dev/latest/api/config/#pydantic.config.ConfigDict.hide_input_in_errors).

For environment loading, a plain Pydantic model fed the explicitly supplied mapping is the smaller first step. Use `pydantic-settings` only if consolidating environment, dotenv and TOML sources pays off. Preserve `EXPLORER_ENV_FILE`/`BOOTSTRAP_ENV_FILE`, blank-value behavior, environment precedence and tests' isolation from real settings; default `BaseSettings` source discovery would otherwise change that contract. Dataset loading and Board database checks stay explicit. [Settings sources](https://docs.pydantic.dev/latest/concepts/pydantic_settings/#customise-settings-sources).

**5. Consider delegating generated TOML serialization during export work.** `bootstrap/export.py::_toml_string` implements TOML escaping by adapting JSON escaping, and `render_comments_toml` assembles quoted table and column keys manually. If adopted, build the ordered Comment overlay mapping and serialize it with `tomli-w`; keep the existing table/column ordering policy and parse the output with `tomllib` in export tests. Tomli-W respects mapping order but does not preserve comments. Keep drafted Board/layout files as their original text. If editing a human-authored `dataset.toml` structurally becomes necessary, TOMLKit is the style-preserving alternative; introducing both writers now is unnecessary. [Tomli-W](https://github.com/hukkin/tomli-w), [TOMLKit](https://github.com/python-poetry/tomlkit).

### JS/TS: what to replace and what to retain

**6. Extend the existing Zod pattern to wire boundaries.** `client/actions.ts::ActionPropSchema` is already a good example. In contrast, `client/app.ts::tabOf`, `modelOf`, Board metadata extraction and in-place metadata extraction repeatedly inspect unknown objects. Bootstrap `Api.call<T>` simply returns `data as T`, so its TypeScript interfaces do not validate responses. Define schemas at these boundaries and infer the corresponding types, using explicit optionality for older metadata. Bootstrap would need its own dependency declaration. Keep expected forward-compatible fields permissive where intended; do not make the entire A2A envelope strict by accident. Use the existing Zod 3-compatible APIs, rather than coupling this cleanup to a major-version migration. [Zod 3 parsing and type inference](https://github.com/colinhacks/zod/tree/v3).

**7. Evaluate `@lit/task` for read requests and validation result display.** Bootstrap's `refreshEstimate`/`refreshBoardsEstimate`, schema loading, and the two editors' validation requests manually maintain combinations of pending/error/value state or sequence counters. Lit's Task controller provides those states, latest-invocation handling and abort signals. This is a better fit than adopting a new application framework. Keep paid-run initiation explicitly user-triggered, and pass abort signals through the API wrapper when appropriate. A canceled browser request does not undo server-side edits: save ordering and export consistency still need the server revision/snapshot boundary from the main review. Keep the A2A streaming loop and its per-tab status handling; Task is primarily suited to request/response work. [Lit async tasks](https://lit.dev/docs/data/task/).

**8. Evaluate a shared debounce primitive only if it simplifies lifecycle code.** `bootstrap/ui/src/review.ts`, `boards.ts` and `client/controls.ts` each manage timeout cancellation and delayed execution. Lodash's debounce exposes `cancel()` and `flush()`, matching the lifecycle operations needed by the Board editor finding. If adopted, use a narrowly imported implementation, with fake timers retained in tests. Keeping a small local helper is also reasonable; compare the resulting code and bundle cost before adding a dependency. Wire flush into navigation and cancel into disposal. Importantly, flush starts a pending invocation; the application must also track and await an already-running save. Keep slider-versus-toggle policy, Control sequence numbers and optimistic rollback behavior in application code. A debounce library alone does not fix the Control response-order bug. [Lodash debounce](https://lodash.com/docs/#debounce).

### Custom code that is justified

| Area inspected | Recommendation and reason |
|---|---|
| SQL guard, placeholders and NULL checks | Keep the PostgreSQL-specific rules around `pglast` and Psycopg. The parser is already a library; the allowlist, scoped CTE handling, bound placeholders and Drill checks are explorer policy. Replacing them with an ORM or a generic SQL text parser would not remove that policy. Share `normalise`/`wrap` as recommended in the main review. |
| Model tool loop and Repair rules | Keep. The short retry path enforces a Turn deadline, one retry, latency estimates and accounting; Repairs deliberately cannot query. Tenacity can implement async retry mechanics, but the current policy would still need custom callbacks. Reconsider only if several substantial retry policies emerge. [Tenacity](https://tenacity.readthedocs.io/en/latest/). |
| Board cache and prefetch | Keep initially. `cachetools` supplies bounded/TTL caches, but this code needs per-query freshness within a render, pinned Explorer results served through refresh failures, partial success, priority permits and cancellation-aware single-flight. A generic TTL decorator is not equivalent. A storage-only replacement could be evaluated after separating those policies. [Cachetools](https://cachetools.readthedocs.io/en/latest/). |
| Number formatting and chart construction | Keep the current limited vocabulary and shared-unit policy. A locale/formatting library becomes useful if multilingual output is required; it would not by itself enforce one compact currency unit across a Visual. Vega/Vega-Lite already supplies the visualization engine. |
| `DataGrid`, tabs and action dispatch | Keep the small implementations. The current table is preformatted text with actions and share bars, not a sorting/filtering/virtualization framework. A grid or state-machine dependency would add concepts without eliminating most explorer behavior. |
| Markdown and rendering | Already delegated appropriately to Lit, A2UI, `@a2ui/markdown-it` and its sanitizer. The small image-removal policy is application behavior. |
| HTTP wrapper, CLI and export filesystem operations | Keep native `fetch`/HTTPX, existing `argparse`, `tempfile`, `os.replace` and `shutil`. Axios, Typer or a generic atomic-file writer would save little here. File libraries would not solve the multi-file snapshot/export race. |

### Adoption sequence and verification

Start with `jsonschema`, Psycopg composition and `httpx-sse`: they replace generic mechanics with libraries already available and have narrow boundaries. Next extend Zod at selected wire boundaries. Evaluate Pydantic only where it removes substantial duplication without changing contracts. Evaluate `@lit/task` for read/request-display state; debounce and a TOML writer remain optional during associated UI/export work. Any directly imported transitive Python dependency should be promoted to the relevant `pyproject.toml`; update lockfiles only when implementing the change. Measure bundle cost before adding browser packages. No latency or code-size savings are claimed without measurement.

This additional pass made no application, dependency or lockfile changes. Two inline checks reproduced schema-validation drift and multiline SSE parsing failure; a third compared custom SQL quoting with Psycopg. They used installed libraries and mock HTTP, with no live model or database calls. The earlier test results are unchanged; this documentation-only follow-up did not rerun those suites. Candidate APIs were researched, but package upgrades, licenses, vulnerability status and complete migration compatibility were not audited.

## Addendum: test coverage before adopting libraries

Audited 2026-11-04 by reading the relevant test bodies and assertions, including their stubs, fixtures and markers. This assesses **behavioral coverage for migration**, not line coverage; no percentage was measured. A passing test count alone does not establish equivalent behavior, and no proposed replacement has been implemented or compared yet.

**Assessment:** SQL/TOML export and configuration/Control validation have useful existing regression tests. SSE framing, malformed response handling and async lifecycle behavior need additional tests before replacement. None of these recommendations should be treated as certified equivalent merely because the current suites pass.

### Coverage by adoption candidate

The numbers below match the replacement table's order. “Good base” means meaningful observable behavior is asserted, with the remaining gaps stated; “partial” and “weak” require additional characterization before migration.

| # | Replacement | Existing coverage and evidence | Readiness / gaps |
|---|---|---|---|
| 1 | `jsonschema` for tool-argument shapes | [test_drafts.py](../explorer/bootstrap/server/tests/test_drafts.py)::`test_validate_draft_args` covers missing/extra top-level fields, blank files/comments, invalid object/column references, duplicates, malformed expectation kinds, booleans/NaN, negative tolerances and required SQL. `test_validate_draft_args_caps_problems` checks the error cap. [test_boards_draft.py](../explorer/bootstrap/server/tests/test_boards_draft.py)::`test_shape_problems` checks five bad Board submission shapes; `test_submit_boards_schema_follows_adr_0023` checks provider-schema restrictions. | **Partial.** No complete schema-versus-validator acceptance matrix; nested extra fields already escape the manual check. Add boundary cases for every expectation variant, missing/extra nested fields, wrong container types, Infinity and zero/empty values. Preserve semantic rules outside JSON Schema. Most draft-argument tests are database-marked and were excluded from the initial pure run. |
| 2 | Psycopg SQL composition | [test_export.py](../explorer/bootstrap/server/tests/test_export.py)::`test_sql_and_toml_quoting` executes generated comments against Postgres using quoted table/column names and text containing quotes, backslashes, newline, tab, DEL, accented text and an emoji. `test_both_targets_same_comments` compares applied SQL comments with the Comment overlay. [test_drill.py](../explorer/agent/tests/test_drill.py)::`test_display_sql_quotes_values_and_leaves_strings_alone` and `test_display_sql_backslash` already characterize Psycopg literal output elsewhere. | **Good base for export, partial for all helper sites.** The key Postgres export tests have not been executed in this review. Add unusual schema identifiers, side-query/sample-key identifier cases, and an explicit backslash test under `standard_conforming_strings=off`. Assert resulting comments/rows, allowing equivalent SQL spelling. |
| 3 | `httpx-sse` / A2A transport | [test_effort_check.py](../explorer/agent/tests/test_effort_check.py)::`test_ask_against_stub_agent`, `test_ask_sends_extension_and_fresh_conversation`, `test_transport_failures`, `test_odd_final_events`, `test_repairs_counted`, and the `test_act_*` cases check requests, statuses, metadata, final responses and failure classification. They use in-process ASGI or mock HTTP. | **Partial overall; weak for framing.** The `sse()` helper serializes one JSON object per data line, so it cannot expose the demonstrated multiline-event failure. Add multiline data, comments/keepalives, CRLF, byte-chunk boundaries including split UTF-8, malformed JSON, JSON-RPC errors, wrong content type, timeout and premature EOF. If adopting the full SDK transport, also characterize its exception mapping and event ordering. |
| 4 | Zod at TS wire boundaries | [client/test/a2a.test.ts](../explorer/client/test/a2a.test.ts) checks extension headers, capabilities and prose/A2UI splitting. [client/test/app.test.ts](../explorer/client/test/app.test.ts) and [levels.test.ts](../explorer/client/test/levels.test.ts) exercise normal metadata, optional model display, Boards and in-place updates. [data-grid.test.ts](../explorer/client/test/data-grid.test.ts) explicitly checks malformed/missing action properties fail open. Bootstrap's [review.test.ts](https://github.com/williamvoor/glassline/blob/2deb8acdf97e1ad5d961180d9aee578bbd61e61b/explorer/bootstrap/ui/test/review.test.ts) exercises valid responses and server refusal reasons. | **Partial for happy paths; weak for response validation.** There is no dedicated bootstrap `Api` response-contract suite. Add missing/null/wrong-type fields, unknown optional metadata and non-JSON bodies, plus representative server-produced response fixtures. Bootstrap's handwritten `Report` fixtures describe the UI subset of server data; strict schemas must deliberately handle additional server fields rather than rejecting valid responses. |
| 5 | Pydantic / optional settings sources | [test_config.py](../explorer/agent/tests/test_config.py) checks defaults, overrides, empty reasoning effort, bad integers/formats, required Dataset paths, newline normalization, malformed DSNs, secret-free errors/repr and Comment overlay rules. [test_board_controls.py](../explorer/agent/tests/test_board_controls.py) covers every Control kind, strict indices/booleans, ranges, defaults, typed NULLs, multi sorting and strict calendar-date strings. [test_entity_boards.py](../explorer/agent/tests/test_entity_boards.py) covers Entity Board keys. Bootstrap [test_draft.py](../explorer/bootstrap/server/tests/test_draft.py)::`test_settings` covers selected defaults, overrides and errors. | **Good base for configuration/Controls; partial for source loading and HTTP inputs.** Add a full settings-field matrix, whitespace/default handling and real precedence tests using temporary env files. Current entrypoint tests mostly point the override at a nonexistent file. Preserve rejection of numeric strings as Control indices, `True` as an index, compact/week dates, and current secret redaction. Add isolated HTTP-body cases for any routes converted to models. |
| 6 | `@lit/task` | Bootstrap [review.test.ts](https://github.com/williamvoor/glassline/blob/2deb8acdf97e1ad5d961180d9aee578bbd61e61b/explorer/bootstrap/ui/test/review.test.ts) and [boards.test.ts](https://github.com/williamvoor/glassline/blob/2deb8acdf97e1ad5d961180d9aee578bbd61e61b/explorer/bootstrap/ui/test/boards.test.ts) explicitly resolve validation requests out of order and assert the latest result is shown. [app.test.ts](https://github.com/williamvoor/glassline/blob/2deb8acdf97e1ad5d961180d9aee578bbd61e61b/explorer/bootstrap/ui/test/app.test.ts) checks navigation, polling cursors, draft errors, connection/schema success and model defaults. [report.test.ts](https://github.com/williamvoor/glassline/blob/2deb8acdf97e1ad5d961180d9aee578bbd61e61b/explorer/bootstrap/ui/test/report.test.ts) checks estimate requests on load/model changes. | **Partial.** No equivalent out-of-order estimate test, stale rejection after a newer success, abort/disconnect test, or full pending/error/retry transition matrix. Add those and assert that mounting/updating a component never starts a paid draft. Browser cancellation must not be used as proof that a server-side save was canceled. |
| 7 | Shared debounce primitive | [client/test/controls.test.ts](../explorer/client/test/controls.test.ts) uses manual timers to assert slider coalescing, immediate toggle flush, no-op writes, rollback without echo, sequence acceptance, pending state and disposal. Client app tests check focus preservation, single-request failures and successful out-of-order replies. Bootstrap editor tests exercise edits, stale replies and Review save failure. | **Good base for Control policy; weak for editor timing/lifecycle.** Bootstrap editor tests normally set `validateDelay=0`; the disposal test has no timer pending when disposed. Add fake-clock assertions just before/at the delay, drag-back cancellation, flush exactly once before navigation, cancel with a pending timer, an already-running save during navigation, and mixed success/failure Control response orders. Preserve the existing injected timer seam or replace it with fake global timers. |
| 8 | `tomli-w` | [test_export.py](../explorer/bootstrap/server/tests/test_export.py)::`test_sql_and_toml_quoting` parses generated TOML and checks tricky keys/text; `test_overlay_file_loads_with_tricky_text` round-trips it through `load_overlay`. `test_both_targets_same_comments` checks semantic agreement with database comments and excluded rewrites. Export tests also check file sets, backups and Board replacement. | **Good semantic base; partial formatting/edge coverage.** These tests are all module-marked `db`, including the standalone overlay round-trip. Add pure tests for an empty overlay, table-only/column-only entries, stable table/column order, repeated deterministic output and supported control-character escaping. Assert parsed values and preserved draft/Board text; only pin exact serialization where it is an explicit contract. |

### Tests needed to make the migration reviewable

Apply this checklist to the affected change, not as a requirement to complete every candidate's matrix before fixing one defect. Keep correctness fixes and optional library migrations independently reviewable. Differential checks must explain intentional fixes rather than preserve known bugs.

1. **Run the relevant existing baseline first.** Include the database-marked export, draft validation and startup tests when Docker is available. Move database-independent schema/settings/serialization tests into an ordinary unit-test module so the fast suite actually runs them. A module-wide `db` marker currently hides more than database behavior.
2. **Add characterization cases at the public boundary before replacing internals.** Compare accepted/rejected inputs, normalized values, outgoing requests, final metadata, returned error shape and displayed state. Avoid tests that merely assert the chosen library was called. Reference the cases in the matrix above in the migration PR.
3. **Separate compatibility from deliberate fixes.** Existing valid inputs and documented defaults should retain behavior. Rejecting extra nested fields, accepting valid multiline SSE, and correcting save/rollback races are intentional corrections to the demonstrated gaps; record them explicitly rather than freezing the current bugs as expected behavior.
4. **Use differential checks where practical.** Run old and proposed validators over the same valid/invalid corpus and explain every acceptance difference. Parse both TOML outputs and compare mappings; execute both SQL exports against a disposable database and compare comments. Replay identical mock SSE streams and compare status/final records and failure classification, excluding nondeterministic timings/IDs. For UI requests, use deferred promises and fake timers to compare state transitions.
5. **Run integration and stability checks after replacement.** Run the affected Python/TS suites. Include client interop with the stub agent when transport or shared rendering changes, and the affected TS builds when browser code or dependencies change; run both builds for changes spanning both clients. For configuration/schema changes, also retain `agent/tests/test_prompt.py`'s deterministic-prefix snapshots and catalog compatibility checks. Do not regenerate snapshots simply to make a library migration pass; examine any prompt/catalog change first. No live model calls are needed.

Library tests validate the library's own behavior; they cannot replace these application-level parity checks. In particular, a library's cancellation/debounce support does not establish ordering of saved server state or consistency between shown Controls and panels.

### Coverage-audit verification

Test names above were checked against actual assertions, not inferred from filenames. The database integration assertions were inspected but not executed during this audit. The earlier 84 client and 38 bootstrap UI test passes remain the available TS execution evidence; their missing cases are listed explicitly above. No new tests, application changes or dependency changes were made for this audit.

The following existing tests were rerun specifically for this coverage audit, using installed environments:

| Command (from the indicated project directory) | Result |
|---|---|
| Agent: `.venv/bin/python -m pytest -q -m 'not db and not live_db' tests/test_config.py tests/test_board_controls.py tests/test_boards.py tests/test_entity_boards.py tests/test_effort_check.py` | **237 passed, 6 deselected.** Covers the selected configuration, Board/Control and transport-helper baseline; not database integration. |
| Bootstrap server: `.venv/bin/python -m pytest -q tests/test_draft.py::test_settings tests/test_export.py::test_overlay_file_loads_with_tricky_text tests/test_boards_draft.py::test_shape_problems` | **7 passed.** Explicitly selects database-independent cases, including settings and overlay round-trip tests otherwise excluded by their module's `db` marker. No database fixture is used. |
