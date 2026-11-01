# LoRA tuning of Muse Glimmer 30B: progress report

As of 2026-11-01.

## Goal

Tune Muse Glimmer 30B with LoRA so that it writes better **generic A2UI Express**, against the A2UI v0.9 basic catalog ([ADR 0008](../../docs/adr/0008-train-generic-express-on-the-v0-9-basic-catalog.md)). Fireworks runs serverless SFT for this model for free until **2026-11-30**.

## What has been done

1. **Eval harness (milestone 1, approved 2026-10-31).** It scores a model's reply on several checks:
   - Does it compile?
   - Is it valid A2UI?
   - Is the component tree well formed, and do the data bindings resolve?
   - How well do the slots match (F1), and is it an exact match?
2. **Data pipeline (milestone 2a).**
   - The 43 A2UI gallery examples are split by design: 26 train, 7 dev, 8 test, and 2 multi-turn examples excluded. Train, dev and test never share a design ([ADR 0004](../../docs/adr/0004-splits-by-gallery-family.md)).
   - Templates exist for 6 designs, giving 240 training rows.
   - Training targets are the answer only, with reasoning set to low ([ADR 0003](../../docs/adr/0003-answer-only-sft-targets.md)).
   - The export drops rows that overlap with dev/test and fails on rows that are too long. The longest row is 5,418 tokens.
3. **Smoke run `gt-smoke-1`** (details in `runs/gt-smoke-1/run.md`).
   - Settings: LoRA rank 8, alpha 32, 1 epoch, batch 16, learning rate 1e-4, 15 steps, on 232 rows.
   - Loss went from 0.85 to 0.0002 and was near 0 by step 9. The model memorised the templates.
   - Adapter: `accounts/williamvoor/models/muse-starter-sl-gt-smoke-1`.
   - Cost: $0 for training. Eval sampling used about 97k tokens, which are billed.
4. **Eval data.**
   - A local review page (`python -m a2ui_eval.review`) renders each draft row's UI next to its intent.
   - All 29 draft rows are approved.
   - The dev split is built: 12 rows, all from one design (27_stats-card).
   - The test split (17 rows, 36_modal) is approved but deliberately not frozen. No training design teaches Modal yet.
5. **Switch to the v0.9 catalog** (ADR 0008). Done and reviewed independently (233 tests pass), but not committed yet.
   - It turned up a silent bug. The same Express means different things under v1 and v0.9, because `TextField`'s positional slots are in a different order. It compiles without error either way.
   - Fixed. The data was rebuilt, and every approval was kept.

## How is the tuned model better?

**Short answer: we don't know yet.** The only measurement is the smoke run, and its 8 held-out rows came from the same 4 designs and templates the model trained on. It shows that the pipeline works, not that the model generalises.

Here is what the smoke run shows. These numbers are our harness rescoring the saved predictions (`runs/gt-smoke-1/predictions_*.jsonl`).

| On 8 held-out rows | Base | Tuned |
|---|---|---|
| Compiles | 7/8 | 8/8 |
| Valid A2UI (SDK check) | 5/8 | 8/8 |
| Exact match | 0/8 | 8/8 |
| Slot F1 (mean) | 0.34 | 1.00 |
| Sampled tokens, all 8 rows | 8,400 | 1,941 |

What is behind the numbers:

- **Fewer invalid replies.** Three base replies failed:
  - One had a syntax error, a stray quote, so it didn't compile.
  - Two passed `None` where a password length check needs an integer.

  The tuned model made none of these errors. This is a real gain, but it was measured on familiar designs.
- **Exact match and F1 mostly measure memorised conventions.** The base model's replies were reasonable UIs. They lost points for choices the request never specified, such as data key names (`/signIn/email` instead of `/email`), the surface ID, and text variants like `caption`. The tuned model learned the templates' conventions. On unseen designs that advantage is much smaller. That's why dev and test score slot F1 and structure, not exact match.
- **About 4× fewer sampled tokens, so faster replies.** The visible answers were the same length (about 890 characters). The difference is reasoning tokens: the base model still reasons at "low", while the tuned model learned to skip reasoning from answer-only targets. This gain is likely to carry over to new designs, since it doesn't depend on the templates. It matters because speed is the project's top requirement.
- **Not comparable with later runs.** gt-smoke-1 was trained on the old v1 prompt, so it can't serve as a baseline for v0.9 runs.

An honest answer needs two things that don't exist yet:
- **Templates for most designs (2b).** Then train covers enough variety.
- **A base-vs-tuned eval on dev and test.** Those are designs the model has never seen.

## Not done yet

- **2b templates** for the remaining ~35 designs. This is the main gap: the model has seen only 4 designs.
- **Freezing the test split**, once 2b adds a train design with Modal.
- **An eval runner of our own** comparing base vs tuned on dev/test (milestone 3).
- **A real training run** on the v0.9 data.
- **Deploying the tuned adapter** for serving.

## Risks

- About 4 weeks of free SFT remain, and 2b is the bulk of the remaining work.
- Dev covers a single design, so its score doesn't yet say much about generic A2UI.
- The memorisation in the smoke run means more variety will help, but more rows of the same designs won't.
