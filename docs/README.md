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

Explorer’s approved SPEC takes precedence over its original BUILD_PROMPT. Dated amendments
supersede earlier sections. VicProp’s spec remains labelled draft; Glimmer’s spec is a
chronological record whose latest sections apply.

Read ADR status before relying on it: 0020 is superseded by 0030, 0025 by 0034, and the
0031/0042 serving integration was stopped. An accepted design does not prove implementation;
check code and tests. READMEs describe current setup and operation.

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
- [0039 — Workspaces share one agent process](adr/0039-workspaces-are-sub-apps-of-one-agent-process.md)
- [0040 — Saved Conversations keep SQL, not rows](adr/0040-saved-conversations-keep-sql-not-rows.md)
- [0041 — Bootstrap becomes Settings](adr/0041-bootstrap-becomes-the-explorers-settings.md)
- [0044 — New Workspace wizard](adr/0044-new-workspace-is-a-wizard-with-inline-cards-and-model-chips.md)
- [0045 — Datasets are deployment data](adr/0045-datasets-are-deployment-data-not-part-of-the-explorer.md)
- [0048 — The explorer has one mode: Workspaces with Settings](adr/0048-the-explorer-has-one-mode-workspaces-with-settings.md)

### A2UI evaluation, training, and stopped integration

- [0003 — Answer-only SFT targets](adr/0003-answer-only-sft-targets.md)
- [0004 — Family-based splits](adr/0004-splits-by-gallery-family.md)
- [0008 — Generic v0.9 basic-catalog training](adr/0008-train-generic-express-on-the-v0-9-basic-catalog.md)
- [0009 — Catalog-driven Skeletons](adr/0009-synthetic-surfaces-from-the-catalog-with-skeletons.md)
- [0011 — Synthetic reference house style](adr/0011-synthetic-references-follow-a-house-style.md)
- [0014 — Brief plus Result schemas as Glimmer input](adr/0014-glimmer-input-is-a-brief-plus-result-schemas.md)
- [0015 — Separate SFT trainer environment](adr/0015-our-own-sft-loop-in-a-separate-trainer-project.md)
- [0021 — Name-blind slot scoring](adr/0021-name-blind-slot-score-uses-placeholders.md)
- [0025 — Original Spark Open boundary; superseded by 0034](adr/0025-muse-spark-open-only-for-glimmer-tune-data-work.md)
- [0031 — Spark Brief / Glimmer Surface split; stopped](adr/0031-spark-briefs-glimmer-writes-the-surface-behind-a-setting.md)
- [0033 — Distilled and filtered training data](adr/0033-glimmer-training-data-is-distilled-and-filtered.md)
- [0034 — Spark Open may teach and judge](adr/0034-muse-spark-open-may-teach-and-judge-glimmer-training-data.md)
- [0038 — Gallery dev plus a held-out Explorer slice](adr/0038-the-v2-swap-in-gate-is-gallery-dev-plus-a-held-out-explorer-slice.md)
- [0042 — Two Glimmer serving setups; stopped](adr/0042-two-glimmer-setups-s225-alone-and-base-plus-3.md)

## Evidence and history

| Location | Contents and use |
|---|---|
| `acceptance/step-4.md` | Recorded live acceptance evidence for the initial Explorer build |
| `reasoning-effort-check.md`, `effort-check/` | Experiment procedure, reference values, scoring sheet |
| `scenarios/austender/` | Dated scenario reports and run artifacts; reports are evidence for particular settings/data |
| `summary-YYYYMMDD-N.md` | Session handoffs, completed work, findings, and next steps |
| `followups-2026-11-02.md` | Follow-ups recorded at that point in development |
| `bootstrap-build-prompt.md` | Original bootstrap brief, preceding the tool's detailed spec |
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

Issues live in `issues/<N>.md`; follow the [root issue format](../AGENTS.md#issues).

Do not put credentials, secret-bearing DSNs, full model histories, or raw private data into
documentation. Use placeholders for operational examples and review generated artifacts
before committing them.
