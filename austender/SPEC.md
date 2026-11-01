# SPEC: AusTender database v1

Status: approved brief (2026-10-31). Source of truth for `austender/`.

## 1. Purpose and the one rule that matters

Build a Postgres database of Australian Commonwealth contract notices from the AusTender OCDS API,
in Python. Its consumer is the explorer (`../explorer/`): an LLM agent that writes SQL against this
database to answer plain-English questions, and charts the results.

**Speed is the most important requirement**, after correctness of numbers. The agent's queries must
be fast (target: typical aggregate queries under 500 ms on the full ~870k contracts), and the agent
must not be able to get totals wrong by forgetting a filter. Both are achieved the same way: the
agent only sees a precomputed schema (`mart`) in which the traps are already handled.

## 2. Non-negotiables

- **Python only** (3.13, `uv`). No TypeScript, no Node.
- `httpx` for HTTP, `psycopg` 3 for Postgres. Plain SQL: migrations are `.sql` files, data access
  is a thin module of SQL functions. No ORM, no Alembic.
- Tests never touch the network. Database tests use a disposable Postgres 18 (Testcontainers).
- Never modify anything outside `austender/`. `../fti/` may be **read** for lessons (section 11);
  copy ideas, not code.
- Secrets live in `austender/.env` (git-ignored at the repo root). Commit an `.env.example` with
  placeholders. Never print or log passwords or URLs containing them.
- **Do not run the full backfill.** It takes hours and is started deliberately later. You fetch one
  month live to prove the client works (section 10).

## 3. Runtime and layout

- Postgres 18 in Docker Compose (`austender/docker-compose.yml`), host port **5434**, database
  `austender`, a named volume.
- Roles, created by a migration or setup step with passwords from `.env`:
  - `austender_loader`: owns the schemas, does all writes.
  - `explorer_ro`: `LOGIN`, `SELECT` on schema `mart` only (including future tables, via default
    privileges), nothing else, no access to `raw`, `core` or `ops`. Set
    `ALTER ROLE explorer_ro SET default_transaction_read_only = on`.
- Schemas: `raw` (releases as received), `core` (normalised), `mart` (what the explorer reads),
  `ops` (ingest runs, checkpoints, rejected releases, applied migrations).

```
austender/
  SPEC.md  README.md  pyproject.toml  uv.lock  .env.example  docker-compose.yml
  migrations/0001_….sql …            applied in order by our own runner
  src/austender/
    config.py      settings from env
    cli.py         `uv run austender <command>`
    api.py         AusTender OCDS client (pagination, retries, rate limit)
    db.py          connections and the migration runner
    ingest.py      backfill / fetch-month into raw, checkpoints
    normalise.py   raw → core (pure function of raw)
    mart.py        core → mart (rebuild)
    abn.py         ABN checksum, name normalisation
    testing.py     build_test_db(dsn): migrate + load seed + rebuild + roles (used by explorer tests)
  tests/
    seed/          hand-written OCDS release JSON files
    …
```

The explorer will add this package as an editable path dev-dependency and call
`austender.testing.build_test_db(dsn)` against its own Testcontainers Postgres. Keep that function's
signature stable and its dependencies light.

CLI commands: `migrate`, `fetch-month YYYY-MM`, `backfill --from YYYY-MM --to YYYY-MM [--resume]`,
`rebuild` (core and mart from raw), `stats` (row counts, latest checkpoint, rejected count).

## 4. The AusTender API (verified by an earlier project)

- Base `https://api.tenders.gov.au/ocds`. Endpoint `findByDates/{dateType}/{from}/{to}`, ISO-8601 UTC
  with `Z`. Use `dateType = contractPublished` for the backfill. (`contractLastModified` is for a
  future delta job, out of scope; see the trap below.) `findById/{CN}` returns one notice's chain.
- No auth. Send a descriptive `User-Agent` from `INGEST_USER_AGENT`; fail fast if unset.
- Pagination: at most 100 releases per page; `links.next` is a full URL with opaque cursors. Use it
  immediately, never store it. **A short page can still have `links.next`, followed by an empty
  page: stop only when `links.next` is absent or `releases` is empty.**
- One request at a time, at least 1000 ms apart. Retry network errors, 429 and 5xx with backoff
  starting at 5 s and doubling, 5 attempts. Don't retry other 4xx. Read the body inside the retried
  unit, so a connection dropped mid-body is retried.
