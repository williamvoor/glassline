# Cleanup plan: distill-and-filter restart (Phase 0)

**Status:** approved and carried out, 2026-11-04 (see "Outcome" at the end).
**Recovery point:** tag `pre-distill-v1` = `48e061e` (local only; the repo has no remote).
**Brief:** `docs/distill_restart_prompt.md`.

Verdicts: **KEEP** (as is), **ADAPT** (keep, change), **RETIRE** (move to `archive/`, out of
imports and tests), **HISTORY** (read-only, never rebuilt), **DELETE** (dead).
`archive/` mirrors the original paths (`archive/a2ui_eval/synth/…`, `archive/families/…`) and is
outside the `a2ui_eval` package, so nothing can import it. There is no CI here; "out of tests"
means the tests move to `archive/tests/` and `uv run pytest` no longer collects them.

## Where I disagree with the brief's defaults

1. **The gallery dev and test cases are template output.** All 84 dev and 136 test rows were
   generated from `families/*.yaml` by `generate.py`, and their intents came through
   `data/intents_draft.jsonl` and `build-eval`. Retiring the templates is fine because the case
   files are frozen, but after the cleanup dev and test **can't be rebuilt** from the working
   tree, only from the archive or the tag. I treat `data/dev/cases.jsonl` and
   `data/test/cases.jsonl` as the source of truth from now on, and say so in SPEC.
2. **`generate.py` isn't only the generator.** `read_jsonl`, `write_jsonl`, `wrap` and
   `load_splits` are imported by 10 modules and many tests. ADAPT: move those four into a new
   `a2ui_eval/io.py`, and archive the rest (Jinja `Template`, `generate_family`, `load_templates`).
3. **The launch default depends on the gallery train rows.** `fewshot_examples` reads its 3
   examples from `data/train/cases.jsonl`, which the templates generated. Base + 3 + grammar is
   the explorer launch default, so those three must stay byte-identical. ADAPT: freeze them into
   `data/fewshot3.jsonl` (with a test that they match the run 2 `sft_dev_fewshot3.jsonl` rows),
   point `fewshot_examples` at it, and archive `data/train/cases.jsonl`.
4. **The S8 linter, `CatalogModel` and `structhash` live inside `synth/`.** `catalog_check.py`
   (the outside-catalog check, KEEP) imports `synth.catalog_model`. ADAPT: move
   `catalog_model.py`, `lint.py`, `structhash.py` and `coverage.py` up to `a2ui_eval/`.
   - `lint.py` becomes validator layer 3. It uses only one skeleton tag, `form`, in
     `form_submit`. Without a skeleton the rule falls back to "two or more inputs", which is fine.
   - Its `MATCHERS`/`banned_matches` refuse synthetic rows shaped like dev or test families. I
     want to keep them as a leakage guard for distilled rows too.
   - `structhash.py` is the near-duplicate check for validator layer 5.
   - `coverage.py` is catalog-driven and data-agnostic. It will report which catalog parts the
     distilled set covers. Its `targets` stay as-is, unused by v2 until a spec sets new ones.
5. **`llm_call.py` (in `synth/`) is used by `teacher.py`.** Move it to `a2ui_eval/llm_call.py`.
6. **The synthetic dev set contains `muse-spark-1.3` vocabulary.** It stays an eval set (the brief
   allows Model API output for evaluation). The same vocabulary is in the run 1/2 training rows,
   so s225 and s250 were trained on it. That needs a decision before s225 is called a candidate
   (see "Decision needed").

## a2ui_eval/ (package)

