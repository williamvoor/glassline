# Tuning Muse Glimmer 30B to write A2UI Express: report to 2026-11-02

All paths are under `genUI/glimmer-tune/` unless stated. Every number comes from the file named next to it. Where sources disagree, the report says so.

## 1. Goal and constraints

**Goal.** Tune Muse Glimmer 30B with LoRA SFT so that it writes better generic A2UI Express against the A2UI v0.9 basic catalog (`../docs/adr/0008`; `docs/lora-tuning-report-20261101.md`). It is not tuned for the explorer's macro replies. The user made this correction on 2026-10-31 (memory `glimmer-tune-decisions.md`; `docs/summary-20261101-1.md`).

**Why Glimmer: the two-model split.** The explorer plans to use Muse Spark (`meta/muse-spark-1.3`) for the SQL tool loop and tuned Glimmer for the Express (memory `explorer-two-model-split.md`). Speed is the project's top requirement (`genUI/CLAUDE.md`). Glimmer's input would be a Brief from Spark plus Result schemas added by code (`../docs/adr/0014`). The output side (Macros) is agreed in principle but deferred until this first run, on generic Express, has been evaluated. ADR 0014 notes that the current training rows do not have the Brief-plus-schemas input shape, so the explorer's model will need a second run.

**Constraints.**
- Free SFT: Fireworks serverless LoRA SFT on Muse Glimmer 30B is free for jobs started before the deadline of **2026-11-30** (memory `fireworks-free-sft-deadline.md`). `docs/summary-20261031-2.md` gives the exact cut-off as 2026-12-01 08:00 UTC and `docs/summary-20261102-1.md` says "before 2026-11-30 Pacific". Sampling, RL and deployments are billed (`docs/summary-20261031-2.md`).
- Glimmer may be called only to generate A2UI. No other model is used for live calls, apart from `muse-spark-1.3` writing synthetic vocabulary (`genUI/CLAUDE.md`; `../docs/adr/0009`).
- Reasoning strength is `low` everywhere, and the training targets are answer-only (`../docs/adr/0003`).
- One A2UI SDK pin, git commit `825beac`, shared with the explorer (`../docs/adr/0006`).

## 2. Timeline by milestone

Commit trail is from `git log --oneline -- .` (oldest first, `b713c05` to `3ec2213`).

### Milestone 1: eval harness and scorer (2026-10-31, summary 1)

Built: `a2ui_eval/{sdk,refs,surfaces,structure,align,score}.py`. A response is checked for: compiles, valid per the SDK, tree structure (orphans, undefined refs, cycles, missing root), data bindings, slot F1 against the reference, and exact match.

Decisions:
- A2UI main pinned by git, because PyPI 0.9.0 lacks `RendererState` and the stateful `parse_response`.
- Reasoning `low` everywhere, with one dev-only `high` setup for the base model.
- Three-way split by family.
- Catalog v1.0 at that point.

Facts found: a stop sequence strips `</a2ui>`, so the scorer puts it back. The SDK does not check bindings. 42 of 43 gallery seeds self-score as exact matches; the exception is the multi-turn `31_incremental-dashboard`. 32 tests passed (`docs/summary-20261031-1.md`).

### Milestone 2a: gallery data pipeline (2026-10-31; commit `b713c05`)

Built: vendored the 43 gallery seeds (`seeds/gallery/`), Jinja slot templates for 6 families, `prompt.py`, `fingerprint.py`, `generate.py`, `export.py`, `data_cli.py`.

Decisions:
- ADR 0003: answer-only targets with an explicit `Reasoning strength: low.` system prompt.
- ADR 0004: splits by gallery family, with an exporter guard that fails on a non-train family and drops train rows whose fingerprint or intent appears in dev or test.
- `data/splits.yaml`: 26 train, 7 dev, 8 test families, 2 multi-turn excluded.

Data: 240 train cases, 29 dev/test drafts, 240 SFT rows of about 5.7k tokens (`docs/summary-20261031-2.md`). The refuter returned PASS-WITH-NOTES; four majors were fixed, 87 tests pass.

