# Documentation guide

This directory holds design decisions, local issues, acceptance evidence, experiment outputs,
and session history. For setup and commands start with the [root README](../README.md) or the
component READMEs linked below. Historical notes explain how the project arrived here; they
do not override current specifications.

## Find the right document

| Need | Start here |
|---|---|
| Repository overview and first run | [Root README](../README.md) |
| Current application architecture, setup, tests | [Explorer README](../explorer/README.md) |
| Explorer behavioral contract | [Explorer SPEC](../explorer/SPEC.md), then its dated amendments and [sub-specs](../explorer/specs/) |
| Domain vocabulary | [Explorer glossary](../CONTEXT.md), [context relationships](../CONTEXT-MAP.md) |
| New Dataset preparation | [Bootstrap README](../explorer/bootstrap/README.md) and [SPEC](../explorer/bootstrap/SPEC.md) |
| Contract ingestion/schema | [AusTender README](../austender/README.md) and [SPEC](../austender/SPEC.md) |
| Property workbook/schema | [VicProp README](../vicprop/README.md) and [SPEC](../vicprop/SPEC.md) |
| A2UI evaluation and tuning | [Glimmer README](../glimmer-tune/README.md), [glossary](../glimmer-tune/CONTEXT.md), and [SPEC](../glimmer-tune/SPEC.md) |
| Why a design was chosen | [Architecture decisions](adr/) |
| Known problems and unresolved choices | [Local issues](issues/) |
| Original feasibility evidence | [Explorer spike findings](../explorer/spike/FINDINGS.md) |

## Authority and implementation status

`explorer/SPEC.md` is approved and takes precedence over `explorer/BUILD_PROMPT.md`, the
original brief. Later amendments explicitly supersede earlier sections: the initial chat
scope is not the current Boards/Controls/tab design. `austender/SPEC.md` defines database
behavior. VicProp's spec remains labelled draft. Glimmer's spec is a chronological record
whose later amendments supersede its opening milestone draft.

ADRs explain decisions and alternatives. Read their status before treating them as settled:
for example, ADR 0020 is superseded by 0030; ADR 0025 and 0031 are proposals. “Accepted” or
“approved” records a design decision, not necessarily completed implementation. Check build
notes, code, and tests when that distinction matters. READMEs describe the implementation
and operational workflow and should be updated when those change.

## Architecture decision index

### Application foundations

- [0001 — Result-backed Macros and values on screen](adr/0001-data-reaches-the-screen-only-through-macros.md)
- [0002 — One merged catalog](adr/0002-one-merged-catalog.md)
- [0005 — A2UI v0.9 transport and Express training](adr/0005-v0-9-on-the-wire-express-in-training.md)
- [0006 — Shared A2UI SDK pin](adr/0006-one-a2ui-sdk-pin-for-training-and-serving.md)
- [0007 — Browser calls the agent directly](adr/0007-browser-calls-the-agent-directly.md)
- [0010 — Disposable Explorer database tests use the AusTender package](adr/0010-explorer-tests-build-their-database-with-the-austender-package.md)
- [0012 — Effort checks read agent logs](adr/0012-effort-check-reads-the-agent-log.md)
- [0013 — Surface prose rules](adr/0013-surface-text-follows-the-prose-rules.md)
- [0016 — Names matched in the main query](adr/0016-names-are-matched-in-the-main-query.md)
- [0017 — SQL-free UI Repairs](adr/0017-repairs-stay-sql-free-in-the-loop-not-via-tool-choice.md)
- [0018 — Scenario scoring reruns Surface SQL](adr/0018-scenarios-are-scored-by-rerunning-the-surface-sql.md)
- [0022 — Row Tone comes from a Result column](adr/0022-per-row-state-comes-from-a-tone-column.md)
- [0026 — Look and feel lives in the client](adr/0026-look-and-feel-lives-in-the-client.md)

### Dataset preparation and interactions

