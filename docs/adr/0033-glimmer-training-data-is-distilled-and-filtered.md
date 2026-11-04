---
status: accepted
---

# Glimmer's training data is distilled from teacher candidates and filtered by a layered validator

Runs 1 and 2 trained on two sources that code built from the gallery: Family Templates (ADR 0004) and catalog-driven Skeletons in a house style (ADRs 0009, 0011). Both plateaued on gallery dev. s225 scores 0.523 name-blind slot F1, level with base Glimmer + 3 examples (about 0.47); every family-level interval includes 0 (`glimmer-tune/docs/run2-decision-memo-20261104.md`). The skeleton data taught a house style that doesn't transfer to realistic asks, and invented operators persisted.

We replace that data strategy. A teacher writes several candidate answers (n = 4–8) to each realistic intent. Every candidate goes through a layered validator in `a2ui_eval/validate.py`:
1. mechanical checks: compile, SDK/schema, catalog and bindings;
2. a headless render at phone and desktop widths;
3. the S8 lint rules;
4. a calibrated judge for intent fidelity;
5. structural dedupe.

Only the best valid candidate per intent becomes a training row, picked by highest judge score and then fewest tokens. Intents are realistic requests written in the register of rules R1–R6, weighted toward explorer-shaped asks (a Brief plus Result schemas). The brief and its phases are `glimmer-tune/docs/distill_restart_prompt.md`. The SPEC section is "Approach v2".

The teacher is Glimmer itself (best-of-n), Muse Spark Open (ADR 0034), or both. It is chosen in Phase 1 by measurement on gallery dev. If neither clearly beats s225 at best-of-8, the track stops.

## What stays

- Splits by gallery Family (ADR 0004), and the fingerprint and intent leakage guards.
- Answer-only targets at reasoning strength low (ADR 0003). The reasoning is stripped from teacher output.
- The frozen student prompt (ADR 0015), the SDK pin (ADR 0006), and the name-blind score (ADR 0021).
- The frozen gallery dev and test cases. The generator that made them is archived, so the case files are now the source of truth.

## Considered options

- **More templates or skeletons (run 3 on the old data):** rejected. Two runs show the data, not the training settings, is the ceiling. More of the same data is likely to stay level with base + 3.
- **Base + 3 + grammar only, no more tuning:** kept as the explorer launch default. It isn't the end of the track, because the grammar doesn't fix s225's or base's invented named arguments and strings.
- **A teacher without filtering:** rejected. Spark Open's raw Express scores about base level (ADR 0025's baseline). Filtering is what could make a teacher's output better than the student's.

## Consequences

- Supersedes ADR 0009 (synthetic surfaces from skeletons) and ADR 0011 (house style). Their generators, data and tests are in `glimmer-tune/archive/` (`glimmer-tune/docs/cleanup-plan.md`, tag `pre-distill-v1`).
- ADR 0003's premise, "dataset text must not be written by a model", no longer holds for targets. The answer-only, reasoning-low rule still does.
- Checkpoint selection is on gallery dev only. Synthetic dev is a regression guard (commit `9ad28e3`).
- Intents become the bottleneck. Each row records which person, script or model wrote each text field.
- The judge must agree with 50 hand labels on at least 85% of pass/fail before it is used for filtering.
- Free Fireworks SFT ends for jobs started after 2026-11-30 Pacific. The first v2 run has to start before then, or the track costs money.

## Note (2026-11-04)

Phase 2 (user decision): "explorer-shaped asks" means the **content** of the asks (dashboards, grids, stat tiles, lists, detail views, filters, forms that write back), not the Brief + Result schemas input. Every intent is generic v0.9 Express in the R1–R6 register with inline values. Brief rows belong to run 3 (ADR 0014, ADR 0008 note). The swap-in gate becomes gallery dev plus an explorer-shaped dev slice. glimmer-tune SPEC "Approach v2 Phase 2".
