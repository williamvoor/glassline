# Spec: 2c-1 build B (linter, mixed export, token check, synthetic review)

Source: `SPEC.md` "# 2c", sections Linter, Criteria S8, S12, S13, "Decisions (build A)". This file only adds what build B needs. It does not edit `SPEC.md`.

## Files

| Path | Change |
|---|---|
| `a2ui_eval/synth/lint.py` | Adds the S8 rules and `lint_surface`, `lint_rows`, `lint_gallery`, `load_gallery_allowlist`. The banned-archetype matchers are untouched. |
| `data/synth_lint_allowlist.yaml` | Committed allowlist: `seed stem -> {rule id: reason}` for gallery seeds that fail a rule by upstream design. |
| `a2ui_eval/export.py` | `export_sft` gains `skeleton_splits`, `share` and `seed`. Synthetic cases are split-guarded by skeleton. Mixing, per-source token stats and the token-length check. |
| `a2ui_eval/data_cli.py` | `lint` (with `--gallery` and `--synthetic <cases.jsonl>`), `export --mix [share]` (bare `--mix` is 0.7), `--seed`, `--approx-tokens`. Synthetic dev rows are added to the export's eval guards. |
| `a2ui_eval/review.py` | `--synthetic <cases.jsonl> --sample N --seed S`: deterministic sample, verdict API, `data/synth/review.jsonl`. |
| `review/src/main.ts` | Verdict UI (approve, reject with reason) shown when the server is in synthetic mode. |
| `tests/test_synth_lint.py`, `tests/test_export_mix.py`, `tests/test_review_synthetic.py` | New tests. Existing `test_export.py` and `test_review.py` are not edited. |

## Behaviour

### Linter (S8)

`lint_surface(messages, model, tags=()) -> list[str]` runs on the folded v0.9 surface and returns the sorted unique ids of the rules that fail. `model` is a `CatalogModel` (it supplies the Icon enum). `tags` are the skeleton's tags.

| Rule id | Fails when |
|---|---|
| `button_action` | a Button has no `action` |
| `form_submit` | the surface is a form (tag `form`, or 2+ inputs) and no single Button has an Event action whose `context` references the path of every input (a context path also covers the paths under it) |
| `input_bound` | an input's `value` (TextField, DateTimeInput, ChoicePicker, CheckBox, Slider) is not a `{path}` binding |
| `modal` | a Modal's `trigger` is not an existing Button, or its `content` is missing or empty (an empty Text, or a container with no children) |
| `tabs` | a Tabs has fewer than 2 tabs, or a tab's child is missing or empty |
| `icon_name` | a literal Icon name is not in the catalog enum |
| `heading_length` | a heading (Text variant h1-h5, or text starting with `#`) has a literal longer than 60 characters, after stripping the `#` marks; a Markdown text is judged by its first line |
| `body_words` | body text has fewer than 3 words (body text: a body-variant literal Text in a Column or Card that also holds a heading; see Decisions) |
| `list_source` | a List with a static `children` list has none, or a template `path` (absolute) does not resolve to a non-empty list in the folded data model |
| `lorem` | any string in the components or data model contains "lorem" or "ipsum" (word match, case-insensitive) |

`lint_rows(rows, model, skeleton_tags)` lints synthetic rows (tags looked up by `row["skeleton"]`) and returns `{row id: [rule ids]}` for failing rows. `lint_gallery(seeds, model, allowlist)` lints every single-turn converted seed and returns `{stem: [rule ids]}` after removing allowlisted `(stem, rule)` pairs. A seed is single-turn when its messages contain exactly one `createSurface` and no `deleteSurface`. `data_cli lint --gallery` prints failures and exits 1 if any. `data_cli lint --synthetic <cases.jsonl>` does the same for synthetic rows. An allowlist entry that no longer fires is an error (stale entries hide nothing).

Pipeline hook: see "Pipeline hook" below. This build does not edit the pipeline.

### Mixed export (S12)

