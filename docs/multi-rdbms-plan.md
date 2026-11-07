# Multiple relational database engines

Status: **draft evaluation**, 2026-11-06. Requested separately from the REST API Dataset plan.
No engine, dependency or spec change is approved by this document.

## Recommendation

Support additional RDBMSs through explicit, tested database adapters. Keep the agent's
`list_tables`, `describe_table` and `run_sql` tools and the Result/Macro pipeline. Have the model
write the selected engine's native SQL; do not translate all queries from Postgres at runtime.

Preserve the existing Postgres implementation behind the first adapter. Add one engine through
the whole workflow before expanding the matrix. MySQL 8.4 is the provisional first server-engine
candidate if there is no stronger user priority; SQL Server is a sensible alternative when an
actual target Dataset requires it. SQLite is useful for local files but does not prove support
for server authentication, grants, pooling or cancellation. MariaDB needs separate certification
from MySQL. Defer Oracle until a concrete Dataset justifies its integration and test environment.

This is feasible, but it is a cross-cutting feature rather than a connection-string change.
The expensive part is preserving execution safety and Board/Drill-down behavior across dialects.

## Scope and relationship to the API plan

Each Workspace still has one Dataset and one selected database engine. Different Workspaces can
use different engines. Cross-engine joins, federation, moving data between engines, converting
existing Boards automatically and replacing the Conversation SQLite store are out of scope.

The [REST API plan](rest-api-datasets-plan.md) generalizes retrieval provenance. This plan
generalizes relational execution. They can share Result types, evidence, configuration dispatch
and Workspace availability without sharing SQL-specific machinery. Neither implementation must
wait for the other. If developed together, use Dataset kind `database` or `api`, then a separate
database engine field; avoid separate, competing connector registries.

## Current Postgres coupling

| Area | Evidence in the current code | Work required |
|---|---|---|
| Connections and startup | `sql.check_dsn`, `open_pool`, `SqlRunner.check_privileges` use libpq, psycopg and Postgres privilege catalogs | Engine-specific configuration, pool lifecycle and effective privilege checks |
| SQL guard | `sql.guard` walks `pglast` nodes, Postgres CTE scopes and function names | Dialect-aware validation with a tested allowlist of query forms |
| Execution | `SqlRunner._run` uses `BEGIN READ ONLY`, `statement_timeout`, streaming and Postgres error diagnostics | Per-engine read protection, deadlines, cancellation, metadata and error mapping |
| Data dictionary | `SchemaCatalog` queries `pg_class`, `pg_attribute`, comments, OIDs and `format_type` | Engine catalog readers plus the existing Comment overlay |
| Identifiers | `SCHEMA_NAME` accepts lowercase names; `TableInfo.qualified_name` joins two strings with a dot | Structured catalog/schema/object identity, quoting and case rules |
| Drill-downs | `drill.rewrite`, `display_sql`, `null_dropping_params` use Postgres scanning, `$n`, casts and literal formatting | Dialect-aware binds, validation and safe evidence display |
| Boards and Controls | `BoardQuery`, `options_sql`, `range_sql`, array binds and `LIMIT 0` checks | Engine-specific preparation, type binding and output description |
| Result types | Numeric and temporal categories use Postgres type-name sets | Stable logical types alongside original database types |
| Prompts and saves | Instructions include `ILIKE`; saved SQL and Board SQL implicitly mean Postgres | Native dialect guidance and durable engine identity |
| New Workspace | Bootstrap `stats.py` reads `pg_stats`, `pg_namespace` and comment privileges | Capability-based discovery, statistics and export |

The bootstrap work is part of support, not a later cosmetic change: a connection that works only
through hand-edited configuration is an experimental adapter, not a finished product feature.

## Candidate evaluation

These are relative engineering assessments, not measured delivery estimates. Pin supported
server versions, drivers and deployment targets during the first spike.