### Smoke run `gt-smoke-1` (2026-10-31)

232 train and 8 holdout rows, LoRA rank 8, learning rate 1e-4, 15 steps, 1 epoch, batch 16, max-seq-len 6144. Loss fell from 0.85 to 0.0002 and was near 0 by step 9. The model memorised the templates. Pre-clip grad norms of about 28k were clipped to 1.0 on every step. Holdout SDK-valid outputs went from 5/8 (base) to 8/8, and sampled tokens fell from 8,400 to 1,941 (`docs/summary-20261031-3.md`; `docs/lora-tuning-report-20261101.md`).

The report's reading is that exact match and F1 on this holdout measure memorised conventions. The likely real gains are valid output and about 4x fewer sampled tokens. The adapter was trained on the v1 prompt, so it is not comparable with later runs (ADR 0008).

### Review page, draft approval, dev split (2026-11-01, summary 1)

A local review page renders each draft with `@a2ui/lit` (`a2ui_eval/review.py`, `review/`). All 29 drafts were approved. `build-eval --split` built a 12-row dev split from `27_stats-card`; test (36_modal) waited. Commits `c2bab82`, `907a260`, `77b8d9b` (`docs/summary-20261101-1.md`).

### Gallery to v0.9 conversion (ADR 0008; commit `db29065`)

- ADR 0005 (2026-10-31) first fixed v0.9 on the wire, with Express treated as version-neutral.
- ADR 0008 (2026-11-01) corrected that: Express positional slots follow each catalog's prop order. v1 `TextField` slot 4 is `variant`; in v0.9 it is `validationRegexp`. The same Express compiles under both and means different things.
- Fix: `a2ui_eval/convert.py` converts seeds from v1 to v0.9 at the JSON level, never through Express. The scorer, align, fingerprint and refs tests run under both catalogs. All 29 approvals were kept.
- Result: 233 tests pass (`docs/summary-20261101-1.md`, `-2.md`).

### 2b: templates for every single-turn family (commit `65f7a60`)

35 new templates, 41 single-turn families in all. Train: 1560 rows. Draft: 220 rows (the 29 approvals byte-identical). The export dropped 0 rows. 339 tests pass (`docs/summary-20261101-2.md`).

### Synthetic generator, 2c-1 (ADR 0009, accepted 2026-11-01)

Why: the 26 train designs were memorised by step 9. ADR 0009 adds deterministic, catalog-driven synthetic surfaces. The LLM writes only vocabulary.

| Part | What and key decisions | Source |
|---|---|---|
| Catalog model | `CatalogModel` reads slots, kinds, enums and function roles from the SDK's `build_catalog_helpers` | `summary-20261101-3.md` |
| Vocabulary | 10 domains, 8 Topics each, written by `muse-spark-1.3` (3 calls per domain), validated and committed (commit `8a1e2e3`). User approved at checkpoint (a) | `summary-20261101-3.md`, `-4.md` |
| Eval-literal rule | 3+-word dev/test literals are blocked inside longer strings. 2-word literals are blocked only if they contain a digit. Generic labels are allowed | `summary-20261101-3.md` |
| Skeletons | 22 train skeleton files plus banned archetypes in `data/splits.yaml`. No skeleton may mirror a test design. Test stays gallery-only (commit `c3bbacc`) | `summary-20261101-4.md`; ADR 0009 |
| House style | Synthetic targets use a fixed look copied from gallery habits. Specific intents state only departures (ADR 0011) | `SPEC.md` "A2" |
| Linter (S8) | 10 rules (`button_action`, `form_submit`, `input_bound`, `modal`, `tabs`, `icon_name`, `heading_length`, `body_words`, `list_source`, `lorem`). Every single-turn gallery seed passes after a 4-seed `form_submit` allowlist (commit `8157f04`) | `docs/spec-build-b.md` |
| Review checkpoints | (a) vocabulary approved 2026-11-01. (b) 10 samples approved 10 of 10 on 2026-11-02, with word-level coherence limits accepted for run 1 | `SPEC.md` "Checkpoint (b), re-issued" |

