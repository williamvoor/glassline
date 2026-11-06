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

Read an ADR's status before relying on it: some are superseded and some were stopped. An accepted design does not prove implementation;
check code and tests. READMEs describe current setup and operation.

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