- A not-found `findById` is HTTP **400** with `{"errorCode":100,…}`, not 404. Treat it as "none".
- Latency 0.2–1.2 s per page. One month is about 4.5–6.7k releases, 45–70 pages, ~1 minute.
- `contractPublished` returns parents **and** their later amendments (despite Swagger saying parents
  only). Dedupe on the release `id`, never on `contracts[0].id`.
- Trap for later (delta, not v1): `contractLastModified` returns amendments without their parent when
  the parent was published earlier; each needs `findById`.
- Backfill: one UTC calendar month at a time, oldest first, from 2013-01. Checkpoint per month in
  `ops`; `--resume` skips completed months. A month is complete only when its pages are all stored.

## 5. Raw storage

`raw.release`: one row per distinct release `id`: `release_id` (unique), `ocid`, `payload jsonb`,
`payload_sha256` (of canonical JSON, sorted keys), `ingest_run_id`, `first_seen_at`. Append-only. A
known `release_id` with a different hash is recorded in `ops.rejected_release` and the stored row is
left unchanged (the earlier project saw 56 of these).

`core` and `mart` are a **pure function of raw**: `rebuild` from the same raw gives identical tables
(test it). IDs derived from source identity, not sequences, wherever a row is keyed.

## 6. Normalisation rules (raw → core)

- Release shape: parents are tagged `contract`, amendments `contractAmendment`, all sharing an `ocid`.
  On an amendment, `contracts[0].id` is the **parent** CN; the amendment ID `CN<n>-A<k>` is in
  `contracts[0].amendments[0].id`. `amendsReleaseID` always points at the original (a star, not a
  chain), so order amendments by `k`.
- `core.contract`: one row per parent notice (`notice_id` = CN). `original_value` from `value.amount`,
  which arrives as a **string**: parse straight to `numeric(18,2)`, never via float.
  `limitedTenderExempt` is the string "0"/"1". UNSPSC code at `contracts[0].items[0].classification.id`
  (code only). Procurement method, limited-tender reason code, status, period start/end, agency
  (procuring entity), source URL if present.
- **Dates:** convert timestamps to `Australia/Canberra` before taking the date
  (`2026-06-30T14:00:00Z` → `2026-07-01`). `publish_date` = the parent release date in Canberra time.
  `financial_year` derived from it, July–June, text like `2024-25`.
- `core.amendment`: one row per amendment, a **full snapshot** (value, period, status), not a delta.
  The API gives no amendment date: the release `date` on amendments is a re-publication stamp shared
  by all amendments of a contract. **Never store it as an amendment date.** `value_delta` is computed
  against the previous non-cancelled snapshot (the parent if none). Values can go down.
- **Current value:** the value of the highest-`k` non-cancelled amendment, else the original. A
  cancelled amendment never sets the current value. A contract is cancelled when its own notice status
  is cancelled. Cancelled notices keep their full value in the source, which is exactly the trap.
- `amendment_count` counts all `-A<k>` notices; `cancelled_amendment_count` is a subset of it, not
  additional.