`data_cli export --mix 0.7` reads `data/train/cases.jsonl` (gallery) and `data/synth/train/cases.jsonl` (synthetic). Order of work:
1. Guards run on every train case first. Split guard: a gallery case's family must be in the train split; a synthetic case's `skeleton` must be in `train` in `skeleton_splits` (a case missing from it is rejected as not train). Fingerprint and intent guards run against every eval case: gallery draft, gallery dev, gallery test, synthetic dev, and the dev/test seed fingerprints. A synthetic train row whose fingerprint equals a synthetic dev row's is dropped and counted under reason `fingerprint`.
2. Mixing: let S be the surviving synthetic count and `T = round(S / share)`. The gallery supplies `T - S` rows: the surviving gallery rows, sorted by id and shuffled with `random.Random(seed)`, then repeated cyclically (oversampled) or truncated to that count. The result is the synthetic rows followed by those gallery rows, then shuffled deterministically with the same seed. `share` must be in `(0, 1]`; at 1 no gallery rows are added. With no synthetic rows, or no gallery rows when one is needed, it is an `ExportError`.
3. Output is byte-identical for the same inputs and seed.
4. Without `--mix`, export is unchanged (gallery rows only, as before).

The report gains `mix`: `{share, synthetic, gallery_rows, gallery_unique}` and per-source token stats.

### Token lengths

Rows are measured with the tokenizer the repo already uses (`tokenizers` with `meta-models/Muse-Glimmer-30B`, via `load_tokenizer_counter`) plus `TEMPLATE_OVERHEAD`. `--approx-tokens` instead uses `ceil(len(text) / 3)` for offline runs; this is an approximation and over-counts typical Express, so it errs toward flagging. Export raises `ExportError` listing the rows over `--max-seq-len` (existing behaviour). The report adds, per source (`gallery`, `synthetic`): `max`, `p50`, `p95`, and `near_limit` (rows above 90% of `max_seq_len`), so the size tail (about 15% of rows with 25-40 components) is visible before training.

### Synthetic review (S13)

`python -m a2ui_eval.review --synthetic data/synth/train/cases.jsonl --sample 50 --seed S [--verdicts data/synth/review.jsonl]` serves a deterministic sample (`random.Random(seed)` over rows sorted by id, sampled without replacement, then in id order). Each row is rendered as v0.9 from `reference_express`, with the intent, skeleton, domain, detail and the linter result. The API records `{id, verdict, reason}` (verdict `approve` or `reject`) into the verdicts file, one record per id (the latest wins, order kept). A `reject` needs a non-empty reason; an unknown id or verdict is refused and the file is left alone. The page shows the verdict controls only in this mode. The existing draft review mode is unchanged.

## Pipeline hook

When the concurrent generator session is ready, add this one line after `validate(...)` succeeds in the attempt loop, with `sk` the drawn skeleton:

```python
if fails := lint_surface(messages, env.model, sk.tags): raise Reject("lint", ",".join(fails))
```

with `from .lint import lint_surface`.

## Criteria → tests