- [0019 — Committed Comment overlays](adr/0019-comment-overlay-in-a-committed-file.md)
- [0020 — Original bootstrap-only Wonder boundary; superseded](adr/0020-muse-wonder-only-in-the-dataset-bootstrap.md)
- [0023 — Dataset drafts submitted through a tool](adr/0023-bootstrap-drafts-arrive-as-a-tool-call.md)
- [0024 — Model-free Drill-downs](adr/0024-drill-downs-run-a-drill-query-without-the-model.md)
- [0027 — Required Drill queries](adr/0027-drill-queries-are-required-in-the-answer.md)
- [0028 — Boards written with the Dataset](adr/0028-boards-are-pregenerated-with-the-dataset-and-rendered-without-the-model.md)
- [0029 — Bound parameters for Control re-queries](adr/0029-controls-re-query-in-place-through-bound-parameters.md)
- [0030 — Configured Wonder in the Explorer request path](adr/0030-muse-wonder-allowed-in-the-explorers-request-path.md)
- [0032 — A second bootstrap loop drafts Boards](adr/0032-boards-are-drafted-in-a-second-bootstrap-loop.md)

### A2UI evaluation, training, and proposed integration

- [0003 — Answer-only SFT targets](adr/0003-answer-only-sft-targets.md)
- [0004 — Family-based splits](adr/0004-splits-by-gallery-family.md)
- [0008 — Generic v0.9 basic-catalog training](adr/0008-train-generic-express-on-the-v0-9-basic-catalog.md)
- [0009 — Catalog-driven Skeletons](adr/0009-synthetic-surfaces-from-the-catalog-with-skeletons.md)
- [0011 — Synthetic reference house style](adr/0011-synthetic-references-follow-a-house-style.md)
- [0014 — Brief plus Result schemas as Glimmer input](adr/0014-glimmer-input-is-a-brief-plus-result-schemas.md)
- [0015 — Separate SFT trainer environment](adr/0015-our-own-sft-loop-in-a-separate-trainer-project.md)
- [0021 — Name-blind slot scoring](adr/0021-name-blind-slot-score-uses-placeholders.md)
- [0025 — Scoped Spark Open data work; proposed](adr/0025-muse-spark-open-only-for-glimmer-tune-data-work.md)
- [0031 — Spark Brief / Glimmer Surface split; proposed](adr/0031-spark-briefs-glimmer-writes-the-surface-behind-a-setting.md)

## Evidence and history

| Location | Contents and use |
|---|---|
| `acceptance/step-4.md` | Recorded live acceptance evidence for the initial Explorer build |
| `reasoning-effort-check.md`, `effort-check/` | Experiment procedure, reference values, scoring sheet |
| `scenarios/austender/` | Dated scenario reports and run artifacts; reports are evidence for particular settings/data |
| `summary-YYYYMMDD-N.md` | Session handoffs, completed work, findings, and next steps |
| `followups-2026-11-02.md` | Follow-ups recorded at that point in development |
| `bootstrap-build-prompt.md` | Original bootstrap brief, preceding the tool's detailed spec |
| `Follow the Contract · SaC prototype.html` | Static interaction/design reference for Explorer; not runtime client code |
| `../glimmer-tune/docs/` | Training/scorer reports, sample reviews, experiment decisions |

Reports refer to specific datasets, prompts, model settings, and run times. Some raw run
artifacts are ignored by Git and may be absent from a fresh checkout. Preserve enough
provenance in committed reports to assess a result without assuming every local artifact
will be available. Do not treat an old pass count as evidence for subsequent changes.

## Maintain this documentation

Keep setup commands in READMEs, behavior in specs, rationale in ADRs, and measured outcomes
in reports. Link related documents rather than copying long contracts into several places.
When behavior changes, update the relevant README and spec together; mark superseded decisions
and distinguish proposals from implemented features.

There is no remote issue tracker. Create `issues/<N>.md` using the next free number, starting
with `# <N>. <title>` and a status/opened/source line. Include Problem, Evidence, Options, and
Unknown or Decision needed as applicable. Reference code symbols rather than line numbers.
When fixed, record `fixed in <commit>` and the relevant amendment or ADR. See the root
[AGENTS.md](../AGENTS.md#issues) for the exact format.

Do not put credentials, secret-bearing DSNs, full model histories, or raw private data into
documentation. Use placeholders for operational examples and review generated artifacts
before committing them.