- `core.contract_supplier`: one row per party with role `supplier` (notice + ordinal). In practice
  AusTender publishes exactly one supplier party per release: all 1,171,903 raw releases from
  2013-01 to 2026-10 have one (checked 2026-11-02). Multi-supplier handling and its seed cases are
  kept in case that changes. ABN from
  `additionalIdentifiers` with scheme `AU-ABN`. Keep `abn_raw`; store `abn` only when it passes the
  checksum (11 digits; weights 10,1,3,5,7,9,11,13,15,17,19 after subtracting 1 from the first digit;
  sum mod 89 = 0). About 7.5% of suppliers have no ABN; that is normal. Don't store `contactPoint`
  (it is almost always the agency's contact). Country and state as published; `is_overseas` from
  country.
- `supplier_key`: the valid ABN, else `'name:' || name_norm`. `name_norm`: lower case, unaccent, `&` →
  `and`, `P/L` → `pty ltd`, strip punctuation, collapse spaces, repeatedly strip trailing legal
  suffixes (pty, ltd, limited, pty ltd, inc, the trustee for …).
- `core.agency`: keyed by agency ABN, else `name_norm`. Display name from the **latest** parent
  notice (agencies rename).
- Supplier display name: the most frequent published name for the `supplier_key`, ties broken by
  latest notice.

## 7. The `mart` schema (the explorer's whole world)

Rebuilt from `core` by `rebuild`, in one transaction, so readers never see a partial state. Plain
tables with indexes, not views. Proposed tables (adjust if you find better, but keep the
properties):

- `mart.contract`: one row per contract, **including cancelled**, with `is_cancelled`. Notice ID,
  agency key and name, publish date, financial year, procurement method, limited-tender reason,
  UNSPSC code and segment/family/class prefixes (2/4/6 digits), period start/end, original value,
  current value, value change, amendment counts, number of suppliers, source URL.
- `mart.contract_supplier`: one row per contract × supplier, **excluding cancelled contracts**, with
  `value_share` = current value ÷ number of suppliers (unrounded numeric) and the contract's dimensions
  copied in (FY, publish date, agency, method, UNSPSC prefixes), so most questions need no join.
  Summing `value_share` never double counts.
- `mart.supplier`: one row per `supplier_key`: display name, ABN (nullable), overseas flag, totals.
- `mart.agency`: one row per agency.
- Summary tables for the common rollups, all excluding cancelled contracts, e.g.
  `mart.supplier_fy`, `mart.agency_fy`, `mart.supplier_agency_fy` (contracts count, value). The
  acceptance question the explorer must answer quickly is: *"Which 10 suppliers received the most
  contract value in 2024, and how is it split across agencies?"*

**Every `mart` table and column has a `COMMENT ON`.** These comments are the agent's only data
dictionary: the explorer pastes them into the LLM's prompt. Write them for an LLM reader that knows
nothing about AusTender: meaning, units (AUD as published), what is included or excluded (cancelled,
split across suppliers), FY format, nullability meaning, and which table to prefer for what. Keep
each concise; the whole schema's comments should stay under ~12k characters.

Index for the expected access paths (FY, agency, supplier, UNSPSC prefix, publish date). No PL/pgSQL
functions (generic-plan slowdowns hit the earlier project hard).

## 8. Test seed

`tests/seed/`: a few dozen hand-written OCDS releases, realistic in shape (base them on the real
release structure you see in your one-month fetch, with fictional names), covering at least: a plain
contract; a cancelled contract; a contract with 2 and one with 3 suppliers; an amendment raising the
value; one lowering it; a cancelled amendment; a supplier with no ABN; one with an invalid ABN; two
spellings of the same supplier name with one ABN; a publish timestamp just before midnight UTC on
30 June (FY boundary in Canberra time); two agencies, one renamed; a duplicate release; a release ID
seen again with a different payload. Commit a `tests/seed/EXPECTED.md` (or a Python module) giving
the exact expected totals by FY, supplier and agency, computed by hand. Tests assert them.

## 9. Tests (pytest, no network)

- API client against `httpx.MockTransport`: pagination incl. short-page-then-empty-page, retry and
  backoff (inject the sleep), no retry on 4xx, 400 + errorCode 100 → none, User-Agent required,
  spacing between requests.
- ABN checksum and `name_norm` cases.
- Normalisation and mart on the seed vs `EXPECTED`: cancelled excluded from shares and summaries,
  value split, current value rules, FY boundary, agency rename, duplicate and rejected releases.
- `rebuild` twice gives identical tables (compare a hash per table).
- Roles: `explorer_ro` can read `mart`, cannot read `raw`/`core`/`ops`, cannot write anything.
- Every `mart` table and column has a non-empty comment; total comment length under the limit.
- `build_test_db(dsn)` works on an empty database.

## 10. Done means

1. `uv run pytest` passes (Docker available).
2. `docker compose up -d`, `uv run austender migrate`, then `uv run austender fetch-month <a recent
   complete month>` run live against the API, followed by `rebuild`. Report: releases fetched,
   distinct contracts, pages, elapsed time, rejected count, `mart` row counts, and the
   `EXPLAIN ANALYZE` time of the acceptance query against that month.
3. README: setup, commands, how to start the full backfill, the estimated duration (~150 months at
   ~1 min each), and how the explorer connects (`explorer_ro` URL format, no real password).
4. Commits on your branch, Conventional Commits per the repo's `.gitmessage`
   (`feat(austender): …`), with **no** `Co-Authored-By` or tool-attribution lines.

## 11. Reading list (in `../fti`, read-only)

- `VERIFICATION.md` (API behaviour, sections around lines 36–92)
- `docs/specs/01-austender/SPEC.md` (adapter, HTTP client, `core.contract`, `core.contract_supplier`)
- `docs/adr/0004-amendment-dates-are-never-inferred.md`, `0006-amendments-are-snapshots-with-computed-deltas.md`,
  `0018-…`, `0022-…`, `0023-…` (supplier keys, amendment counts, shared filtering)
- `docs/review-20261022-database.md` (findings 2 and 4: inconsistent filters, double counting)
- `packages/ingest/src/http/client.ts` (retry behaviour)

Where `fti` and this spec disagree, this spec wins; tell the orchestrator about the disagreement.