| # | Criterion | Test |
|---|---|---|
| B1 | Each S8 rule fires on a hand-made bad surface, and the clean surface passes | `test_synth_lint.py::test_rule_fires[rule]`, `::test_clean_surface_passes` |
| B2 | Rule boundaries: heading 60 vs 61 characters, body 2 vs 3 words, button label and caption are not body text, a bound text is not checked | `test_synth_lint.py::test_heading_boundary`, `::test_body_words_scope` |
| B3 | A form with the submit context covering every input passes; one input missing fails; tag `form` alone requires a submit | `test_synth_lint.py::test_form_submit_cases` |
| B4 | 100% of single-turn gallery seeds pass after the allowlist; the allowlist has a reason for each entry and no stale entry | `test_synth_lint.py::test_gallery_passes`, `::test_allowlist_entries_have_reasons_and_fire` |
| B5 | Synthetic rows pass the linter (generated with the current pipeline; failing rules are reported, not hidden) | `test_synth_lint.py::test_synthetic_passes` |
| B6 | `lint_rows` maps row ids to rules and uses the skeleton's tags | `test_synth_lint.py::test_lint_rows_uses_skeleton_tags` |
| B7 | Mix ratio is within 1 row of the target, gallery rows are repeated when scarce and truncated when plentiful | `test_export_mix.py::test_mix_ratio`, `::test_gallery_oversampled`, `::test_gallery_truncated` |
| B8 | Mix is deterministic for a seed and differs for another | `test_export_mix.py::test_mix_deterministic` |
| B9 | A synthetic row from a non-train skeleton is refused | `test_export_mix.py::test_mix_rejects_dev_skeleton` |
| B10 | A synthetic train row with a synthetic dev row's fingerprint is dropped, counted as `fingerprint` | `test_export_mix.py::test_synthetic_dev_fingerprint_dropped` |
| B11 | Share outside (0, 1], or no synthetic rows, is an `ExportError`; share 1 adds no gallery rows | `test_export_mix.py::test_mix_bad_share`, `::test_mix_share_one` |
| B12 | Per-source token stats and `near_limit` are reported; an over-length synthetic row fails the export | `test_export_mix.py::test_token_stats_per_source`, `::test_over_length_synthetic_fails` |
| B13 | `--approx-tokens` counter is `ceil(len/3)`; CLI mix end to end with the approximate counter writes the file | `test_export_mix.py::test_approx_counter`, `::test_cli_export_mix` |
| B14 | The existing export tests still pass unchanged | `test_export.py` |
| B15 | Synthetic review serves a deterministic sample of `--sample` rows rendered as v0.9 | `test_review_synthetic.py::test_synthetic_sample` |
| B16 | A verdict is recorded as `{id, verdict, reason}`; reject needs a reason; bad ids and verdicts are refused and leave the file alone; the latest verdict per id wins | `test_review_synthetic.py::test_synthetic_verdict_requires_reason`, `::test_verdict_recorded`, `::test_verdict_rejects_bad_input`, `::test_verdict_upsert` |
| B17 | HTTP round trip for the verdict endpoint, and the draft mode API still works | `test_review_synthetic.py::test_http_verdict_round_trip` |

## Decisions

- ASSUMED: "body text" is a literal Text with variant `body` or none, not a heading, that sits in a Column or Card which also holds a heading Text (a paragraph under a title). Bound and `formatString` text, captions, Button labels, and table cells and record values (no heading beside them) are not body text. The first, wider reading (every body-variant literal) flagged 31% of synthetic rows, all of them short labels and static record cells such as "Item" or "Bananas", so it was dropped.
- ASSUMED: `form_submit` needs one Button whose Event context covers all input paths, not a union across buttons. A context path that is a parent of an input path (e.g. `/formData` for `/formData/email`) covers it. This is the strict reading; the allowlist covers gallery exceptions (4 seeds).
- ASSUMED: input paths inside list templates are relative and are skipped by `form_submit` (they cannot be named in an absolute context).
- ASSUMED: `list_source` resolves absolute template paths only. Relative template paths (nested templates) are not checked.
- ASSUMED: a "heading" also includes Text whose literal starts with `#`, since the gallery uses Markdown headings.
- ASSUMED: single-turn seeds are those with exactly one `createSurface` and no `deleteSurface`.
- ASSUMED: a stale allowlist entry fails the test. Otherwise the allowlist rots silently.
- ASSUMED: oversampling cycles through a seeded shuffle of the gallery rows rather than weighting by family; the SPEC says only "oversamples gallery rows by repeating them".
- ASSUMED: mix ratio counts rows after the guards drop any, so `share` is the share of what is actually written.
- ASSUMED: the output rows are shuffled after mixing so a training epoch is not blocked by source.
- Tokenizer: the repo's existing `tokenizers` counter. `--approx-tokens` (`ceil(len/3)`) is a documented offline approximation.
- The synthetic row format is unchanged. The linter is not wired into the pipeline here (the generator is being rewritten elsewhere); the hook line is above.
- The review page keeps one app for both modes. The server tells the page which mode it is in by adding `synthetic: true` to each row view.

## Non-goals

- Editing the generator, skeletons, vocabulary, `SPEC.md` or `CONTEXT.md`.
- Lint rules beyond the SPEC list; update-turn or text-only rows (2c-2).
- A real tokenizer download in tests (tests use a stub counter).

## Test command

```bash
cd glimmer-tune && uv run pytest -q
```