| Path | Verdict | Reason |
|---|---|---|
| `sdk.py`, `refs.py`, `surfaces.py`, `structure.py`, `align.py`, `score.py` | KEEP | Scorer and name-blind slot F1. |
| `catalog_check.py` | KEEP | Outside-catalog check; validator layer 1. Import path changes (point 4). |
| `convert.py`, `seeds.py` | KEEP | v1 → v0.9 conversion of the gallery seeds. |
| `fingerprint.py` | KEEP | Leakage guards; Phase 2 fingerprint check. |
| `grammar.py` | KEEP | Grammar decoding for the launch default and teachers. |
| `prompt.py` | KEEP | Student prompt; frozen `data/system_prompt.txt` unchanged. |
| `checkpoints.py` | ADAPT | Selection = gallery dev F1 (full 84 rows or a ≥ 42-row gallery subset); synthetic dev reported as a regression guard, not in `selection`. `export-dev` drops the 64-row mixed subset. `fewshot_examples` reads `data/fewshot3.jsonl`. |
| `export.py` | ADAPT | Keep `sft_row`, guards, token checks. Input = distilled dataset. `mix_cases` and the skeleton-split routing go. |
| `generate.py` | ADAPT → split | I/O helpers to `io.py`; template generator to archive (point 2). |
| `review.py` | ADAPT | Keep the draft, prediction and synthetic-row modes. `_lint_context` stops loading skeletons; it will review distilled candidates later. |
| `teacher.py` | ADAPT | Becomes the Phase 1 Spark Open runner (low/medium effort, n samples). The ADR 0025 docstring ("never an SFT target") is updated by the new ADR. |
| `data_cli.py` | ADAPT | Keep `export`, `freeze-prompt`, `export-dev`, `export-test`, `grammar`, `lint` (gallery lint). Remove `generate`, `draft`, `build-eval`, `vocab`, `synth`, `coverage` (`coverage` returns later as a report on any cases file). |
| `synth/catalog_model.py`, `synth/lint.py`, `synth/structhash.py`, `synth/coverage.py`, `synth/llm_call.py` | ADAPT → move | Up to `a2ui_eval/` (points 4, 5). |
| `synth/skeleton.py`, `synth/skeletons/*.yaml` (22) | RETIRE | Skeleton generator and files. |
| `synth/grow.py`, `synth/fill.py`, `synth/pipeline.py`, `synth/datamodel.py` | RETIRE | Skeleton → surface generator. |
| `synth/intents.py`, `synth/phrases.py`, `synth/style.py` | RETIRE | Templated intents and house style (ADR 0011). |
| `synth/vocab_writer.py`, `synth/vocab_schema.py`, `synth/vocab/*.yaml` (10) | RETIRE | `muse-spark-1.3` vocabulary (ADR 0009); barred from training by Model API clause (ix). |

## Data and config

| Path | Verdict | Reason |
|---|---|---|
| `seeds/gallery/` (43), `seeds/README.md` | KEEP | Verbatim upstream seeds. |
| `catalogs/` | KEEP | Pinned v0.9 and v1 catalogs. |
| `data/dev/cases.jsonl` (84) | KEEP, frozen | Primary metric. Source of truth from now on (point 1). |
| `data/test/cases.jsonl` (136) | KEEP, frozen | Unopened until the final candidate. |
| `data/splits.yaml` | KEEP | Family → split; the Family guards use it. |
| `data/system_prompt.txt` | KEEP | Run 2 student prompt. |
| `data/synth_lint_allowlist.yaml` | KEEP | Gallery allowlist for the S8 rules. |
| `data/synth/dev/cases.jsonl` (200) | KEEP, relabelled | Synthetic regression guard; README says so. |
| `data/train/cases.jsonl` (1560) | RETIRE | Template-generated gallery train rows; the 3 few-shot rows are frozen first (point 3). |
| `data/synth/train/cases.jsonl` (3640), `data/synth/coverage.json`, `data/synth/README.md`, `data/synth/review.jsonl` | RETIRE | Run 2 synthetic train set, its coverage report and its review verdicts. |
| `data/intents_draft.jsonl` (220) | HISTORY | Provenance of the reviewed dev and test intents. `review.py` draft mode still opens it. |
| `families/*.yaml` (41) | RETIRE | Jinja slot Templates (point 1). |

## Git-ignored outputs (moved within the ignored tree, not committed)

| Path | Verdict | Reason |
|---|---|---|
| `exports/sft_train.jsonl`, `exports/sft_mix.jsonl` | RETIRE → `archive/exports/` (ignored) | Run 1/2 training exports. ~160 MB together with the others, so they stay out of git. |
| `exports/sft_dev.jsonl`, `sft_dev_fewshot3.jsonl`, `sft_test.jsonl`, `express_chat.gbnf` | KEEP | Eval prompts and the grammar; rebuildable. |
| `runs/sft1/`, `runs/sft2/` | HISTORY | The baselines to beat. Rescore only with the current scorer. |
| `runs/baselines-run2/`, `latency-run2/`, `grammar-run2/`, `compare-run2/`, `launch-run2-measure.sh` | HISTORY | Evidence behind the run 2 memo. |
| `runs/teacher-spark-open/` | HISTORY | The 2026-11-03 Spark Open baseline (gallery 0.290); Phase 1 compares with it. |
| `runs/gt-smoke-1/` | HISTORY | The first trainer smoke run. Small; keep. |