Bugs fixed with a failing test first, among others: list records never written to the data model, a pluralise bug ("1 itinerarie", 29 rows), and repeated media files on one surface (176 rows) (`SPEC.md`). The refute rounds are in the summaries; all blockers and majors were fixed.

The size tail is a quota of about 15% of rows with 25-40 components (`SPEC.md`).

### 2d: intents in the user's words, test freeze (commit `c868bfa`)

A report-only Sonnet review of 191 unreviewed drafts approved only 36 and found a real bug. `merge_reviews` had copied stale intents onto regenerated references, so 45 rows described a different UI from their answer. It was fixed, with a regression test first. The user decided that Sonnet's approvals count as theirs and that intents are written as a real user asks, with no component, prop, path, event or icon names (rules R1-R6 in `SPEC.md`). All 41 templates were rewritten.

Results: train 1560 rows, dev 84 rows, test 136 rows (frozen), 524 tests (`docs/summary-20261101-5.md`). Caveat recorded there: no person has read the 220 eval rows. Known weak spots are developer-like wording, raw numbers and ISO timestamps.

Synthetic intents were restyled to the same register in commit `ad8b638`, and checkpoint (b) fixes landed in `fa37624`.

### Export mix and trainer (ADR 0015; commit `3ec2213`)

- Mix: `export --mix 0.7` with 3639 synthetic and all 1560 gallery rows once gives **5199 rows**. One synthetic train row was dropped for matching a synthetic dev fingerprint. Tokens: max 6882, p50 5388, p95 5958 from the export, and max 6843, p50 5349, p95 5919 from the trainer's `prepare` (`SPEC.md`).
- Dev prompts: `export-dev` writes 284 rows (84 gallery, 200 synthetic). A fixed subset of 64 rows is 32 gallery and 32 synthetic, seed 0.
- Prompt frozen in `data/system_prompt.txt`. Export refuses if `build_system_prompt` differs.
- Scoring: `a2ui_eval/checkpoints.py`. `selection` is the macro mean of gallery and synthetic mean row slot F1. A scorer bug (a failed request scored F1 1.0) was fixed first.
- Trainer: separate uv project `trainer/` (the Fireworks SDK needs `huggingface-hub<2`, the A2UI pin needs 2.4+). It saves a checkpoint every 25 steps, samples the subset in the background, picks the top 2 by subset score, scores them and base on full dev, promotes the full-dev winner and deletes the session.
- Tests: 623 main and 22 trainer (`docs/summary-20261102-1.md`).
- Choices by the user: dev subset on every checkpoint, full dev for base and the best 2, a separate trainer project, per-token mean loss. Serverless was chosen over Managed SFT; both are $0 under the promo.

## 3. SFT run 1 results

### Configuration (`runs/sft1/manifest.json`)

| Setting | Value |
|---|---|
| Base model | `accounts/fireworks/models/muse-glimmer-30b` |
| LoRA rank, alpha | 16, 32 |
| Learning rate, batch, epochs | 5e-5, 16, 1 |
| Steps, checkpoints | 325, 14 (`s000` to `s325`, every 25) |
| Rows trained | 5199 (`metrics.jsonl`, sum of `rows`) |
| Max sequence length, sample max tokens | 8192, 3072 |
| Sample errors | 0 at every checkpoint, including the three full-dev files |
| Winner | `s250`, promoted as `accounts/williamvoor/models/glimmer-a2ui-sft1-s250`; session deleted |

### Training loss (`runs/sft1/metrics.jsonl`, 325 lines)

Step 1 loss 0.542; step 325 loss 0.0149; minimum 0.0096. Step times sum to 2,776 s (about 46 minutes), which excludes sampling. Wall time is not recorded. `grad_norm_post_clip` was about 1.0 every step (`docs/summary-20261102-2.md`).

### Subset selection per checkpoint (64 rows; `manifest.json` `subset_scores`)

