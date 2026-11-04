# Board drafting in the bootstrap tool (SPEC §18 slice 4)

Status: **superseded** (2026-11-04). Approved and moved to `bootstrap/SPEC.md` phase 3, which is
the source of truth. This file is kept as the reviewed draft, and its two open questions were
answered there (the model picks the entities; 3 Entity Boards). It was written here because another
session had uncommitted edits in `bootstrap/*` on 2026-11-04. Where this file is silent, `explorer/SPEC.md`
§18 and `bootstrap/SPEC.md` (phases 1–2c, plus that session's S10, S11, U6, D8 and E5) apply.

Decision: ADR 0032 (proposed). Board drafting is a second Wonder loop with its own tool, run
after the dataset draft.

## Why

ADR 0028 says Boards are written with the Dataset, drafted by the bootstrap tool and reviewed by a
person. The user ruled out hand-written Boards (2026-11-03). Slices 1a–3 built the runtime and
one template (`bootstrap/templates/explorer.toml`). This slice makes the bootstrap produce, check
and preview `datasets/<name>/boards/*.toml` for any database. austender gets its first Boards
here, which unblocks B-M (the speed targets) and the Board scenarios.

## Scope

**In:**
- **Templates** in `bootstrap/templates/`, dataset-agnostic: slots, no SQL, no names.
  - `explorer.toml`, which exists. It gains an optional `[[controls]]` slot block.
  - `entity.toml` (new). Its slots are `key`, `key_type` and `key_columns`, a `scope` line that
    says the Board isn't limited to the clicked answer, 2–3 Metrics, a `Timeline` or trend, and
    a top-N table.
  - `controls.md` (new). It's guidance, not a file the agent loads: which Control kinds suit
    which columns, the typed-NULL "All" idiom, and that option queries take no placeholders.
- **A Boards draft loop** (`boards.py`, new in `bootstrap/server/src/explorer_bootstrap/`).
  - It's the same tool loop as `draft.py` (LiteLLM `acompletion`, `BOOTSTRAP_MODEL`,
    `reasoning_effort`, `prompt_cache_key="bootstrap-boards-<name>"`). Its tools are `run_sql`
    and `submit_boards`.
  - The prompt holds the templates, `controls.md`, §18's Board-file rules (generated from one
    constant, so the rules and the checks don't drift), and the dataset draft: `dataset.toml`,
    notes, glossary, and the schema section with accepted comments.
  - It drafts the Explorer Board and up to `BOOTSTRAP_MAX_ENTITY_BOARDS` (default 3) Entity
    Boards, choosing entities from key-like columns: identifier columns that appear in several
    tables or views.
- **`submit_boards` schema** (ADR 0023 rules: no `oneOf`, `additionalProperties: false`, every
  key required):
  - `{boards: [{name, toml, why}]}`, where `name` is the file stem and `why` is one line for the
    reviewer.
  - `toml` is the whole file as text. A nested schema of Board keys would duplicate
    `parse_board` and drift from it.
- **Checks on submit**, the agent's own code (`explorer_agent.boards`), so a Board that passes
  here passes the agent's startup check:
  - `parse_board` on each file;
  - `load_boards` rules over the set: an Explorer is present, the Explorer has no key, and
    `key_columns` don't overlap;
  - `check_board` (`LIMIT 0` runs, columns, options, Drill queries) through the bootstrap's
    read-only `SqlRunner`;
  - every Board's queries stay inside the draft's `allowed_schemas`.
  - Any problem goes back as the tool error, naming the Board and key, and the loop continues
    (as `submit_draft` does). `BOOTSTRAP_MAX_TOOL_ITERATIONS` still caps the loop.
- **Preview** (no model): for each accepted Board, one cold render with `BoardRenderer` at the
  defaults. An Entity Board takes the key value from the first row of its key column's most
  common table, found by a `SELECT … LIMIT 1` the loop records. The preview reports:
  - per query: rows, truncation, milliseconds, and the first 5 rows as text;
  - per Visual: the Macro, its query, and whether it drew, showed "No rows", or failed;
  - the total time against §18's cold target (1.5 s).
  - It shows no A2UI rendering. The bootstrap UI has no A2UI renderer and doesn't import from
    `explorer/client` (bootstrap SPEC 2c), so the reviewer opens the exported Dataset with
    `./run.sh --real` to see the Boards themselves.
- **Server:**
  - `POST /api/boards/draft` starts the loop. It needs a draft (409 otherwise) and runs one loop
    at a time.
  - `GET /api/boards` returns the files, the check results and the preview.
  - `POST /api/boards/validate` re-checks edited files and re-previews.
  - Boards are kept in memory beside the draft, as the draft is.
- **UI:** a "Boards" step after Review. It has one tab per Board (a monospace `<textarea>`, its
  `why` line, check problems, the preview tables), a "Draft Boards" button with the estimate
  shown first (the 2b `estimate_cost` path), and re-validation 400 ms after the last edit.
- **Export:** `export` writes `boards/<name>.toml` beside the other files when Boards are
  accepted, and refuses when any Board has check problems.
  - With `force` and an existing `boards/`, the whole `boards/` directory is replaced and the
    old one is kept in E5's backup sibling. A Board left out of the new set would otherwise
    linger.
- **Explorer side:**
  - `agent/scripts/scenarios.py` gains Board scenarios: open the Explorer, change a Control,
    open an Entity Board. Each is timed against §18's targets and needs no model.
  - `specs/scenarios/SPEC.md` gets the matching criteria.
