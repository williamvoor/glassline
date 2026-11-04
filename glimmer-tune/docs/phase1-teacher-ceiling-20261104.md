# Distill Phase 1: teacher ceiling on gallery dev (2026-11-04)

**Question.** Does a teacher's best-of-8, filtered by validator layers 1–3, clearly beat s225 (0.523 name-blind slot F1 on gallery dev)? The brief says not to proceed if neither teacher does.

**Answer.** No. Spark Open at effort low comes out +0.03 over s225, paired. Glimmer base + 3 + grammar comes out −0.04. Both intervals span about ±0.2.

## Setup

- **Rows.** The 84 gallery dev rows, with the student prompt and the 3 frozen few-shot examples (`exports/phase1_gallery_fewshot3.jsonl`, the gallery rows of `export-dev --fewshot`).
- **Samples.** 8 per row, with the sampling settings each model card recommends:
  - Glimmer base (`runs/sft1` s000): temperature 1.0, top_p 0.95, top_k 64, with the Express grammar.
  - Spark Open (`muse-spark-open-1026`, Baseten): effort low, temperature 1.0, top_p 1.0, max 8192 tokens, no grammar. Baseten doesn't enforce structured outputs for Spark Open.
- **Validator.** Layers 1–3 of `a2ui_eval/validate.py`:
  - layer 1: mechanical checks;
  - layer 2: render at 375 and 1280 px;
  - layer 3: the S8 lint.

  The validator's pick is the passing candidate with the fewest tokens (`validate.pick`). A row with no passing candidate scores its sample 0.
- **Oracle.** The best of the 8 by F1 against the reference. It's an upper bound, because selection never sees the reference.
- **Intervals.** Family-level, 7 gallery dev families.

## Results

`runs/phase1/report.json` and `report.md`, written by `python -m a2ui_eval.phase1 report`:

| Teacher | Single | Validator best-of-8 (CI) | Rows with a valid candidate | Oracle best-of-8 | Compiles | Outside catalog | Candidates passing | Answer tok p50/p95 | Reasoning tok p50/p95 | Cost |
|---|---|---|---|---|---|---|---|---|---|---|
| Glimmer base + 3 + grammar | 0.466 | 0.486 (0.415–0.561) | 77% | 0.687 | 0.87 | 0.10 | 55% | 274/389 | 4/740 | $3.09 |
| Spark Open, effort low | 0.497 | 0.555 (0.511–0.603) | 77% | 0.678 | 0.86 | 0.04 | 66% | 338/433 | 168/1087 | $6.96 |

The paired comparison is in `runs/phase1/paired_vs_s225.json` (`runs/phase1/paired_vs_s225.py`). It is the validator pick minus s225, row by row, families resampled. The s225 predictions were rescored with the current scorer: 0.523 without grammar, 0.515 with grammar.

| Teacher | vs s225 | vs s225 + grammar |
|---|---|---|
| Glimmer base + 3 + grammar | −0.037 (−0.210 to +0.176) | −0.029 (−0.204 to +0.181) |
| Spark Open, effort low | +0.032 (−0.114 to +0.231) | +0.040 (−0.108 to +0.240) |

A one-off rerun of the validator (a scratch script, not committed) gave:
- **Selection.** A random valid candidate scores 0.511 (Glimmer) and 0.541 (Spark Open). The best valid candidate by F1, which is what a perfect judge would pick, scores 0.607 and 0.609.
- **Where rows had no valid candidate.** All the gaps are in two families:
  - `33_financial-data-grid`: 2/12 rows covered (Glimmer) and 0/12 (Spark Open). The cause is overflow at 375 px, which its gallery reference also fails.
  - `32_advanced-form-validator`: 3/12 and 5/12. The causes are `required` on a checkbox and outside-catalog checks.
  - The other 5 families had every row covered.
- **Top failure reasons:**
  - Glimmer: schema errors (165 of 672 candidates), outside the catalog (134, despite the grammar), not compiling (88), overflow (67).
  - Spark Open: not compiling (92), overflow (47), `required` on a boolean input (21), outside the catalog (19).

## Reading

1. **The bar.** Neither teacher clearly beats s225. Spark Open is ahead on the point estimate and is the cleaner writer (outside catalog 0.04 against 0.10). Its paired interval still includes zero.
2. **Selection.** A perfect judge would reach about 0.61 with either teacher. That is about +0.09 over s225, still unproven with 7 families. A real judge would land between the random-valid and the perfect-judge figures.
3. **Few-shot.** Spark Open with 3 examples scores 0.497 single-sample. Without them it scored 0.290 (ADR 0025 baseline, a different prompt). Teacher prompting is the cheapest lever left. Tuning it needs the Phase 2 tune slice, not dev.
4. **The intervals.** With 7 gallery dev families, a paired difference smaller than about ±0.15 can't be resolved. "Clearly beats" is close to unreachable for any teacher on this split. That is a limit of the measurement, not only of the teachers.
5. **Speed.** Spark Open's answers are a bit longer (338 vs 274 tokens at p50). A student distilled from it might write longer answers than base + 3 does.

Total spend: $10.13, including $0.08 of probes.
