# glimmer-trainer

Separate Fireworks supervised fine-tuning (SFT) environment for Muse Glimmer 30B
(ADR 0015). It is separate from the harness because its Fireworks training
SDK/cookbook dependencies differ from the compiler/scorer environment. Do not
install trainer dependencies into the Explorer agent or the main harness
environment, and do not import harness code from here except through the
invocation below.

Spec: [`../SPEC.md`](../SPEC.md) (latest sections win). Change rules:
[`../AGENTS.md`](../AGENTS.md). Run evidence: [`../docs/`](../docs/).

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
uv run python -m glimmer_trainer prepare       # validate rendered data/token lengths, no remote session
uv run python -m glimmer_trainer train         # validate and report, exits without launching
```

`prepare` and plain `train` never open a remote session. Both default to
`../exports/sft_mix.jsonl`, `../exports/sft_dev.jsonl`, and the frozen prompt;
flags select other files (see `--help`). The run 2 `sft_mix.jsonl` is retired
(`../archive/exports/`, ignored), so pass the v2 export explicitly.

## Paid commands (explicit approval required)

```bash
uv run python -m glimmer_trainer train --launch --run-id NAME
uv run python -m glimmer_trainer sample --run <run-dir> --ckpt <ckpt> --cases <prompts.jsonl> --out <predictions.jsonl>
```

- `train --launch` opens a Fireworks training session (remote). SFT is free only
  for jobs started before 2026-12-01 Pacific; sampling is billed.
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

## Boundaries

- Muse Glimmer 30B (base or adapters) generates A2UI only.
- Hosted Model API models (`muse-spark-1.x`, `muse-wonder-*`) are evaluation
  only; their output never enters training data (ADR 0035).
- Spark Open may teach/write intents/judge (ADR 0034); an adapter trained on its
  output is a Muse Model License Derivative — record that in the run manifest.
- Reasoning strength is `low` in training rows and normal inference.
- Keep Family splits and fingerprint guards intact; do not loosen the export
  guards to make a run pass.
