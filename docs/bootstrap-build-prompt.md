# Build prompt: dataset bootstrap workflow (Muse Wonder drafts, Lit UI)

You are going to spec and then build a dev tool that helps a person add a new Postgres dataset to
the explorer. Write the spec first (`explorer/bootstrap/SPEC.md`), get it approved, then build.
Once the spec is approved it is the source of truth over this prompt.

The work comes in **two phases**. Phase 1 (the report, section 1 step 1) ships on its own before
any phase 2 work starts.

## 0. Decisions already settled (2026-11-02)

Each is recorded; read the ADRs before writing the spec. Use the terms in `CONTEXT.md`
(notably **Data dictionary** and **Comment overlay**).

1. **Model rule.** `CLAUDE.md` allows Muse Wonder only in this bootstrap tool, never in the
   explorer agent's request path (ADR 0020). The LiteLLM model string is
   `meta/muse-wonder-1.0` (verified in the installed LiteLLM 1.106.6: function calling,
   prompt caching, effort `minimal`–`max`, no `none`).
2. **Frontend stack.** Lit + TypeScript on Vite, the same stack and pinned versions as the
   explorer client (`explorer/client/`). It's a separate npm package: it doesn't import from
   `explorer/client/`, and the explorer client doesn't import from it.
3. **Where comments live.** The Data dictionary is the database's own comments with a Comment
   overlay taking precedence, kept in a committed `comments.toml` file (ADR 0019). There is no
   external comments database. This changes the approved `explorer/SPEC.md`: the spec amendment
   comes before any explorer code changes, at the start of phase 2. The bootstrap tool never
   runs DDL on the dataset database.

## 1. What the tool does

A person points the tool at a database and one or more schemas.

**Step 1 (phase 1): Report (deterministic, no model), as a CLI.**

```
uv run python -m explorer_bootstrap report --dataset-dir ../datasets/austender [--json out.json]
uv run python -m explorer_bootstrap report --schemas public,stats [--json out.json]
```

`--dataset-dir` takes the allowed schemas, notes and glossary from an existing dataset, so the
context size is exact. `--schemas` is for a new database: the context size then covers the
schema section only, and the report says so. The report prints a readable summary to the
terminal and writes the full report as JSON. **That JSON is the contract**: step 2's prompt and
the phase 2 UI both read it.

The report shows:
- every table, view and materialized view in the chosen schemas, with columns, types and
  nullability
- which tables and columns **have no comment**. In phase 2, once the Comment overlay exists, also
  each comment's source (database or overlay), with an overlay comment that replaces a database
  comment marked as such.
- whether the login can add comments (it owns the objects, or is superuser) or the database is
  effectively read-only for us. This drives the default comment target in step 2.
- row-count estimates; per-column null fraction, distinct-value estimate and most common values
  (from `pg_stats`, which is fast and needs no table scans; say when a table has no statistics
  because it was never `ANALYZE`d)
