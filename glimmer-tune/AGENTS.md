# AGENTS.md

Guidance for coding agents working in `glimmer-tune/`. Read `SPEC.md` first. It is a dated log of
milestones and runs, so the latest sections win. The glossary is `CONTEXT.md` (Seed, Family,
Template, Skeleton, Block, Case, Fingerprint, …), and the decisions are `../docs/adr/`
(0003–0005, 0008, 0009, 0011, 0014, 0015, 0021, 0022). Run results and reports are in `docs/`.

## What this is

The eval harness and SFT data factory for teaching Muse Glimmer 30B to write **generic A2UI
Express** against the v0.9 basic catalog, with no explorer macros (ADR 0008). Gallery Seeds become
Family Templates and Cases. Catalog-driven Skeletons become synthetic surfaces. Both are exported
as Fireworks chat-format SFT rows, and a separate trainer runs the SFT loop on Fireworks.

| Path | Role |
|---|---|
| `a2ui_eval/` | Main package: scorer, prompt, fingerprint, generate, export, review |
| `a2ui_eval/data_cli.py` | `python -m a2ui_eval.data_cli {generate,draft,build-eval,export,freeze-prompt,export-dev,export-test,vocab,synth,coverage,lint}` |
| `a2ui_eval/checkpoints.py` | Scores and compares checkpoint predictions |
| `a2ui_eval/review.py` + `review/` | Local review page (Vite + Lit UI, built to `review/dist`) |
| `a2ui_eval/synth/` | Synthetic surfaces: skeletons, vocab, intents, house style, linter |
| `seeds/gallery/` | 43 upstream gallery examples. **Verbatim, never edit** (`seeds/README.md`) |
| `catalogs/` | Vendored v0.9 and v1 basic catalogs, pinned to the SDK commit (`catalogs/README.md`) |
| `families/*.yaml` | One hand-written Template per gallery Family |
| `data/splits.yaml` | Family → split (train/dev/test/excluded). Written by hand |
| `data/system_prompt.txt` | The **frozen** system prompt (ADR 0015) |
| `data/dev`, `data/test`, `data/synth/` | Eval cases and synthetic rows, plus review verdicts |
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

- **Models.** Muse Glimmer 30B (base or our adapters) only generates A2UI. `meta/muse-spark-1.3` is
  the only other model (`data_cli vocab`). No Gemini, no Wonder, no other baselines.
- **Reasoning strength is `low` everywhere:** every eval setup, model and training row. The chat
  template defaults to `high`, so a row without it mismatches inference. The one exception is a
  dev-only base-model setup at `high`.
- **Tests never call a model or the network.** Stub the model. Fireworks is touched only by
  `glimmer_trainer train --launch` and `sample`.
- **Don't spend money unasked.** Don't run `train --launch`, `sample`, `vocab` or any eval sampling
  unless the user asks. Eval sampling is billed. SFT is free only for jobs started before
  2026-12-01 Pacific. State the cost before a paid step.
- **The test split is frozen.** Nothing reads `data/test` except the one-time final evaluation
  (`export-test`). The exporter and trainer read only train and dev.
- **No leakage across splits.** Splits are by Family (gallery) or Skeleton (synthetic), ADR 0004.
  Export refuses train rows whose Family or Fingerprint appears in dev or test. Keep those guards.
- **The prompt is frozen.** `export` refuses when `build_system_prompt` differs from
  `data/system_prompt.txt`. A change invalidates trained adapters, so make it only as a decided step
  for a new run, then run `freeze-prompt`. Upstream SDK text stays byte-identical (don't rename its
  rules).
- **One A2UI SDK pin** (git commit `825beac`) shared with the explorer (ADR 0006). The seeds and
  catalogs come from the same commit. Moving it means re-copying them and re-running the data build,
  because canonical references can change.
- **Catalog-agnostic code.** Catalog facts come from the catalog JSON, not hard-coded component
  names or prop rules.
- **Dataset text is ours, not a model's,** except where a spec says otherwise (vocab, 2c-3
  rewording). Intents for dev and test are hand-reviewed (`reviewed: true`).
- **Secrets.** `FIREWORKS_API_KEY` and `META_API_KEY` live in the repo root's `.env`. Code reads env
  vars only. Never print or log them.
- **Don't modify `../a2ui/`** or depend on paths inside it at runtime. `../explorer/` belongs to its
  own session. Don't change it from here.

## Changing data or the scorer

Bugs found in data get a regression test first (see `tests/test_synth_plausibility.py`). Coverage
targets and the linter allowlist (`data/synth_lint_allowlist.yaml`) are part of the spec, so don't
loosen them to make a run pass. When a change would alter existing references or scores, say so and
rescore rather than silently mixing old and new numbers.

## Commits

Conventional Commits per the repo root's `../.gitmessage`, scope `glimmer-tune`
(e.g. `feat(glimmer-tune): …`).
