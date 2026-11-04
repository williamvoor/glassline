# Task: Restart Glimmer tuning with a distill-and-filter approach

## Why we're changing course
Runs 1 and 2 plateaued on gallery dev (name-blind slot F1 ~0.52, level with
base + 3 examples at 0.47; interval includes zero). Template and skeleton
synthetic data taught a house style that doesn't transfer to realistic asks,
and invented operators persist (outside-catalog rate 0.19 on gallery).

New approach: a strong teacher writes many candidate answers to realistic
intents, a layered validator renders and checks every candidate, and only the
best valid candidate per intent becomes training data. We compare two
teachers before committing: Muse Spark Open, and Glimmer itself (best-of-n
self-distillation).

The measurement stack is the valuable part of the last two weeks. Keep it.
Replace the data strategy.

## Decisions already made (record them in ADRs)
- **Muse Spark Open is acceptable as a teacher.** New ADR: its outputs may be
  used for training. Consequence: the tuned model is a Muse Spark Open
  derivative under the Muse Model License, so the explorer UI must
  prominently display "Built with Muse Spark Open". Quote the relevant license
  clauses from the license text itself (Hugging Face model card / LICENSE),
  and link them. Also check the terms of whichever provider hosts Spark Open
  for us (Fireworks or Baseten) for any extra restriction on using outputs
  for training, and quote what you find.
- **Meta's hosted Model API models (`muse-spark-1.x`, `muse-wonder-*`) stay
  out of training data**, per Model API terms clause (ix). They may be used
  only for evaluation/benchmarking. This retires the `muse-spark-1.3`
  vocabulary from ADR 0009.
- **Test split stays unopened** until the final candidate. Gallery dev is the
  primary metric. Synthetic-style dev is a regression guard only.
- **Explorer launch (2026-11-25) does not depend on this track.** Keep a
  ready serving config for base Glimmer + 3 examples + grammar as the
  launch default. This new track swaps in an adapter only if it clearly wins.

## Phase 0: Tag, inventory, clean up (do this first)

1. `git tag pre-distill-v1` on the current commit, and push the tag, so
   everything removed stays recoverable.
2. Produce `docs/cleanup-plan.md`: a table of every directory/module/data file
   with a verdict (KEEP / ADAPT / RETIRE / DELETE) and a one-line reason.
   **Wait for my OK before removing anything.**
3. Use these defaults, and flag anything you disagree with:

**KEEP (reuse as is):**
- Eval harness and scorer: `a2ui_eval/{sdk,refs,surfaces,structure,align,score}.py`,
  the name-blind slot F1, the outside-catalog check, `selection_ci` and
  family-level bootstrap, `checkpoints compare`.
- Gallery seeds (`seeds/gallery/`), v0.9 conversion (`convert.py`), the
  frozen dev and test splits and their exports, `fingerprint.py` and the
  leakage guards.
- Review page (`a2ui_eval/review.py`, `review/`) with `@a2ui/lit` rendering.
- `CatalogModel` and the S8 linter rules (these become validator layer 3).
- Trainer project `trainer/` (checkpointing, background subset sampling,
  promotion, `sample` command), with `train_unembed=False`.
- The test-split tooling from section 5 of the 2026-11-02 report.
- All ADRs. Mark superseded ones as "Superseded by ADR NNNN" rather than
  deleting them.

**ADAPT:**
- Export: keep the format, guards and token checks, but take rows from the
  new distilled dataset instead of templates/synthetic.
- Checkpoint selection: gallery dev F1 as the primary score, synthetic only as
  a regression guard. Select on full dev or a gallery subset of ≥ 42 rows,
  not the old 64-row mixed subset.
- System prompt: keep the run 2 prompt (no-conditionals line, renamed rule 7)
  as the student prompt. Teacher prompts are separate files.

**RETIRE (move to `archive/`, out of imports, tests and CI):**
- Jinja gallery slot templates and their generator.
- Synthetic skeleton generator, skeleton files, the vocabulary written by
  `muse-spark-1.3`, and the house-style rules (ADR 0011).
- Run 1 and run 2 training exports (`exports/sft_*.jsonl` built from
  templates/synthetic).

