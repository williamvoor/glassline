---
status: accepted
---

# Our own SFT loop, in a separate trainer project, picks the checkpoint by dev score

The cookbook's Muse Glimmer starter holds out random rows from the training file and keeps only the final checkpoint. The smoke run's loss collapsed by step 9, so the final checkpoint is likely the memorised one, and a same-file holdout can't show it. We decided to write our own small loop. It saves a sampler checkpoint every 25 steps and samples our separate dev split (gallery and synthetic reported apart, ADR 0009). Checkpoints are ranked by dev slot F1, not by loss. The loss is a per-token mean over each batch, so the learning rate means what it says.

The Fireworks training SDK and the cookbook need `huggingface-hub<2` and `pydantic<2.13`, while the A2UI SDK pin (ADR 0006) has 2.4 and 2.14. The loop therefore lives in its own uv project, `glimmer-tune/trainer/`. It never imports `a2ui_eval`. It reads exported JSONL, writes predictions, and runs the scorer as a subprocess in the main environment.

## Considered options

- **The cookbook starter with `--n-eval`:** rejected. Its holdout is same-family, and it keeps no intermediate checkpoints.
- **One environment:** rejected. It would mean downgrading the A2UI SDK's dependencies under the pin that training and serving share.
- **Summed token loss, as in the smoke run:** rejected. The gradient norm was 28k and was clipped on every step, so the update size ignored the learning rate.

## Consequences

- The system prompt is frozen as a committed text file (`data/system_prompt.txt`). The export, the trainer and a test all check it, because changing `build_system_prompt` invalidates the adapter.
- Eval sampling is billed. Every checkpoint is scored on a fixed 64-row dev subset. Base and the two best checkpoints are scored on the full dev split.