## Tests

| Path | Verdict |
|---|---|
| `test_align`, `test_score`, `test_refs_structure`, `test_fingerprint`, `test_seeds`, `test_v0_9`, `test_prompt`, `test_catalog_check`, `test_grammar`, `test_teacher`, `test_review`, `test_review_predictions` | KEEP (imports updated) |
| `test_checkpoints`, `test_export`, `test_splits` | ADAPT. The selection and few-shot tests change. Export tests move to distilled-style fixtures. `test_splits` checks the frozen dev/test files, not regeneration. |
| `test_synth_lint`, `test_synth_catalog` | ADAPT → rename to `test_lint`, `test_catalog_model`. |
| `test_synth_coverage` | ADAPT. Keep the `components_of`/report tests and drop the synth-pipeline cases. |
| `test_review_synthetic` | ADAPT. Keep the review-mode tests on a fixture file; drop the skeleton tags. |
| `test_generate`, `test_export_mix`, `test_synth_skeletons`, `test_synth_pipeline`, `test_synth_intents`, `test_synth_phrases`, `test_synth_plausibility`, `test_synth_style`, `test_synth_vocab`, `test_synth_splits` | RETIRE → `archive/tests/`. Before archiving, check each for tests that run over the frozen dev/test files rather than the generator, and move those to `test_splits`. |
| `conftest.py` | ADAPT. Prune `SLOW_TESTS` names that leave; `SPREAD_FILES` loses `test_generate.py`. |
| `trainer/tests/*` | KEEP |

## Docs, trainer and top-level files

| Path | Verdict | Reason |
|---|---|---|
| `trainer/` | KEEP | `train_unembed=False` stays the default; `prepare` reads the new export. |
| `docs/*` (reports, memos, summaries, guides) | KEEP | History; every number traces to them. |
| `SPEC.md` | ADAPT | New "Approach v2" section; earlier sections stay as the dated log. |
| `README.md` | ADAPT | Architecture diagram and commands for v2. |
| `AGENTS.md` | ADAPT | The hard rules change (Spark Open as teacher and judge, the reasoning-effort rule for teachers, Glimmer as judge, no Model API output in training) once the ADRs are accepted. |
| `CONTEXT.md` | ADAPT | Mark Template, Skeleton and Block as retired; add Candidate, Validator layer, Teacher and Judge. |
| `pyproject.toml` | ADAPT | Drop `jinja2` (only `generate.py` uses it). |
| ADRs 0009, 0011, 0025 (and parts of 0003/0004/0021) | ADAPT | Mark "Superseded by ADR NNNN"; none deleted. |

## DELETE

None found. Every retired module has a caller only among the other retired modules, so they're
archived, not deleted. The only scratch files are in the session scratchpad, outside the repo.

## Decisions (2026-11-04)

- **s225 and s250 are clean.** They may be candidates and test-split comparisons.
- **The archive is committed** (`archive/`) instead of relying only on the tag.

## Outcome

Carried out as planned, with one change:
- **`coverage.py` was archived, not moved.** It isn't data-agnostic: `report` reads the
  generator's `patterns` field, and `SynthConfig` holds house-style rules (`always_set`). Its test
  went with it. `structhash.py` now has its own two-line `components_of` and a new
  `tests/test_structhash.py`, because its old tests were in the archived pipeline tests.

Also done:
- **Frozen fixtures.** Before archiving, the export tests' fixtures were frozen from the template
  generator (`tests/fixtures/export_{train,dev}.jsonl`). The few-shot rows were frozen into
  `data/fewshot3.jsonl` and checked against the run 2 `sft_dev_fewshot3.jsonl` export.
- **Fewer tests.** 389 harness tests pass (before: 824, of which 435 were in retired files),
  and 40 trainer tests.
- **Review verdicts.** The default verdicts file moved to `data/review.jsonl`. The run 2 verdicts
  are in `archive/data/synth/review.jsonl`.
