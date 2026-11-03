---
status: proposed
---

# Muse Spark Open is used only for glimmer-tune data work, never in the explorer's request path

Live calls are otherwise limited to `meta/muse-spark-1.3`, Muse Glimmer (only for A2UI) and Muse Wonder (only in the dataset bootstrap, ADR 0020). We have Baseten credits ($100, up to 2M tokens per minute) for Muse Spark Open, Meta's open-weights model (816B mixture of experts, 42B active, released 2026-10-15). Tuned Glimmer's weak point is designs it never trained on (gallery dev name-blind slot F1 0.51, `glimmer-tune/docs/tuning-report-20261102.md` §6–7), and the fixes need more varied data and more checking than we can write or read by hand. We allow Spark Open in `glimmer-tune/` for offline data work, through Baseten, and nowhere in `explorer/`.

Allowed:
- **Teacher baseline (done 2026-11-03):** Spark Open answered the dev prompts (`python -m a2ui_eval.teacher baseline`) and the existing scorer scored them. It never sees the test split.

Allowed once a spec section approves each one:
- writing words, as `meta/muse-spark-1.3` does for vocabulary today (domains, intents, rewordings, per-item state topics), with structure, paths and data still built by code (ADR 0009);
- flagging rows whose intent and reference disagree, for a person to review. Its verdicts never change a row by themselves.

Not allowed: Spark Open's Express answers as SFT targets. The baseline ruled it out as a teacher (below).

## Baseline result (2026-11-03)

`muse-spark-open-1026` on all 284 dev rows, scored by the same scorer as run 1 (`glimmer-tune/runs/teacher-spark-open/score.full.json`; `s250` and `s000` rescored with that scorer gave unchanged slot scores):

| Model | Selection blind | Gallery blind | Synthetic blind | Gallery compiles |
|---|---|---|---|---|
| Glimmer base `s000` | 0.273 | 0.232 | 0.313 | 0.74 |
| Spark Open | 0.326 (0.289–0.366) | 0.290 | 0.362 | 0.69 |
| Glimmer tuned `s250` | 0.726 | 0.514 | 0.937 | 0.83 |

Spark Open writes Express about as well as untuned Glimmer: named arguments (`formatCurrency(value:…)`), icon names outside the catalog, a missing `root`, and extra components. It ties tuned Glimmer only on the two families tuned Glimmer also fails. It barely invents operators on gallery (2/84 rows outside the catalog, against `s250`'s 21), which suggests tuned Glimmer's invented conditionals come from tuning. Cost $3.39 at list price with no cache discount; median 1,231 output tokens and 8.3 s per row.

## Considered options

- **Spark 1.3 only:** keeps one rule, but the Baseten credits go unused and Spark 1.3 calls are billed.
- **Spark Open in the explorer as well:** might be faster, which matters most there. It is a separate product decision (latency, prompt caching on Baseten, the license's attribution term) and is out of scope here.

## Consequences

- The license reportedly permits fine-tuning and distillation, but requires products to display "Built with Muse Spark Open". Whether that applies to a Glimmer adapter trained on its output is unknown. Read the license itself before any of its text reaches training data.
- Words it writes pass the same validators and eval-literal rule as Spark 1.3's vocabulary. Anything it writes is checked against dev and test fingerprints and wording, because it may know the public A2UI gallery.
- The LiteLLM model string and Baseten prices in `a2ui_eval/teacher.py` are unverified as of 2026-11-03.
- Tests stub the model, as for every other model call.
