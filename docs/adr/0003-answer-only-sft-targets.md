---
status: accepted
---

# SFT targets are answer-only at reasoning strength low

Muse Glimmer is a reasoning model. Its chat template renders a reasoning segment only when an assistant message has non-empty `reasoning_content`, and it appends `Reasoning strength: high.` unless the system prompt names a strength. We have gold Express but no reasoning traces, and dataset text must not be written by a model. So every SFT row puts `Reasoning strength: low.` in an explicit system prompt, and its assistant target is the wrapped Express with no reasoning. The fine-tuned model learns to answer directly, which suits our first requirement, speed.

## Considered options

- **Distil reasoning from base Glimmer:** rejected. It breaks the no-model-written-data rule, and sampling is billed even while SFT is free.
- **Train at the default (high):** rejected. Inference uses low everywhere, so the prompts would not match.

## Consequences

- The eval must compare the tuned model with base Glimmer at low and at high, to show what reasoning was worth.
- Training and inference must share one system prompt (`a2ui_eval.prompt.build_system_prompt`). If the inference prompt drifts, the tuned model sees a prompt it was never trained on.

## Amendment (2026-11-03): model-written words

The rule above, "dataset text must not be written by a model", was meant for what the model learns to produce: **reasoning traces and SFT targets (Express)**. Those stay model-free. ADR 0009 made an exception that this ADR never recorded, and this amendment records it.

- **Allowed:** model-written *words* that a spec section approves, with structure, paths and data still built by code. Today that means the synthetic vocabulary (titles, labels, sentences, records, icons), written once by `meta/muse-spark-1.3`, then validated, reviewed and committed (ADR 0009). It also covers the optional 2c-3 intent rewording, with the model logged per row.
- **Not allowed:** a model-written Express target or reasoning trace in any SFT row. ADR 0025 keeps the same line for Muse Spark Open.
- Dev and test intents stay hand-reviewed.

## Note (2026-11-04)

Under ADR 0033, targets are written by a teacher model (Glimmer or Spark Open, ADR 0034), so "dataset text must not be written by a model" no longer holds for targets. The decision stands: targets are answer-only, any teacher reasoning is stripped, and rows are at reasoning strength low.
