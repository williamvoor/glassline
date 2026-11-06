# glimmer-trainer

Separate Fireworks SFT environment for Muse Glimmer 30B. Its SDK dependencies
require a separate environment from the compiler/scorer harness. Scoring runs through the
harness CLI below.

Spec and experiment status: [SPEC](../SPEC.md). Model/data rules: [AGENTS.md](../AGENTS.md).

## Setup

Requires Python 3.13 and `uv`. Tokenizer loading on first use may need network
access (Hugging Face). This project has its own `pyproject.toml`, `uv.lock`,
and virtual environment.

```bash
cd glimmer-tune/trainer
uv sync --locked
```

## Commands

All commands run from `glimmer-tune/trainer/`. They expect the export files in
`glimmer-tune/exports/` (built by `python -m a2ui_eval.data_cli export...`
from `glimmer-tune/`) and the frozen prompt at
`glimmer-tune/data/system_prompt.txt`.

```bash
uv run pytest                                  # tests; remote SDK stubbed, no spend
uv run python -m glimmer_trainer --help
uv run python -m glimmer_trainer prepare --train ../exports/sft_train.jsonl
uv run python -m glimmer_trainer train --train ../exports/sft_train.jsonl  # validate only
```

`prepare` and plain `train` never open a remote session. Both default to
`../exports/sft_mix.jsonl`, `../exports/sft_dev.jsonl`, and the frozen prompt;
flags select other files (see `--help`). The run 2 `sft_mix.jsonl` is retired
(`../archive/exports/`, ignored), so pass the v2 export explicitly.

## Paid commands (explicit approval required)

```bash
uv run python -m glimmer_trainer train --train ../exports/sft_train.jsonl --launch --run-id NAME
uv run python -m glimmer_trainer sample --run ../runs/NAME --ckpt s225 --cases ../exports/sft_dev.jsonl --out ../runs/NAME/predictions.jsonl
```

Replace `NAME` and `s225` with your run/checkpoint; train/export files must exist first.

- `train --launch` opens a remote Fireworks training session; sampling is billed. Check current
  service terms and the project’s [remote-work rules](../AGENTS.md) before launching.
- `sample` refuses to overwrite predictions. Use `--limit N` for a probe,
  `--workers 1` when measuring latency, `--budget` to cap token cost.
- Commands do not automatically load the repo root `.env`; supply the relevant
  environment or use `uv run --env-file ../../.env …` from `trainer/`.
- State the current service terms and cost before remote work. Credentials must
  never appear in source, logs, or committed manifests.

## Scoring predictions

Score existing predictions from `glimmer-tune/` without another model call:

```bash
cd ..
uv run python -m a2ui_eval.checkpoints score runs/example/predictions.jsonl --out runs/example/scores.json
```

Score on dev; the test split stays frozen until the decided final evaluation
(`export-test`). Select checkpoints on dev only.

Model provenance, reasoning settings and split guards follow the
[harness rules](../README.md#model-and-experiment-boundaries).
