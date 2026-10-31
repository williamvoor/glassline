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
