# AGENTS.md

Guidance for coding agents working in `glimmer-tune/`. Read `SPEC.md` first. It is a dated log of
milestones and runs, so the latest sections win. The glossary is `CONTEXT.md` (Seed, Family,
Case, Fingerprint, …), and the decisions are `../docs/adr/`
(0003–0005, 0008, 0014, 0015, 0021, 0022, 0033–0035; 0009, 0011 and 0025 are superseded). Run results and reports are in `docs/`.

## What this is

The eval harness and SFT data factory for teaching Muse Glimmer 30B to write **generic A2UI
Express** against the v0.9 basic catalog, with no explorer macros (ADR 0008). Cases are exported
as Fireworks chat-format SFT rows, and a separate trainer runs the SFT loop on Fireworks.

**Approach v2 (2026-11-04):** distill and filter (`docs/distill_restart_prompt.md`, SPEC
"Approach v2"). The template and synthetic generators of runs 1 and 2 are in `archive/`. It's
read-only and not importable; don't revive code from it without a decision. Tag
`pre-distill-v1` is the last commit before the move.

| Path | Role |
|---|---|
| `a2ui_eval/` | Main package: scorer, prompt, fingerprint, export, review, lint, grammar |
| `a2ui_eval/data_cli.py` | `python -m a2ui_eval.data_cli {export,freeze-prompt,export-dev,export-test,grammar,lint}` |
| `a2ui_eval/checkpoints.py` | Scores and compares checkpoint predictions |
| `a2ui_eval/review.py` + `review/` | Local review page (Vite + Lit UI, built to `review/dist`) |
| `seeds/gallery/` | 43 upstream gallery examples. **Verbatim, never edit** (`seeds/README.md`) |
| `catalogs/` | Vendored v0.9 and v1 basic catalogs, pinned to the SDK commit (`catalogs/README.md`) |
| `data/splits.yaml` | Family → split (train/dev/test/excluded). Written by hand |
| `data/system_prompt.txt` | The **frozen** system prompt (ADR 0015) |
| `data/dev`, `data/test` | Frozen gallery eval cases; the source of truth (their generator is archived) |
| `data/synth/dev` | Synthetic regression guard only; never training data |
| `data/fewshot3.jsonl` | The frozen examples of base + 3, the explorer launch default |
| `archive/` | Retired generators, data and tests (`docs/cleanup-plan.md`) |
| `trainer/` | Separate `uv` project, `python -m glimmer_trainer {prepare,train,sample}` (ADR 0015) |
| `exports/`, `runs/` | Git-ignored. Rebuilt by `export` / written by the trainer |

## Commands

```bash
uv sync
uv run pytest                          # no network, no model
cd trainer && uv sync && uv run pytest # the trainer's own env and tests
uv run --env-file ../.env python -m a2ui_eval.data_cli <cmd>   # commands that call a model need the keys
```

## Hard rules

- **Models.**
  - Muse Glimmer 30B (base or our adapters) generates A2UI, including as a best-of-n teacher.
    It judges only if Phase 3 picks it as the judge (ADR 0034).
  - Muse Spark Open via Baseten may write targets, write or vary intents, and judge (ADR 0034).
    Any adapter whose training data holds its output is a Muse Model License Derivative: record
    that in the run manifest, because shipping it means showing "Built with Muse Spark Open".
  - Hosted Model API models (`muse-spark-1.x`, `muse-wonder-*`) are for evaluation only. Their
    output never enters training data in any role (ADR 0035).
  - No Gemini, and no other models.
- **Reasoning strength is `low` everywhere:** every eval setup, model and training row. The chat
  template defaults to `high`, so a row without it mismatches inference. The exceptions are a
  dev-only base-model setup at `high` and the Phase 1 teacher measurement (Spark Open at `low`
  and `medium`). Training rows are always `low`.
- **Tests never call a model or the network.** Stub the model. Fireworks is touched only by
  `glimmer_trainer train --launch` and `sample`.
- **Don't spend money unasked.** Don't run `train --launch`, `sample`, `teacher baseline` or any eval sampling
  unless the user asks. Eval sampling is billed. SFT is free only for jobs started before
  2026-12-01 Pacific. State the cost before a paid step.
- **The test split is frozen.** Nothing reads `data/test` except the one-time final evaluation
  (`export-test`). The exporter and trainer read only train and dev.
- **No leakage across splits.** Splits are by Family, ADR 0004.
  Export refuses train rows whose Family or Fingerprint appears in dev or test. Keep those guards.
- **The prompt is frozen.** `export` refuses when `build_system_prompt` differs from
  `data/system_prompt.txt`. A change invalidates trained adapters, so make it only as a decided step
  for a new run, then run `freeze-prompt`. Upstream SDK text stays byte-identical (don't rename its
  rules).
- **One A2UI SDK pin** (git commit `825beac`) shared with the explorer (ADR 0006). The seeds and
  catalogs come from the same commit. Moving it means re-copying them and re-checking the frozen
  references, because canonical references can change.
- **Catalog-agnostic code.** Catalog facts come from the catalog JSON, not hard-coded component
  names or prop rules.
- **Every text field records who wrote it** (ADR 0033): a person, a script or a model. Model-written
  text comes only from the models allowed above. Intents for dev and test are hand-reviewed
  (`reviewed: true`).
- **Secrets.** `FIREWORKS_API_KEY`, `META_API_KEY` and `BASETEN_API_KEY` live in the repo root's `.env`. Code reads env
  vars only. Never print or log them.
- **Don't modify `../a2ui/`** or depend on paths inside it at runtime. `../explorer/` belongs to its
  own session. Don't change it from here.

## Changing data or the scorer

Bugs found in data get a regression test first (for example `archive/tests/test_synth_plausibility.py`). The
linter allowlist (`data/synth_lint_allowlist.yaml`) is part of the spec, so don't
loosen them to make a run pass. When a change would alter existing references or scores, say so and
rescore rather than silently mixing old and new numbers.

## Commits

Conventional Commits per the repo root's `../.gitmessage`, scope `glimmer-tune`
(e.g. `feat(glimmer-tune): …`).