| Ckpt | Selection | Ckpt | Selection | Ckpt | Selection |
|---|---|---|---|---|---|
| s000 | 0.2335 | s100 | 0.6522 | s200 | 0.6726 |
| s025 | 0.5449 | s125 | 0.6496 | s225 | 0.6871 |
| s050 | 0.6318 | s150 | 0.6584 | s250 | 0.6936 |
| s075 | 0.6436 | s175 | 0.6815 | s275 | 0.6743 |
| | | | | s300 | 0.6363 |
| | | | | s325 | 0.6788 |

The two finalists other than base were `s250` and `s225` (`manifest.json` `finalists`).

### Full dev (284 rows; `manifest.json` `full_scores`, `scores/*.full.json`)

| Ckpt | Selection | Gallery slot F1 (84 rows) | Synthetic slot F1 (200 rows) | Gallery compiles | Synthetic compiles | Synthetic exact match |
|---|---|---|---|---|---|---|
| s000 (base) | 0.2396 | 0.1984 | 0.2808 | 0.738 | 0.535 | 0.000 |
| s225 | 0.6474 | 0.3588 | 0.9360 | 0.786 | 0.975 | 0.580 |
| s250 | 0.6788 | 0.4211 | 0.9365 | 0.833 | 0.975 | 0.560 |

Gallery exact match is 0.0 for all three. Base `s000.full` truncated 1.2% of gallery rows (`scores/s000.full.json`).

`selection_ci` (95% stratified bootstrap) is not in the scores files. The figures below are quoted from `docs/summary-20261102-2.md` only, and I could not check them against a file: base 0.213-0.267, `s225` 0.619-0.677, `s250` 0.652-0.705. The `s225` and `s250` intervals overlap.

### Per source on the 64-row subset (`scores/s000.json`, `scores/s250.json`)

| Ckpt | Source | Rows | Compiles | Exact match | Slot F1 |
|---|---|---|---|---|---|
| s000 | gallery | 32 | 0.6875 | 0.0 | 0.1953 |
| s000 | synthetic | 32 | 0.5000 | 0.0 | 0.2717 |
| s250 | gallery | 32 | 0.9375 | 0.0 | 0.4455 |
| s250 | synthetic | 32 | 0.96875 | 0.5625 | 0.9418 |

### Interpretation

- **Base to tuned is a large gain.** Full-dev selection went from 0.240 to 0.679 (`manifest.json`). The gain is mostly on synthetic rows, where slot F1 went from 0.28 to 0.94.
- **Gallery is the hard half.** Gallery slot F1 for `s250` is 0.42 on full dev, with 0 exact matches. Gallery dev rows come from designs the model never trained on, so this is the closer proxy for real generalisation. ADR 0009 says a synthetic dev score says nothing about real-world UIs; only test does. The headline 0.679 averages a high synthetic score with a low gallery score, so read the two parts separately.
- **Plateau.** The subset score rose from 0.23 at base to 0.68 by `s175` (0.6815) and then stayed between 0.636 and 0.694 to the end, while train loss kept falling to about 0.015. It did not fall, so there is no sign of the score degrading with memorisation, but more steps are not buying more. `docs/summary-20261102-2.md` puts the ceiling at about 0.68 and expects gains from data (more skeletons) and a path- and event-aware scorer.
- **Subset noise.** The 64-row subset put `s250` and `s225` 0.0065 apart (0.6936 against 0.6871), while full dev put them 0.031 apart (0.6788 against 0.6474). `s300` dipped to 0.636 and `s325` recovered to 0.679. Differences of a few hundredths between neighbouring checkpoints on the subset are within noise. Picking `s250` over `s225` is supported by full dev, but their bootstrap intervals overlap (see the caveat above).
- **Synthetic exact match** is 0.56 on full dev for `s250`. Literal paths and events in the scorer probably limit this (section 4).

## 4. Known gaps and open questions

