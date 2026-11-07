# REST API Datasets

Status: **draft for discussion**, 2026-11-06. The user selected **direct API access first**
and the **AusTender OCDS API** as the pilot.
This is a feature plan, not approval to implement or a replacement for the approved specs.

## Outcome

A Workspace can answer questions against a configured REST API without importing its data
into Postgres. The agent requests data, the server turns responses into stored Results, and
the existing Macros produce the answer. Every displayed value remains traceable to a Result.
Existing Postgres Workspaces continue to work.

Start with bounded searches, lists, entity lookups and navigation between related resources.
Support API-provided aggregates where their scope is documented. Do not promise arbitrary SQL
analytics over an API that only exposes paginated records.

## Selected pilot: AusTender OCDS API

The user selected [austender/austender-ocds-api](https://github.com/austender/austender-ocds-api).
Its README documents notice-ID search and searches by publication, start, end and latest-modified
dates. The linked SwaggerHub page returned 404 to the documentation fetch on 2026-11-06;
verify the current machine-readable contract before implementation. No live data requests were
made for this plan update.

The existing `austender/SPEC.md` §4 and `austender/src/austender/api.py` provide prior project
evidence for the following contract. These are implementation inputs, not a fresh verification
of the remote service:

| Operation | Request | Initial use |
|---|---|---|
| Notice lookup | `GET https://api.tenders.gov.au/ocds/findById/{CN}` | Retrieve a notice's release chain |
| Publication search | `GET https://api.tenders.gov.au/ocds/findByDates/contractPublished/{from}/{to}` | Browse a bounded publication interval |
| Other date searches | Same date-search family, with a documented date type | Later, after verifying parameter names and semantics |

Proposed acceptance questions (the API is selected; these scenarios still need review):

1. **“Show the details and published values for notice CN….”** Retrieve one notice chain;
   show the agency, supplier, description, status, period and values with their release identities.
2. **“Show notices published on this date.”** Resolve the intended timezone into UTC request
   bounds, paginate within budget, and label partial coverage. Verify interval inclusivity at
   boundaries. Distinguish notices from amendment releases returned alongside them.
3. **“Show the amendments for this notice.”** From a selected notice, fetch its chain without
   another model call and show amendment sequence, published values and statuses. Do not label
   release timestamps as amendment dates.

Negative acceptance question: **“Which suppliers received the most contract value across the
whole financial year?”** No aggregate operation has been established by the reviewed sources.
The first slice must explain that its bounded API retrieval cannot establish that ranking;
it must not rank a partial sample as the whole year. The existing Postgres Workspace remains
available for such analysis, without automatic cross-Workspace routing.

### Pilot-specific behavior and verification

- Prior project evidence says no authentication is required. Send a descriptive User-Agent
  with a contact address, configured for this Dataset. Verify this contract in a bounded live
  check during implementation, separately from automated tests.
- Keep one request in flight at a time and at least one second between requests, following the
  project's existing conservative policy. Share the limiter across callers to this API.
- Follow the response's `links.next` URL after validating its destination. A short page can
  still have a next link; stop on an absent next link or an explicitly empty `releases` array.
  Missing or malformed `releases` is a response error, not an empty collection. Detect cycles.
- Treat HTTP 400 with `errorCode: 100` from notice lookup as not found, per existing client
  behavior. Other 400 responses remain errors. Include this distinction in offline fixtures.
- Publication searches can include later amendments. Deduplicate by release ID, preserve
  notice identity separately, and do not treat releases as distinct contracts. Modified-date
  searches can omit the original parent, so defer them until parent-fetch budgets are specified.
- Start with one row per release/contract entry, with explicit release and contract keys;
  represent suppliers separately or as a display list. Test nested arrays without multiplying
  values. Do not imply a derived current value until the publisher's cancellation and amendment
  rules are encoded and tested. Sequence ordering and field mappings belong to reviewed Dataset
  configuration; any reusable normalization primitive must remain publisher-independent.
- Proposed interactive defaults: at most five HTTP attempts including retries, 500 release
  objects, 10 MiB decoded response data and 15 seconds total per Turn. These are draft product
  budgets, not provider limits. A budget stop produces visible partial coverage; a failed lookup
  produces an error. Benchmark and revise before approval. Enforce row-expansion limits too.
- This pilot exercises configured operations and pagination links. It does not establish
  support for arbitrary HATEOAS representations; the unrelated API fixture remains necessary.

Keep the API Dataset separate from the existing AusTender Postgres Dataset. Use a distinct
Workspace name such as `austender-api`. Put publisher mappings and notes in deployment data,
with frozen offline fixtures for tests. Reuse lessons from `austender/`, not its ingestion
runtime as a new dependency of `explorer/`; no changes to the ingestion pipeline are required.

## What changes in the domain

The current glossary defines a Dataset as one database and its descriptions. The proposed
definition is one configured body of data, accessed through a database or API, together with
its descriptions. A Workspace still has exactly one Dataset; federated queries are deferred.

Proposed glossary changes, to resolve alongside the spec before implementation:

- **Result:** stored rows and columns from a data request, together with the evidence of how
  they were obtained. SQL is one kind of evidence. Results remain the only source of numbers.
- **Data dictionary:** descriptions of the available data and its fields, including API
  resources, field meanings, identifiers, units and relationships.
- **Coverage:** the portion of the requested population represented by a Result, and whether
  that portion is complete, partial or unknown.
- **Visual:** a representation of a Result with its retrieval evidence available to inspect.

Workspace availability, Draft, New Workspace, Conversation, Drill query and Board definitions
also need to accommodate APIs. Keep these proposals here until resolved; do not silently change
the approved database behavior or overload the glossary with connector implementation details.

## Design

### Separate access, navigation and data meaning

Use a common execution boundary that returns typed rows, coverage and provenance. Keep
source-specific tools: Postgres retains `run_sql`; APIs gain structured operations. Avoid
pretending every REST API can execute SQL.

An API Dataset contains a reviewed description of:

- Base URL, allowed origins and paths, credential references, and supported read operations.
- Operation names, parameter schemas, filtering/sorting capabilities and expected responses.
- Resource identities, row mappings, field types, units, null semantics and relationships.
- Pagination and supported hypermedia representations/link relations.
- Rate limits, request/page/byte/row/time budgets, and freshness policy.

Import OpenAPI or JSON Schema where available as a drafting aid. Require reviewed mappings and
notes where documentation cannot establish semantics. A base URL alone cannot establish the
meaning of every field, which calls are safe, or what a collection includes.

OCDS describes releases, records and packages; publisher endpoints and filters still need
configuration. Its API guidance is deliberately non-prescriptive. See the official
[hosting guidance](https://standard.open-contracting.org/latest/en/guidance/build/hosting/).
Hypermedia navigation is a separate capability: links express relationships, and their
serialization varies. Initially support HTTP Link headers plus explicitly configured JSON
link mappings; add representations such as HAL as separately tested adapters. See
[RFC 8288](https://www.rfc-editor.org/rfc/rfc8288.html).

### Agent tools and request execution

Provisional tools:

- `describe_resource(name)` for details beyond the compact cached Data dictionary.
- `request_api(operation, parameters, purpose)` for a configured operation.
- `follow_link(result_id, link_id, purpose)` for a server-registered link observed in a response.

The agent chooses an operation or link and validated arguments. The server constructs requests,
handles pagination and maps data into Results. It does not require a model round trip per page.
Do not expose arbitrary HTTP, arbitrary code execution or model-generated credential headers.
Return Result IDs, typed columns, small row previews, coverage and available link handles.

Keep stable descriptions in the cached Dataset context. Response links, timestamps, cursors
and request-specific values belong in tool results, outside the stable prefix. Preserve the
existing model restrictions and prompt-cache tests.

### Correctness and coverage

Keep display truncation separate from retrieval completeness. A complete server-side sum can
be a single row; a thousand retrieved rows can still be only a fraction of the population.
Record filters, sort, pages fetched, termination reason, reported totals and retrieval time.
An exhausted pagination chain does not establish a consistent snapshot if records changed
during traversal; record snapshot support or uncertainty separately.

Only claim a population aggregate when supplied by a documented aggregate operation, or computed
deterministically over a demonstrably complete, appropriately scoped collection. First release
uses API-provided aggregates; general local transformations and joins are deferred. A partial
list must show its coverage on the Visual, not only in the model prompt. Do not interpret a
timeout, rate limit or malformed response as an empty successful result.

Mappings must explicitly select a row grain. Flattening two independent nested arrays must
not silently multiply monetary values. Preserve decimal precision through parsing and any
calculation, then apply the existing rendering contract; retain currency per value where needed.

For OCDS, multiple releases can describe one process, while records collect its releases.
Use reviewed mappings for release history versus compiled state; never sum releases as though
each were a distinct contract. See the official
[record reference](https://standard.open-contracting.org/latest/en/schema/records_reference/).
Publisher-specific amendment, cancellation and supplier allocation rules remain Dataset data.

### Provenance, saving and interactions

Generalize Result execution metadata to a versioned union: SQL request or API operation/link
recipe. API provenance includes sanitized request details, retrieval time, coverage, mapping
version and response validators when available. Credentials stay outside saved Conversations.

Use a shared evidence panel with SQL and API presentations. Update the one merged catalog on
both server and client. Macros keep referencing Result IDs; they do not receive raw API payloads.

Saved Conversations currently retain SQL and hashes, then fetch rows again. Preserve that model
using a replayable API recipe and hash comparison. Restart traversal from the operation or stable
resource identity; do not rely on expiring cursors or signed links. Report changed or unavailable
data honestly. Exact historical replay would require response snapshots and is deferred.

Boards and Drill-downs must eventually execute parameterized API recipes without a model call.
Bind values through operation parameter validation and URL encoding. For the initial slice,
offer supported entity/link navigation and confirmed Ask; explicitly relax the SQL-only mandatory
Drill query rule for API Datasets. API Boards and Controls follow in a separate slice, including
their option lists and ranges. Postgres behavior remains as specified.

### Read-only access and runtime limits

Allow only reviewed read operations, initially GET. Method alone is not proof that a poorly
designed endpoint is safe. Restrict destinations and paths, validate every redirect and discovered
link, reject disallowed address ranges, and prevent credentials crossing origins. Private APIs
need explicit deployment configuration rather than unrestricted network access.

Treat API text as untrusted data. Apply the existing Macro text escaping. Redact secrets from
URLs, errors, evidence and logs; apply the same outbound controls to documentation/schema fetches.

Bound requests, pagination depth, bytes, rows and total elapsed time per operation and Turn.
Detect pagination cycles. Respect provider rate limits and Retry-After within the remaining
budget; retry only eligible failures. Reuse HTTP connections, deduplicate identical in-flight
requests, and share rate limiting across a Workspace. Any cache must isolate credentials and
Workspace identity and expose freshness. No unbounded crawl or speculative link traversal.

## Repository impact

| Area | Required change |
|---|---|
| `config.py`, `workspaces.py`, startup | Dataset kind, API configuration, credential references and source-specific availability checks; API Workspaces need no DSN |
| `tools.py`, `llm.py`, `prompt.py` | Source-specific tools and stable API Data dictionary |
| `results.py`, `saved.py`, Conversation restore | Versioned request/provenance metadata, coverage, persistence migration and replay |
| `macros.py`, catalog, client evidence component | API evidence and visible coverage while preserving Result-based rendering |
| `drill.py`, `boards.py`, action handling | API recipes and capability-aware validation; avoid SQL LIMIT 0 checks for APIs |
| New Workspace/bootstrap | API connection choice, capability report, reviewed Draft and export |
| Specs and glossary | Amend database-only assumptions and record architectural decisions before build |

Evidence for this scope: `ToolBox` depends on `SqlRunner` and `SchemaCatalog`;
`StoredResult` and `SavedResult` require SQL; the saved-results table has `sql TEXT NOT NULL`;
`WorkspaceEntry` requires `dsn_env`; `BoardQuery`, Control side queries and Drill validation use
SQL; `macros._sql_details` constructs `SqlDetails` for Visuals.

## Delivery slices and criteria → tests

1. **Write the approved pilot contract.** AusTender OCDS is selected above; review the three
   proposed questions and unsupported aggregate. Capture sanitized fixtures and reviewed
   mappings. Record the request/Result boundary and persistence decision in an ADR; add an API
   Dataset sub-spec with explicit exceptions to SQL-only behavior.
2. **Generalize Results without changing Postgres behavior.** Add versioned request metadata and
   evidence rendering. Tests: existing SQL Results, saved Conversations, Boards, Drill-downs and
   catalog compatibility still pass; old saved records migrate and replay correctly.
3. **Direct API vertical slice.** Configure a Workspace, ask a question, paginate within budget,
   render a Visual, inspect evidence and reopen the Conversation. Tests use canned model output
   and `httpx.MockTransport`; no network. Cover empty results, nested rows, types, partial pages,
   repeated cursors, errors, expired links, redaction, destination checks and changed replay.
4. **Prove generality.** Add an OCDS fixture and an unrelated API fixture with link navigation.
   Test release/record identity, nested-array duplication, mixed currencies, unknown coverage,
   and rejected global aggregates. Verify navigation requires no model call when preconfigured.
5. **Complete the product workflow.** Add API New Workspace reporting/review/export, then API
   Boards and Controls. Test credentials stay server-side, invalid mappings block export, missing
   capabilities produce clear UI states, and changing Controls executes validated recipes.

Measure HTTP calls, model calls, time to first useful Result, total latency and cache behavior.
Set concrete budgets after examining the pilot's documented limits. The normal request should
aim for the existing two-model-call pattern; pagination stays inside the tool execution.

## Remaining decisions

- Do the three proposed AusTender questions define a useful first slice?
- Verify the current AusTender contract, including authentication, date boundaries, response
  shape and pagination. Prior project evidence supports public access with a User-Agent.
- Is a first slice with answers and entity navigation useful before API Boards are available?

Bulk import, cross-Dataset joins, API writes, unrestricted crawling, arbitrary local analytics
and exact historical snapshots are outside this first version.