- min and max for date and numeric columns, only when cheap (bounded by `statement_timeout`; if
  that's exceeded, show "skipped" rather than fail)
- **dataset context size**: the layer 3 text the explorer would build (notes + glossary + schema
  section) in characters, against the 20k target in `explorer/SPEC.md` §6. Build it with the
  explorer's own functions (`prompt.dataset_context`, `schema.render_schema_section`), not a
  copy, so the number can't drift from the real one.

Run against the existing AusTender dataset, the report is a check that comments are complete and
the context fits its budget.

**Step 2 (phase 2): Draft (model, optional).** From the report JSON plus a few read-only sample
queries, the model drafts:
- `dataset.toml` (name, description, allowed_schemas, notes, glossary, `[currency]`), which must
  pass `config.load_dataset` unchanged
- `notes.md`: which tables to prefer, traps already handled, year conventions, how to search
  names. Same register as `explorer/datasets/austender/notes.md`.
- `glossary.md`: domain terms only, short
- comments for every object that lacks one, and proposed rewrites of existing comments shown
  separately (never overwriting silently). The person picks the **comment target**:
  `comments.sql` for a database we can comment on (the default when the login can), otherwise
  `comments.toml` (the Comment overlay) in the dataset directory. One draft, two possible
  outputs; the model doesn't need to know which.

### Claims

Every factual claim in a draft (a percentage, a code list, "NULL for older rows", "excludes
cancelled") must come with the SQL that supports it and an **expectation** of one of three
shapes:

- **scalar**: one value with an absolute or relative tolerance (percentages, counts, min/max).
  The SQL must return one row, one column.
- **value set**: an exact set of values (code lists). The SQL returns one column; compared as a
  set.
- **none**: the claim can't be checked mechanically; the person reviews it.

The tool runs every claim's SQL itself (read-only, through `guard`) and shows the claim, the
expectation and the actual result side by side, with a state: **verified**, **failed**,
**error** (the SQL failed or returned the wrong shape) or **needs review** (expectation none).
Never mark a claim verified unless a comparison passed. **Failed and error claims block Export**
until the person edits the claim (rerun) or dismisses it; needs-review claims don't block.

The person reviews and edits every file in the UI, then exports them to a new
`explorer/datasets/<name>/` directory, with `comments.sql` or `comments.toml` there. The tool
never commits, never runs DDL on the dataset database and never creates roles.

## 2. Users and scope

- One person, on a laptop, in development. No auth, binds to `127.0.0.1` only.
- Phase 1: the report CLI and its JSON.
- Phase 2: the Comment overlay in the explorer, step 2, review/edit in the UI, export, rerunning
  the report after edits.
- Out of scope: creating the read-only role and grants (database admin work, documented as a
  manual step), applying `comments.sql`, an external comments database (deferred, ADR 0019),
  finding problems in the data by free exploration, multiple people working at once, building
  the second dataset (`vicprop/`, built separately).

## 3. Architecture

```
explorer/bootstrap/
  SPEC.md
  server/        Python 3.13, uv, package explorer_bootstrap; depends on explorer_agent as a
                 path dep. Phase 1: the report CLI. Phase 2: FastAPI (or Starlette) on top.
  ui/            Vite + Lit + TypeScript (phase 2)
```

- **Reuse, don't copy:** `explorer_agent.sql` (`guard`, `SqlRunner`, `check_dsn`),
  `explorer_agent.schema` (catalog read and `render_schema_section`), `explorer_agent.config`
  (`load_dataset` to validate the drafted `dataset.toml`), `explorer_agent.prompt.dataset_context`.
  If something needs changing to be reusable, change it in `explorer_agent` with tests, and keep
  the explorer's behaviour identical (its byte-identical prompt test must still pass).
- **Database safety, same rules as the explorer:** every query runs in a `READ ONLY` transaction
  with `statement_timeout`, a row cap and a schema allowlist (the schemas the person chose). The
  model's SQL goes through `guard`. Catalog and `pg_stats` queries are written by us, not by the
  model.
- **Connection:** `BOOTSTRAP_DATABASE_URL` in `.env` (placeholder in the committed
  `.env.example`). Recommend a read-only login; still enforce `READ ONLY` either way.
- **Model calls (phase 2):** LiteLLM `acompletion` (pinned `1.106.6`, as in the agent) with our
  own short tool loop. Tools: `run_sql(sql, purpose)` only; the report JSON goes in the prompt.
  - Default model `meta/muse-wonder-1.0`, set by `BOOTSTRAP_MODEL`. Allow `meta/muse-spark-1.3`
    as the only alternative, picked in the UI.
  - `reasoning_effort` default `low` (Wonder rejects `none`). Setting: `BOOTSTRAP_REASONING_EFFORT`.
  - Structured output: return drafts as JSON with a schema (`response_format: json_schema` on
    Chat Completions): `{files: {...}, claims: [{text, file, sql, expect}]}`, where `expect` is
    `{kind: "scalar", value, abs_tol?, rel_tol?}`, `{kind: "set", values}` or `{kind: "none"}`.
    Nesting depth ≤ 10, no recursive schemas.
  - Chat Completions does not carry reasoning across turns for external keys. Keep the loop short
    (cap: `BOOTSTRAP_MAX_TOOL_ITERATIONS`, default 12). Don't switch to the Responses API in this
    build; note it as a later option.
  - Prompt caching: stable prefix (instructions, then output rules, then the report), fixed
    `prompt_cache_key` per dataset, log prompt and cached tokens and **cost per run** (Wonder:
    $4.00 input, $0.20 cached input, $20.00 output per 1M tokens, as of 2026-11-02; keep prices
    in config, not code).
  - The pre-run cost shown in the UI is an estimate: exact input cost of the prompt plus a cap
    for output (`max_tokens` × output price). Label it as an upper bound.
- **Secrets:** `META_API_KEY` and the database URL come from the env. Never log, print or send them
  to the UI.
- **Dataset-agnostic:** nothing AusTender-specific in the tool's code or prompts. AusTender is
  only a test fixture.

## 3a. Comment overlay (explorer change, phase 2, then bootstrap support)

The explorer gets one optional setting in `dataset.toml`:

```toml
[comments]
overlay = "comments.toml"      # relative to dataset.toml; absent = database comments only
```

- **Overlay, not replace:** database comments are always read. Overlay entries replace the
  database's comment for that table or column; objects without an entry keep their database
  comment.
- **File format** (`comments.toml`; TOML so the stdlib reads it, like `dataset.toml`):

  ```toml
  ["mart.contract"]
  comment = "One row per contract notice ..."
  [ "mart.contract".columns ]
  notice_id = "Contract notice number. Primary key."
  ```
- **Where the overlay applies:** everywhere comments reach the model. That's the schema section
  in the cached prefix **and** `describe_table`, which today reads comments live per call
  (`SchemaCatalog._load`). Load the overlay once at startup and apply it in one place.
- **Startup checks, failing with a clear message:** the overlay file is missing or invalid; an
  overlay entry names a table or column that doesn't exist in the allowed schemas (this catches
  drift after a schema change).
- **Caching:** the overlay is read once at startup, so the prefix stays byte-identical across
  requests. An edit takes effect on restart; say so in the README. The prefix tests extend to
  the overlay.

## 4. UI (Lit + TypeScript, phase 2)

Three screens, in order, with the current step always visible:

1. **Connect.** Shows which database the server is using (host and database name only, never the
   password), lists the schemas it can see, and lets the person tick the ones to include. Starts
   the report.
2. **Report.** Renders the report JSON: a table per database object (tables, views):
   comment-coverage badge, row estimate, then columns with type, nullability, null fraction,
   distinct estimate, top values and comment (or a clear "no comment" marker). A summary bar at
   the top: objects, columns, % with comments, context size against 20k. Sort and filter by
   "missing comment". Button: "Draft with <model>", with model picker and the estimated cost
   (upper bound) before running.
3. **Review.** One tab per drafted file, each in an editor (a plain `<textarea>` with a monospace
   font is enough; don't add a code-editor dependency unless the spec makes the case). A
   claims panel: each claim, its SQL, expectation, actual result and state (verified / failed /
   error / needs review), with edit-and-rerun and dismiss. Live validation of `dataset.toml`
   through the server (`load_dataset`) and the context size with the drafted notes and glossary.
   Buttons: "Re-run report", "Export" (refuses if `dataset.toml` doesn't validate, a failed or
   error claim is neither fixed nor dismissed, or the target directory already exists).

Show progress while the model runs: tool calls with each SQL's `purpose`, running token and cost
totals. Drafting is not streamed token by token; showing progress per tool call is enough.

Keep the dependency list short: Lit, Vite, TypeScript, Vitest, happy-dom, at the versions
`explorer/client/package.json` pins (pin exact versions; the client still uses `^` for `lit`,
so pin the version its lockfile resolves). No component library and no router: a small
`<bootstrap-app>` element that switches between three screen elements is enough. Follow the
client's patterns for elements and tests. Accessibility: keyboard navigation, real `<table>`
markup, visible focus.

## 5. Server API (phase 2 sketch; settle it in the spec)

- `GET /api/connection`: host, database, visible schemas
- `POST /api/report` `{schemas}` → report JSON (the same JSON the CLI writes)
- `POST /api/draft` `{schemas, model, effort}` → run ID; `GET /api/draft/{id}/events` (SSE)
  for progress; final event carries files and claims with verification results
- `POST /api/claims/verify` `{claims}` → rerun edited claims
- `POST /api/validate` `{files}` → `load_dataset` result and context size
- `POST /api/export` `{name, files, comment_target}` → writes `explorer/datasets/<name>/`, plus
  `comments.sql` or `comments.toml` there

## 6. Tests (write them alongside the code)

- **No test calls a real model or the network.** Use a stub completion with canned tool calls and
  a canned JSON draft, the way the agent's tests do (`explorer_agent/testing`).
- Database tests build their database with the austender package, as the explorer does
  (ADR 0010).
- Phase 1:
  - The report finds every uncommented column in a fixture where some comments are removed. Its
    context-size figure equals `len(dataset_context(...))` for AusTender.
  - `pg_stats` missing (table not analysed) is reported, not an error. A slow min/max shows
    "skipped" when `statement_timeout` is hit.
  - "Can add comments" is false for AusTender's read-only role and true for the owner.
  - The JSON round-trips through its schema; the CLI never prints the DSN's password.
- Phase 2:
  - The model's SQL that writes, or touches a schema not chosen, is refused by `guard`.
  - Claims: a scalar within tolerance is verified, outside is failed; a set compares as a set;
    the wrong result shape is error; a claim whose SQL fails is error, not dropped; `none` is
    needs review. Failed and error claims block export until fixed or dismissed.
  - The drafted `dataset.toml` validates with `load_dataset`. Export refuses to overwrite.
  - Secrets never show up in API responses or logs (assert on captured logs).
  - Comment overlay (explorer): with no `[comments]` table, behaviour and prompt bytes are
    unchanged from today. An overlay replaces exactly the comments it names, in both the schema
    section and `describe_table`. An overlay entry for a missing column is a startup error. The
    prompt is byte-identical across two builds with an overlay.
  - Comment targets (bootstrap): the same draft exports to both targets with the same comments.
  - The explorer's existing tests still pass, especially the byte-identical prompt test.
  - UI: Vitest + happy-dom, mounting the Lit elements as the explorer client's tests do, for the
    report table (missing-comment filter, summary numbers), the review flow (validation errors
    and failed claims block export) and the claims panel states.

## 7. Done when

**Phase 1:**
- The report runs against AusTender, shows 100% comment coverage and a context size matching the
  explorer's.
- `uv run pytest` (bootstrap and agent) passes. A README in `explorer/bootstrap/` covers setup
  and the CLI.

**Phase 2:**
- With a stub model, the draft → review → export flow produces a directory that the explorer
  starts with (originally `DATASET_DIR=datasets/<name>`; since ADR 0048, as a Workspace), once for each comment target. The overlay target
  works through AusTender's read-only role, which can't add comments.
- One live run with `meta/muse-wonder-1.0` against the Victorian property sales dataset
  (`vicprop/`, built separately; wait for it), logging tokens, cached tokens and cost. Record
  the result, including claims that failed to verify, in `docs/live-checks/`.
- `uv run pytest` (server and agent) and `npm test` (UI) pass. The README also covers the
  manual role and grants step and applying `comments.sql`.

## 8. Commits

Conventional Commits using `.gitmessage`. Small commits: report, then overlay, then draft, then
UI, then export.