**lm_head / unembed.** The trainer requests `train_unembed=True` (`trainer/glimmer_trainer/loop.py:208`), and `SPEC.md` says "MLP/attention/unembed on". A read-only API call on 2026-11-02 confirmed that the promoted model's `targetModules` is `[down_proj, gate_proj, k_proj, o_proj, q_proj, up_proj, v_proj]`, with no `lm_head`. The model metadata (`PeftDetails`) has no field for the unembed flag, and the run manifest does not record it, so whether the unembed was trained and kept at promotion is unknown. A session-less `weights_info` lookup on the `s250` sampler path returned `train_unembed=None` (also `train_mlp` and `train_attn` None), so the training side doesn't record it either. The risk: the scores came from the sampler checkpoint, but the promoted adapter may lack lm_head, so the served model may differ from the scored one. Only Fireworks support or the adapter files can settle it. The cheaper fix is to train run 2 with unembed off, so the scored and served models match by construction.

**Scorer limits.**
- It compares data paths and event names literally. The intent rules (R1-R3) mean the user never names paths or events, so a correct answer with different names loses F1.
- The path- and event-blind score is milestone 3 work (`SPEC.md` "Decisions and non-goals"). Selection, and the base-versus-tuned comparison, use the literal slot F1 on both sides, so the comparison stays fair but understates quality.

**Not built or deferred.**
- 2c-2: update turns and text-only rows, and scoring of `expect_ui: false` rows. A missing response on such a row still has 0 reference slots, so it would need revisiting (`SPEC.md` amendments). No dev rows have `expect_ui: false` yet.
- 2c-3: optional LLM rewording of intents.
- Milestone 3: our own base-versus-tuned runner on dev and test, including the path- and event-blind score.
- `service.close()` is not called in the trainer. Deferred by the refuter round (`docs/summary-20261102-1.md`, `-2.md`).
- No deployment of the adapter. Serving is a later, paid decision (`SPEC.md`).
- Test split: frozen at 136 rows and not yet used for any score (`SPEC.md`: "The test split stays untouched").

**Billing.** Fireworks billing showed $0. The prediction files show the eval sampling really ran. The likely reason is that the promo covered sampling, but this is unverified (`docs/summary-20261102-2.md`). The earlier estimate for eval sampling was US$9-22, also unverified (`docs/summary-20261102-1.md`).

**Data limits.**
- Synthetic coherence is word-level: any topic action can be the submit button, and media is labelled with the topic name (`SPEC.md`). Accepted for run 1.
- The 220 eval rows have not been read by a person in full (`docs/summary-20261101-5.md`).
- ADR 0014 means a second run is needed for the explorer's Brief-plus-schemas input and Macro output.

**Conflicts in the sources.**
- ADR 0003 says dataset text must not be written by a model. ADR 0009 has `muse-spark-1.3` write the synthetic vocabulary. ADR 0009 does not mention the exception.
- Longest 2a row: 5,418 tokens in `docs/lora-tuning-report-20261101.md`, 5,814 in `docs/summary-20261031-2.md`.
- Max tokens of the run 1 mix: 6882 (export) against 6843 (`prepare`), both in `SPEC.md`. They come from different counters or steps, and the spec does not say which.
- ADR 0004 gives about 26/8/9 families. `data/splits.yaml` gives 26/7/8 with 2 excluded (`docs/summary-20261031-2.md`).
- Free SFT cut-off: 2026-11-30 (memory) against 2026-12-01 08:00 UTC (`docs/summary-20261031-2.md`).
- Memory `glimmer-tune-decisions.md` still describes the 2a catalog and explorer-prompt plan (ADR 0005). ADR 0008 supersedes it.
- `selection_ci` is described in `SPEC.md` as written to the scores files, but the files in `runs/sft1/scores/` have no such field. They were evidently scored before the feature was added.

## 5. Test-split tooling and checkpoint probe (2026-11-02)

**Decisions (user, 2026-11-02).**
- The test split is spent once, on the final candidate, scored together with base (`s000`). Looking at test now and then tuning run 2 on what we see would stop test being held out.
- The interval is family-level, because 136 rows from 8 families are not 136 independent rows.