**KEEP AS HISTORY (read-only; don't delete):**
- `runs/sft1/` and `runs/sft2/`: manifests, scores and predictions. They are
  the baselines the new approach has to beat. Don't retrain or rescore them
  except with the current scorer for comparison.
- The synthetic dev rows: keep them as a regression guard set, labelled as
  such.

**DELETE:**
- Only truly dead code: scripts with no remaining caller after the
  retirements, and scratch files. List each one in the plan.

4. After my OK: carry out the plan, keep all KEEP tests green, update
   `SPEC.md` (new "Approach v2" section) and `README`, and commit the cleanup
   on its own, separate from new work.

## Phase 1: Teacher ceiling (go/no-go)
Before building any data pipeline, measure what each teacher can do on
**gallery dev** (84 rows), scored with the name-blind scorer:
- Muse Spark Open, hosted (Fireworks or Baseten, whichever our credits cover),
  at `reasoning_effort` low and medium, with the best teacher prompt
  (the student prompt + 3 examples is fine to start with) and grammar if the
  host supports it.
- Glimmer base + 3 examples, sampled best-of-8.
- For each: single-sample score, and best-of-8 after the validator
  (Phase 3, layers 1–3 are enough here), so we see how much filtering adds.

Report a table: gallery F1, compile rate, outside-catalog rate, output tokens
(answer and reasoning separately), and cost. **Stop and wait for my decision.**
If neither teacher beats s225 (0.523) clearly on best-of-8, we don't proceed.

Important: tuning the teacher prompt on gallery dev would make dev a biased
measure. Do all teacher-prompt tuning on a separate tuning slice (Phase 2),
and run gallery dev only to compare finished prompts.

## Phase 2: Intent set
Intents are the new bottleneck. Build `data/intents/`:
- A few thousand realistic requests, in the register of rules R1–R6 (a real
  user asking, with no component, prop, path, event or icon names).
- Weight toward explorer-style asks: a Brief plus Result schemas over
  realistic tables (dashboards, grids, stats, lists, detail views, filters,
  forms that write back). Generate these from templates/code plus a schema
  bank. Spark Open may help write or vary intents now that its license is
  accepted; record which rows it wrote. Never use Spark logs or hosted
  Model API output.
- Include states that vary per item ("losses shown differently", "completed
  steps"), update turns, and text-only asks (`expect_ui: false`).
- Split: `train` (bulk), `tune` (~150, for teacher-prompt tuning), and
  fingerprint-check both against gallery dev and test.
- A provenance field on every row: which person, script or model wrote
  each text field.
- Show me 30 random intents before generating more than 500.

## Phase 3: Validator (layered; every candidate goes through all layers)
Build `a2ui_eval/validate.py`. Each layer records pass/fail and reasons:
1. **Mechanical:** compiles, valid per the SDK/schema, nothing outside the
   catalog, sound tree, bindings resolve against the data model.
2. **Render:** headless `@a2ui/lit` render (Playwright) with no errors; no
   empty containers; no overflow at a phone width and a desktop width. Save
   the screenshot.
3. **Lint:** the existing S8 rules.
4. **Intent fidelity:** a judge sees the screenshot, the Express and the
   intent, and fills a fixed rubric: requested elements present, primary
   action present, sensible grouping, labels fit the ask, nothing invented.
   - Judge model: Spark Open or Glimmer (both acceptable now). Never a hosted
     Model API model.
   - Calibrate it: I hand-label 50 candidates, and the judge must agree on
     ≥ 85% of pass/fail before it's used for filtering. Report the agreement.
5. **Diversity:** structural dedupe (existing fingerprint), and a cap on
   near-identical layouts per intent cluster.

Selection per intent: among candidates that pass layers 1–4, take the highest
judge score and break ties by fewer tokens. If none pass, drop the intent and
log why (for intent-set fixes, not for re-prompting on dev).

Tests: unit tests per layer, including fixtures for invented conditionals,
unresolved bindings, render errors, and a judge-disagreement case.

## Phase 4: Generate and filter
- Teacher(s) per the Phase 1 decision: n = 4–8 candidates per intent,
  temperature at provider defaults, answer-only extraction (strip
  reasoning).
- Track acceptance rate per layer and per intent type. If acceptance for a type
  is below 30%, stop and show me examples before spending more.
- Show me 20 accepted and 10 rejected rendered samples before full-scale
  generation.
- Cost: estimate before starting (tokens × provider price, or GPU-hours) and
  wait for my OK. Use Fireworks/Baseten/Modal credits.

## Phase 5: SFT run (v2-run1)
- Same trainer, `train_unembed=False`, answer-only targets, student prompt.
  Start from run 2's hyperparameters, and explain any change.
- Gallery-first checkpoint selection, as in Phase 0 (ADAPT).
- Free Fireworks SFT ends 2026-11-30 Pacific. Verify the exact cut-off, and
  plan so that the job starts with margin.

## Phase 6: Evaluation
On gallery dev, the synthetic regression set, and a small explorer-shaped dev
slice (20–30 hand-built rows from the explorer draft spec, built by hand or
from templates):
- Compare: base + 3 examples (+ grammar), s225 (+ grammar), the new adapter
  (+ grammar), and the teacher (as a ceiling).
- Quality (name-blind F1, compile, outside catalog), output tokens p50/p95/max,
  family-level intervals.
- Write a one-page decision memo. Then, only on my OK, spend the test split
  once on the final two candidates plus base.

## How to work
- Start with Phase 0, steps 1–2 only, and wait for my OK on the cleanup plan.
- Stop for my decision after Phase 1, after the 30-intent sample, after
  judge calibration, and before any paid generation run.
- Keep the existing discipline: failing test before each fix, refuter round
  per phase, every number in a report traceable to a file.
