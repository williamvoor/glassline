# Run 2 decision memo: which Glimmer setups go to the test split (2026-11-04)

**Recommendation:** take two setups to the test split: **s225** (run 2's tuned adapter) and **base + 3 examples + grammar**. They are level on gallery quality. They differ on what a serving decision turns on: speed and token use for s225, against no adapter to host and clean catalog use for the other. ❗️ The test split can be used only once. If run 3 happens (free SFT until 2026-11-30), hold the test evaluation until after it.

## Quality (dev gallery, 84 rows in 7 families, name-blind slot F1)

| Setup | Gallery | 95% CI (families resampled) | Compiles | Outside the catalog | Synthetic dev |
|---|---|---|---|---|---|
| Base | 0.245 | 0.14–0.37 | 0.77 | 0.00 | 0.31 |
| Base + grammar | 0.301 | 0.19–0.43 | 0.94 | 0.00 | 0.56 |
| Base + 3 examples | 0.474 | 0.37–0.59 | 0.81 | 0.16 | 0.35 |
| **Base + 3 + grammar** | **0.505** | 0.39–0.62 | 0.86 | **0.04** | 0.58 |
| Run 1 s250 (rescored) | 0.514 | 0.38–0.63 | 0.83 | 0.25 | (old set) |
| **Run 2 s225** | **0.523** | 0.36–0.65 | 0.85 | 0.19 | 0.92 |
| s225 + grammar | 0.515 | 0.35–0.64 | 0.85 | 0.16 | 0.92 |

Paired differences on the same rows, with families resampled:

| Comparison | Difference | 95% CI |
|---|---|---|
| s225 − s250 | +0.009 | −0.08 to +0.10 |
| s225 − base + 3 | +0.050 | −0.07 to +0.16 |
| s225 − base + 3 + grammar | +0.011 | −0.22 to +0.19 |
| base + 3 + grammar − base + 3 | +0.031 | −0.05 to +0.16 |
| s225 + grammar − s225 | −0.008 | −0.017 to −0.002 |

- **No setup beats another on the gallery** except against plain base. With 7 families, the dev gallery can't separate gaps under about 0.1.
- **The grammar helps the untuned model and not the tuned one.** For base + 3 it cuts outside-catalog output from 16% to 4%, and fixes `financial-data-grid` (0.27 → 0.66). s225 routes around it: it moves the same conditionals into made-up named arguments and `${…}` strings. Three of its rows also ran into the 3,072-token cap, writing comment or whitespace loops. See `docs/s225-gallery-review-20261104.md`.
- **s225's synthetic score (0.92)** measures data close to what it trained on, not general skill.

## Speed (shared training pool, one request at a time)

| Setup | Tokens p50 / p95 (full dev) | Latency p50 / p95 (64-row subset) | Cost per 1k requests* |
|---|---|---|---|
| Base | 1,240 / 2,078 | 6.8 s / 29.3 s | $7.0 |
| Base + grammar | 1,217 / 2,116 | 7.1 s / 14.1 s | $8.2 |
| Base + 3 | 755 / 1,552 | 5.0 s / 24.6 s | $5.8 |
| Base + 3 + grammar | 792 / 1,585 | 2.4 s / 17.9 s | $6.4 |
| s250 | (subset 295 / 551) | 3.0 s / 7.9 s | $3.9 |
| **s225** | **332 / 591** | **3.1 s / 8.3 s** | **$3.7** |
| s225 + grammar | 332 / 624 (3 cut off) | 2.7 s / 8.0 s | $4.2 |

- **Tokens are the reliable measure.** s225 writes about 2.4× fewer tokens than base + 3, and its p95 is much tighter.
- **The latency figures are indicative only.** They include the network and queueing on the shared pool, which is not a serving deployment. Two runs with the same tokens (base + 3 with and without the grammar, p50 402 vs 403) differed 2× at p50. That's queueing, not the grammar.
- \*Cost is at the training SDK's sampling prices (prefill $1.96, cached $0.39, sampled $4.88 per 1M), with the measured prompt-cache hit rate of 94–97%.

## Serving cost: the open question

| Setup | What serving needs |
|---|---|
| s225 | A LoRA adapter on Glimmer 30B. I don't know whether Fireworks serves Glimmer adapters per token. If it doesn't, it needs an on-demand deployment: an H100 is $8/h, about $5,800/month always on. |
| Base + 3 + grammar | The base model and a `response_format` grammar. That's per-token **if** Glimmer base is still serverless. It reportedly was deprecated from 2026-09-25 (unconfirmed). If not, it also needs a deployment. |

At low traffic, the hosting path dominates the cost, not tokens per request. **Find out whether Glimmer is available per token (base and LoRA) before the serving decision.**

## Decisions needed

1. **Test split now, or after run 3?** Recommendation: after run 3, if one happens.
2. **Run 3's target.** Signed and negative values shown plainly (the `financial-data-grid` failure), and state-driven icons from a field rather than conditionals. A stricter grammar (per-component named arguments) would only matter for the tuned model.
3. **Serving path.** Check Fireworks' per-token availability for Glimmer 30B base and adapters.

Measured 2026-11-04, about $6.8 at list prices. Data: `runs/compare-run2/analysis.json`, `runs/latency-run2/`, `runs/grammar-run2/`.