- **austender's Boards:**
  - one live draft run (Wonder, needs the user's OK first);
  - review by a person;
  - export to `datasets/austender/boards/`;
  - then B-M (§18) measured and recorded in `explorer/SPEC.md`.

**Out:**
- Rendering A2UI inside the bootstrap UI.
- Editing Boards in a structured form rather than as TOML text.
- Runtime drafting (slice 5, ADR 0030).

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `BOOTSTRAP_MAX_ENTITY_BOARDS` | `3` | Entity Boards per draft |
| `BOOTSTRAP_BOARD_PREVIEW_ROWS` | `5` | Rows per query shown in the preview |

The model, reasoning effort, token and iteration caps, and timeouts are the existing `BOOTSTRAP_*`
settings.

## Files

| File | Change |
|---|---|
| `bootstrap/templates/explorer.toml` | optional `[[controls]]` slot block |
| `bootstrap/templates/entity.toml`, `controls.md` (new) | Entity Board template; Controls guidance |
| `bootstrap/server/src/explorer_bootstrap/boards.py` (new) | prompt, loop, `submit_boards` schema and validation, checks, preview |
| `bootstrap/server/src/explorer_bootstrap/prompts/boards.md` (new) | generic instructions for the Boards loop |
| `bootstrap/server/src/explorer_bootstrap/server.py` | the three endpoints; Boards in state |
| `bootstrap/server/src/explorer_bootstrap/export.py` | `boards/` written, replaced under `force` |
| `bootstrap/server/src/explorer_bootstrap/__main__.py` | `boards` CLI subcommand (draft → check → preview, JSON out) |
| `bootstrap/ui/src/boards.ts` (new), `app.ts`, `api.ts`, `types.ts` | the Boards step |
| `agent/src/explorer_agent/boards.py` | `BOARD_FILE_RULES`: the rules text the prompt embeds, kept beside the checks |
| `agent/scripts/scenarios.py`, `specs/scenarios/SPEC.md` | Board scenarios and timings |
| `bootstrap/SPEC.md` | phase 3, moved from this file on approval |

## Criteria → tests

IDs continue `bootstrap/SPEC.md`'s, after that session's D8, E5, S10, S11 and U6. `(db)` needs
Docker.

| # | Criterion | Test |
|---|---|---|
| T1 | Each template is valid TOML, has its top-level keys before `[[queries]]`, holds no SQL and no dataset words, and loads and passes `check_board` once its slots are filled (B22 extended to `entity.toml` and the `[[controls]]` block) | `agent/tests/test_boards.py` |
| D9 | The Boards prompt holds the templates, `controls.md`, `BOARD_FILE_RULES` and the dataset draft, in a stable order with no per-request values; the rules text in the prompt is `BOARD_FILE_RULES` itself | `test_boards_draft.py` |
| D10 | `submit_boards` follows ADR 0023's schema rules. A file that fails `parse_board`, the set rules, `check_board` or the schema allowlist comes back as a tool error naming the Board and key, and the loop continues. A valid submit ends it. The iteration cap is a `DraftError` | `test_boards_draft.py` (stub model, db) |
| D11 | At most `BOOTSTRAP_MAX_ENTITY_BOARDS` Entity Boards; one more is a tool error | `test_boards_draft.py` |
| D12 | Preview: one cold render per Board; per-query rows, ms and the first rows as text; per-Visual drawn / no rows / failed; the cold-target verdict; an Entity Board previewed with a sample key | `test_boards_draft.py` (db) |
| E6 | Export writes `boards/*.toml`, refuses with check problems, and under `force` replaces `boards/` whole, with the backup | `test_export.py` (db) |
| S12 | The three endpoints: 409 without a draft and while a loop runs; validate re-checks edited text; state lost on restart, as for the draft | `test_server.py` (stub model, db) |
| U7 | The Boards step: a tab per Board, problems and preview shown, re-validation after edits, Export disabled while a Board has problems | `bootstrap/ui/test/boards.test.ts` |
| L5 | `boards` CLI: draft, check and preview, JSON out; the stub model via `COMPLETION` | `test_cli.py` (db) |
| SC1 | Board scenarios run with no model and record open, Control change and Entity Board times against §18's targets | `agent/tests/test_scenarios.py` |
| BM | Manual: austender's Boards drafted (one live run, with the user's OK), reviewed and exported. B-M measured, with median and p90 per target, and recorded in `explorer/SPEC.md` | manual |

## Decisions

- **A second loop, after the dataset draft** (ADR 0032). Boards need the reviewed notes, glossary
  and comments, and their own retries against `check_board`. Folding them into `submit_draft`
  would make one long loop that fails as a whole. Phase 2b's "one loop, no step per file" holds
  for the dataset files.
- **TOML text in the tool call, checked by the agent's own code.** One source of truth for Board
  rules, with no second schema to drift.
- **Preview as tables, not A2UI.** It's cheap, it keeps the bootstrap UI free of the client, and
  the reviewer still sees the real Boards through `run.sh --real` before committing.
- **Replace `boards/` whole on `force`.** A Board left out of the new set must not linger.
- **Open, for review:**
  - Should Entity Boards be chosen by the model, or proposed by a stats query over key-like
    columns and then confirmed by the user? The draft above has the model choose, with `why`
    lines.
  - Is three Entity Boards the right default for austender (supplier, agency, and maybe
    category)?

## Test command

```bash
cd explorer/agent && uv run pytest
cd explorer/bootstrap/server && uv run pytest
cd explorer/bootstrap/ui && npm test && npm run build
```