| Engine | Fit | Main concerns | Proposed position |
|---|---|---|---|
| PostgreSQL | Existing reference implementation | Keep safety, performance and saved behavior intact | Baseline |
| MySQL 8.4 | Strong first server candidate | Database/schema naming, case and collation, JSON/types, effective grants, timeout overrides and cancellation | Provisional first addition |
| MariaDB | Related ecosystem | Similar wire access does not imply identical dialect or timeout behavior | Separate adapter policy/version tests after MySQL |
| SQL Server | Useful alternative server target | Catalog/schema names, T-SQL validation, driver deployment, grants and cancellation | First instead if user demand leads; otherwise next server target |
| SQLite | Local file Datasets | File access boundaries, dynamic typing, missing curated descriptions and blocking execution | Separate local-file slice if wanted |
| Oracle Database | Enterprise target | Version/driver deployment, privilege model, types, metadata and query-shape differences | Demand-driven later slice |

### Read protection and deadlines differ

- **MySQL:** supports `START TRANSACTION READ ONLY`, but temporary-table changes are an exception.
  Keep narrow grants and reject unsafe statements/functions as additional layers.
  [MySQL transaction documentation](https://dev.mysql.com/doc/refman/8.4/en/commit.html).
  `max_execution_time` applies to qualifying SELECTs and can be affected by optimizer hints;
  reject unreviewed executable comments and hints that alter limits. Add cancellation tests,
  rather than treating a socket timeout as proof the server stopped work.
  [MySQL timeout documentation](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html#sysvar_max_execution_time).
- **MariaDB:** `max_statement_time` differs from MySQL's timeout, including units and scope.
  It requires its own execution policy, not a connection-string alias.
  [MariaDB timeout differences](https://mariadb.com/docs/server/ha-and-performance/optimization-and-tuning/query-optimizations/aborting-statements).
- **SQL Server:** `ApplicationIntent=ReadOnly` concerns availability-group routing; it is not
  sufficient evidence of restricted permissions. Design around a constrained principal and
  validated queries, optionally a verified read-only database/replica. Command timeouts involve
  client cancellation. Prove cancellation and pool cleanup with the selected driver.
  [Read-only routing](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/listeners-client-connectivity-application-failover?view=sql-server-ver17),
  [query timeouts](https://learn.microsoft.com/en-us/troubleshoot/sql/database-engine/performance/troubleshoot-query-timeouts).
- **SQLite:** use a trusted configured file opened with `mode=ro`, an authorizer to deny unwanted
  operations, and deadline checks through progress/interrupt facilities. Block arbitrary ATTACH,
  extension loading and filesystem access. Do not set `immutable=1` for a file that can change.
  [URI opening](https://www.sqlite.org/uri.html),
  [authorizer](https://www.sqlite.org/c3ref/set_authorizer.html),
  [progress callbacks](https://www.sqlite.org/c3ref/progress_handler.html).
- **Oracle:** supports `SET TRANSACTION READ ONLY`; retain grants and validation as independent
  layers. Driver deadlines, cancellation and result typing still need a version-specific spike.
  [Oracle transaction documentation](https://docs.oracle.com/en/database/oracle/oracle-database/26/zzpre/defining-controlling-transactions.html).

The approved spec currently requires a read-only role, a READ ONLY transaction and a statement
timeout for every query. Some targets cannot implement those exact mechanisms. Before supporting
one, approve an engine-specific equivalent protection contract. Do not silently omit a layer or
describe a connection hint as enforcement. Postgres retains every existing layer.

## Adapter contract

Keep the interface small and driven by existing callers. Proposed responsibilities:

1. **Connect and inspect:** validate configuration without exposing secrets; identify actual
   engine/version; check effective grants, including inherited roles; establish TLS and namespace
   policy; refuse an unsupported or unverified setup. Inspect metadata without attempting writes
   against a user's database. Exercise forbidden writes only in disposable integration fixtures.
2. **Describe data:** list allowed objects and fields, comments and optional estimates. Catalog
   reads are trusted application queries; model SQL cannot freely inspect system catalogs.
3. **Validate and prepare:** parse exactly one permitted query, resolve allowed relations with
   CTE scope, validate functions, and compile logical named parameters to driver placeholders.
4. **Describe query output:** return columns and types for Boards and Drill queries using an
   engine-specific mechanism. Do not assume wrapping every CTE, ordered query or union with
   `SELECT * FROM (...) LIMIT 0` works. Description itself must obey time and privilege limits.
5. **Execute and cancel:** read protection, bounded pool wait/execution/fetch, row and byte caps,
   rollback/reset, and disposal of uncertain connections. Return normalized outcomes and
   redacted diagnostics while retaining vendor codes for diagnosis.

Expose explicit capabilities for optional statistics, comments and query forms. Capabilities
must not turn required safety controls into optional UI features. Unsupported behavior fails
clearly and is not sent through the nearest available dialect.

### SQL generation, guards and binding

Put engine, tested version family, naming rules and concise SQL examples in the stable cached
prompt. Use one native dialect per Dataset; no extra model translation round trip. Keep dates,
request IDs and other changing values outside the prefix. Include dialect/configuration identity
in cache isolation while keeping the prefix deterministic for a given Dataset.

Retain `pglast` for Postgres initially. Evaluate SQLGlot for additional dialect parsing, but use
an explicit input dialect and reject unhandled syntax/AST nodes. It offers parsing and translation,
not a read-only security guarantee. Add engine-specific checks for file output, stored routines,
external access, locking, session mutation and cross-database references. Qualification alone
does not constrain what a definer-rights view or routine can access: review exposed objects and
enforce deployment grants. [SQLGlot documentation](https://sqlglot.com/sqlglot.html).

Keep `:name` as the Board authoring convention if the spike proves it unambiguous, but compile
it with a dialect-aware lexer/parser. Never replace parameters with regex or string interpolation.
Resolve repeated parameters, NULL values, dates, decimals and multi-select lists explicitly;
Postgres arrays and `ANY` cannot be the universal representation. Display SQL and bindings as
separate evidence if literal formatting cannot safely reproduce the executed statement.

### Types, naming and persistence

Represent relation identity structurally, with catalog and schema optional where appropriate.
Store allowed namespaces explicitly; retain `allowed_schemas` as a compatible Postgres input.
Quote identifiers through the adapter, preserve case and test names containing spaces or dots.

Preserve native types in the Data dictionary so the model writes valid SQL. Give Results stable
logical categories for rendering: integer, decimal, floating point, boolean, text, date/time,
JSON and binary/unsupported. Preserve decimal precision until the established rendering boundary;
define behavior for large integers, timezone-less timestamps, nullable expressions and empty
results. Do not infer an empty Result's types from its first row. Reject ambiguous duplicate
column labels before building row dictionaries.

Persist engine/dialect identity with SQL recipes and Board metadata. Legacy records mean
Postgres. Changing a Workspace engine must invalidate incompatible caches and prevent replaying
old SQL through a new driver; require a new Workspace or an explicit migration. No automatic
transpilation of saved Conversations or Boards is promised.

### New Workspace and deployment

Add engine selection, server-specific connection validation and source-specific read-only setup
instructions. Secrets remain in referenced server configuration; no credentials in client state,
saved metadata or diagnostics. Permit only explicitly supported driver schemes/options.

Read native comments where available and use Comment overlays for missing descriptions. Make
planner statistics optional and clearly marked as estimates; do not replace missing `pg_stats`
with whole-table scans. Any sampling/min-max queries share execution budgets. Overlay export
should work without write permission on every engine; native comment writes are separate,
explicitly supported bootstrap behavior rather than an adapter requirement.

Package drivers as engine-specific optional dependencies with pinned lockfile resolution. An
uninstalled driver makes that Workspace unavailable with a useful message, not a process-wide
crash. Prove Python 3.13, developer-machine and Linux deployment compatibility before selecting a
driver. Synchronous drivers need bounded workers and real query cancellation; cancelling an async
wrapper around a worker is insufficient. Do not grant broad administrative kill rights merely
to implement timeouts.

## Library and architecture alternatives

| Option | Benefit | Limitation | Assessment |
|---|---|---|---|
| Direct drivers behind our adapters | Preserves current Postgres path; explicit cancellation and safety | More connection/reflection code per engine | Recommended starting architecture |
| SQLAlchemy Core inside adapters | Established dialect, connection and reflection machinery | Does not make arbitrary SQL portable or replace grants/guards; driver behavior still varies | Worth a bounded spike for the new engine; no ORM required |
| Universal SQL transpilation | Appears to reuse query text | Semantics, functions and safety checks still differ; saved evidence becomes harder to explain | Do not use as the runtime contract |
| Federated query layer or copy into Postgres | One query dialect for the explorer | Adds infrastructure, data movement or external-query policy; changes freshness and deployment | Separate feature, outside this request |

SQLAlchemy includes dialects for the candidate families, but supported driver/version combinations
must be checked individually. Its presence would not remove most responsibilities above.
[SQLAlchemy dialect documentation](https://docs.sqlalchemy.org/en/20/dialects/).

## Delivery and acceptance gates

1. **Choose one engine/version and run a bounded technical spike.** Test driver installation,
   native SQL parsing, effective grants, read protection, cancellation, streaming, schema comments,
   empty-result metadata and parameterized Board query description. No live model calls needed.
   Compare a direct-driver implementation with SQLAlchemy Core only where it reduces real code.
2. **Extract the Postgres adapter.** Move the existing implementation without changing behavior.
   Existing tests, prompt snapshots, saved Conversations, Drill-downs and Boards remain valid.
   Measure wrapper overhead and confirm no extra model or database round trips on the normal path.
3. **Implement the chosen engine end to end.** Configuration, schema discovery, agent tools,
   Results, evidence, saved replay, Boards and Controls, then New Workspace report/draft/export.
   Treat partial capability as experimental until this gate passes.
4. **Certify and document.** Pin engine/driver versions, publish read-only principal setup, validate
   deployment packaging, and document unsupported SQL forms. Approve spec amendments and an ADR
   for the engine safety contract before making it available as supported.
5. **Add the next engine using the same gates.** Revisit the adapter boundary using evidence;
   do not infer compatibility from a similar product name or protocol.

### Criteria → tests

| Criterion | Verification |
|---|---|
| Correct numbers across dialects | Small dataset-agnostic fixture with manually computed sums, counts, NULLs, dates and joins; native SQL per engine gives equivalent Results |
| No writes or escape from allowed data | Guard corpus plus disposable-engine tests for DML/DDL, SELECT INTO/file output, writable CTEs, role inheritance, functions, catalog access and cross-database names |
| Bounded work | Slow query, blocked query, fetch cancellation, pool wait and disconnect; observe server termination and subsequent connection health |
| Bounded memory and correct empty results | Large outputs, row-cap-plus-one truncation, byte caps, empty expressions, duplicate labels and metadata |
| Boards and Drill-downs | NULL keys, repeated parameters, list Controls including empty lists, casts, quoted names and dialect-specific CTE/ordering validation |
| Stable prompts and rendering | Byte-identical prefixes per Dataset/dialect; unchanged Macro semantics and common catalog; no data retyping |
| Persistence compatibility | Old Postgres records reopen; new records retain dialect; changed engine refuses incompatible replay |
| Product completeness | Create/reopen Workspace, draft/export with overlays, restart, unavailable credentials and missing-driver diagnostics |
| No hidden live dependencies | Canned model calls; disposable local databases only; tests never contact external services or hosted models |

Mocks alone cannot certify transactions, permissions or cancellation. Required engine integration
jobs must fail clearly if their database is unavailable. Keep each added engine's fixture independent
of the AusTender schema, and retain existing Postgres tests. Do not broaden the implementation's
test matrix beyond the explicitly supported engine/version combinations without a reason.

## Decisions still needed

- First target engine and a real Dataset to validate it; MySQL is a recommendation, not selected.
- Supported server versions and deployment platforms for that target.
- Acceptance of engine-specific equivalents to the current Postgres safety mechanisms.
- Whether local SQLite files are a desired product feature or merely a future possibility.

No runtime code or approved glossary/spec has been changed. This evaluation inspected the current
source and vendor documentation; no driver benchmark or cross-engine execution has been run.