**Built** (spec: `SPEC.md` "Test evaluation"; uncommitted at the time of writing):
- `data_cli export-test` writes `exports/sft_test.jsonl`: 136 rows, 8 families.
- `checkpoints score --split test` scores against test references, needs every row and reports a family-level bootstrap `selection_ci`.
- `checkpoints compare a.json b.json` gives a paired, family-level interval for the difference.
- `glimmer_trainer sample` samples one saved checkpoint of a finished run, by its manifest path. It never overwrites output.
- Refuter review: a first FAIL on two blockers. The row-mean statistic was untested, and a partial export could bypass the every-row guard. Both were fixed with regression tests that catch the mutants. The re-review was a PASS: 637 main and 30 trainer tests, rerun by the refuter.

**Probe.** One dev row (`dev/03_calendar-day/0005`), sampled from `s250`'s sampler path after the training session was deleted:
- It returned a clean answer with no errors, so **saved sampler checkpoints survive session deletion**, and the test evaluation needs no deployment.
- The text differed from run 1's stored answer in one component id only (`actions_row` → `actions`). Greedy sampling on the shared pool is not bit-exact. Both versions score slot F1 0.765, because the scorer ignores component ids.
- What this means: small checkpoint-to-checkpoint differences include some sampling noise, as well as the subset noise in section 3.

**Not done:** the test evaluation itself. It waits for the final candidate.

## 6. Name-blind rescore and gallery failure reading (2026-11-02)

**Score.** Name-blind slot F1 (ADR 0021; `SPEC.md` "Name-blind slot score") rewrites data paths, event names and event context keys to placeholders before comparing. It is reported next to the literal score, and selection is unchanged.
- Refuter: PASS-WITH-NOTES. The notes were fixed with tests that kill the refuter's mutants, and 656 tests pass.
- The literal scores of all 1748 run 1 predictions are identical to before.

**Rescore of run 1** (`runs/sft1/rescore-20261102/`, git-ignored; the originals in `scores/` are untouched):

| Ckpt | Literal selection | Name-blind selection | Gallery literal → blind | Synthetic literal → blind |
|---|---|---|---|---|
| s000.full (base) | 0.240 | 0.273 | 0.198 → 0.232 | 0.281 → 0.313 |
| s225.full | 0.647 | 0.703 | 0.359 → 0.469 | 0.936 → 0.937 |
| s250.full | 0.679 | 0.726 | 0.421 → 0.514 | 0.937 → 0.937 |

- Names cost the gallery about 0.09 to 0.11 and the synthetic rows almost nothing. Synthetic dev uses the house-style names the model learnt.
- The checkpoint ranking holds: `s250` stays best on full dev. On the subset, `s250` (0.7456) and `s175` (0.7453) tie.

**Why gallery is still 0.51.** A sub-agent read the 20 worst gallery rows of `s250.full`.
- **14 of the 84 gallery rows don't compile and score 0**, against 5 of 200 synthetic rows. In 13 of the 14, the model invents syntax Express doesn't have:
  - ternaries in shipping-status, 7 rows (`Icon($completed ? "check" : "arrowForward")`);
  - comparison operators inside `if(...)` or `formatString` in financial-data-grid, 6 rows.
- The 70 gallery rows that compile average 0.62.
- Of the worst 20: 16 are model errors, 3 have under-specified references and 1 is a scorer artefact (a hard-coded list against a data-driven template).
- Per-family blind F1 runs from 0.25 (shipping-status) and 0.26 (financial-data-grid) up to 0.70 (calendar-day). Seven families make for a noisy gallery number.

**What this means for run 2.**
- The cheapest real gain is a tuned model that never writes non-Express syntax. If the 14 rows compiled and scored like the rest, gallery would rise by about 0.10.
- Two kinds of data are missing:
  - training rows that show how Express does per-item variation (for example, data-driven values bound from the data model instead of inline conditionals);
  - more data-bound lists.
- An inference-time compile check with one retry would also help the explorer, whatever the model.
- Not yet checked: which conditional idioms the v0.9 catalog's functions support.
