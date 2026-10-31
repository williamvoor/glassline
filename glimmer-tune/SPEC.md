# glimmer-tune spec: milestone 2 (data)

Status: draft for milestone 2a, 2026-10-31. Decisions: [ADR 0003](../docs/adr/0003-answer-only-sft-targets.md), [ADR 0004](../docs/adr/0004-splits-by-gallery-family.md).

Milestone 2 turns the A2UI gallery into eval cases and SFT rows. It is staged so a free SFT smoke run can happen early (the Muse Glimmer 30B free SFT offer ends 2026-11-30):

- **2a (this spec):** all machinery, the split of all 43 seeds, and 6 family templates (4 train, 1 dev, 1 test).
- **2b:** templates for the remaining single-turn families. No new machinery.

## Terms

- **Seed:** one vendored gallery example (`seeds/gallery/<stem>.json`), unchanged from upstream.
- **Family:** one seed design. Its id is the seed stem (`09_login-form`). A family belongs to exactly one split.
- **Template:** `families/<stem>.yaml`. It holds an Express template and intent templates that share named slots, plus value lists for the slots.
- **Case:** one eval item, in the shape `score_response` already reads (see Case schema).
- **Fingerprint:** a hash of a reference's component tree that ignores ids, text and values (see Fingerprint).
- **System prompt:** the one prompt used for training and inference (see System prompt).

## Files

| Path | What |
|---|---|
| `seeds/gallery/*.json` | 43 files copied verbatim from `a2ui/catalogs/basic/v1/examples/` at commit 825beac |
| `seeds/README.md` | Provenance (commit, path) and the Apache-2.0 licence note |
| `data/splits.yaml` | One list of families per split (`train`, `dev`, `test`, `excluded`), covering all 43 seeds. Lists rather than a mapping, so that a family listed twice is caught (YAML silently keeps the last duplicate key). Committed and written by hand. |
| `families/*.yaml` | Templates (6 in 2a) |
| `a2ui_eval/seeds.py` | Loads seeds (`stem`, `name`, `description`, `messages`) |
| `a2ui_eval/prompt.py` | `build_system_prompt(sdk, reasoning="low") -> str` |
| `a2ui_eval/fingerprint.py` | `fingerprint(messages, index: RefIndex) -> str` (the catalog index finds component-reference props) |
| `a2ui_eval/generate.py` | Renders templates into cases and validates them |
| `a2ui_eval/export.py` | Turns train cases into SFT JSONL, with the overlap and length guards |
| `a2ui_eval/data_cli.py` | `python -m a2ui_eval.data_cli {generate,draft,build-eval,export}` |
| `data/train/cases.jsonl` | Generated train cases |
| `data/intents_draft.jsonl` | Dev/test candidates for you to edit (`reviewed: false`) |
| `data/dev/cases.jsonl`, `data/test/cases.jsonl` | Built from reviewed draft rows only |
| `exports/sft_train.jsonl` | Rows in the Fireworks chat format |
| `tests/test_seeds.py`, `test_prompt.py`, `test_fingerprint.py`, `test_generate.py`, `test_export.py`, `test_splits.py` | Tests |

New dependencies: `jinja2` and `tokenizers` (with `huggingface_hub` to fetch `tokenizer.json`). The cookbook (with torch) is **not** a dependency.

## System prompt

`build_system_prompt` returns `sdk.prompt_snippet + "\n\nReasoning strength: low."`.

- The SDK is built with **no examples**, because the gallery seeds are dev/test data.
- The result must be byte-identical across calls and processes. It has no dates or ids. The Muse renderer then adds neither its default system prompt nor `Reasoning strength: high`, because it detects "reasoning strength" in our system text (cookbook `renderer/muse_glimmer.py:424-444`).
- Milestone 3's runner must use this function. It is the single source of the prompt.

## Templates

```yaml
family: 09_login-form
slots:                     # each key: a list of values; a value may be a scalar, mapping or list
  title: ["Welcome back", "Sign in to Acme", ...]
  fields: [[{label: Email, path: email, kind: email}], ...]
intents:                   # Jinja2; the same slot values render the intent and the Express
  - "A login card titled \"{{ title }}\" with ..."
express: |                 # Jinja2 over canonical Express (from sdk.decompile of the seed)
  root = Card(main_column)
  ...
```

- The Express is rendered with Jinja2 `StrictUndefined`, so an unknown slot is an error.
- A variant comes from one seeded RNG draw per slot plus one choice of intent template. The seed is `(global_seed, family, index)`, so output is deterministic.
- In the template file, the template with the seed's own slot values must reproduce the seed. This is tested.

## Case schema

```json
{"id": "train/09_login-form/0007", "family": "09_login-form", "split": "train",
 "intent": "...", "expect_ui": true,
 "reference": [<compiled A2UI messages>], "reference_express": "<canonical Express>",
 "existing_surface": [], "fingerprint": "<hex>"}
```

`reference_express` is `sdk.decompile(reference)`, the canonical form. Gallery JSON only round-trips in this form, so the training targets use it.

## Fingerprint

The first 16 hex characters of a SHA-256 over a canonical string, built by walking the folded surface from `root` and emitting `Type(child-fingerprints...)`. Children keep their order. It ignores component ids, text, data values, bindings and surface ids. It includes component type, tree shape, and the names of props whose values are component references. Variants that differ only in text share a fingerprint, while adding a field changes it.

## Behaviour and criteria → tests

| # | Criterion | Test |
|---|---|---|
| C1 | Exactly 43 seeds load, each with `name`, `description` and non-empty `messages` | `test_seeds.py::test_all_seeds_load` |
| C2 | `splits.yaml` maps every seed stem exactly once to a split. Unknown stems, missing stems and a stem in two splits are errors. Single-turn counts are train 25–27, dev 7–9 and test 8–10. The multi-turn seeds `00_incremental` and `31_incremental-dashboard` are `excluded` in 2a. | `test_splits.py::test_partition_complete`, `::test_unknown_family_rejected`, `::test_family_in_two_splits_rejected`, `::test_family_without_split_rejected` |
| C3 | The system prompt is byte-identical across two SDK instances. It ends with `Reasoning strength: low.`, with exactly one "reasoning strength" in it. It doesn't contain today's date (the SDK rules have a fixed RFC 3339 example date, `2026-03-14`, which is allowed because it is static). It has no gallery example text (no seed `name` or seed surface id). | `test_prompt.py::test_stable`, `::test_reasoning_low`, `::test_no_dates`, `::test_no_gallery_examples` |
| C4 | Fingerprints match for text-only variants and differ when a child is added or a component type changes. They are unchanged by renaming ids. | `test_fingerprint.py::test_text_invariant`, `::test_id_invariant`, `::test_added_child_differs`, `::test_type_change_differs`, `::test_child_order_matters` |
| C5 | Each 2a template, rendered with its seed's values, gives a reference whose fingerprint equals the seed's and that scores `exact_match` against the seed | `test_generate.py::test_template_reproduces_seed[family]` |
| C6 | Generation is deterministic: the same seed gives the same cases, byte for byte | `test_generate.py::test_deterministic` |
| C7 | Every generated case compiles, passes `sdk_check`, and self-scores `exact_match`. A variant that fails stops generation with its family, index and error, and is never written. | `test_generate.py::test_cases_valid`, `::test_invalid_variant_raises` |
| C8 | Duplicate `(intent, reference_express)` pairs within a family are dropped. The requested count is met, or a shortfall error says how many unique variants were found within the draw budget (20 × count). That is a lower bound, not an enumeration. | `test_generate.py::test_dedup`, `::test_shortfall_reported` |
| C9 | `generate` writes only train-split families to `data/train/cases.jsonl`. `draft` writes only dev/test families to `intents_draft.jsonl`, with `reviewed: false`. `draft` refuses to overwrite an existing draft file without `--force`, because the file holds your edits. Every template's family has a train, dev or test split. | `test_generate.py::test_split_routing`, `::test_draft_does_not_overwrite_edits`, `::test_all_templates_have_a_split` |
| C10 | `build-eval` copies only `reviewed: true` draft rows, keeping your edited intent. It writes a split's file only when that split has reviewed rows, so a partial review never freezes an empty test split. It fails when no rows are reviewed, or the draft is missing. It refuses to overwrite an existing `data/test/cases.jsonl` (test is frozen). `--split dev` builds dev only, so an approved test split stays unfrozen. | `test_generate.py::test_build_eval_reviewed_only`, `::test_test_split_frozen`, `::test_build_eval_missing_draft_fails`, `::test_build_eval_partial_review_does_not_freeze_test`, `::test_build_eval_split_option_leaves_other_split_unwritten` |
| C11 | Each export row is `{"messages": [system, user, assistant]}`. System is the prompt from C3. User is the intent. Assistant is `"<a2ui>\n" + reference_express + "\n</a2ui>"`, a non-empty string with no `reasoning_content`. The last message is the assistant. | `test_export.py::test_row_shape`, `::test_no_reasoning` |
| C12 | Export reads only `data/train/cases.jsonl`. It **fails** if any train case's family is not `train` in `splits.yaml`. It **drops** (and reports per family) train cases whose fingerprint appears in dev/test cases or drafts, or whose intent text equals a dev/test intent. It also drops train cases whose fingerprint equals a dev/test **seed's** fingerprint, which covers dev/test families with no cases yet. | `test_export.py::test_rejects_non_train_family`, `::test_drops_fingerprint_overlap`, `::test_drops_intent_overlap`, `::test_drops_extra_fingerprints`, `::test_cli_export_guards_against_drafts_and_seeds` |
| C13 | Export counts tokens per row with an injected tokenizer: system + user + assistant contents plus 64 tokens of chat-template overhead. It fails, listing case ids, if any row exceeds `--max-seq-len` (default 8192). It reports max, p50 and p95 token counts. The overhead (`TEMPLATE_OVERHEAD == 64`) and the 8192 default are pinned. | `test_export.py::test_over_length_fails`, `::test_length_report`, `::test_overhead_and_default_pinned` |
| C14 | Assistant targets round-trip: `unwrap` and `compile` of the assistant content equals the case `reference` | `test_export.py::test_target_round_trips` |
| C15 | Existing milestone 1 tests still pass | full suite |
| C16 | Slot sharing is enforced. Every non-boolean scalar leaf of every drawn slot value appears in every rendered intent, except leaves on the per-family `MAY_OMIT` list (fields shown only when another slot says so). An unknown slot raises `UndefinedError`. | `test_generate.py::test_intents_mention_slot_values[family]`, `::test_undefined_slot_raises` |

Tests use a fake whitespace tokenizer and tiny in-test templates where possible. No test touches the network, Hugging Face or Fireworks.

## Decisions

- Training targets are answer-only, with `Reasoning strength: low.` in the system prompt (ADR 0003).
- Splits are fixed by seed family. Train/dev/test never share a family, and the fingerprint and intent guard drop near-duplicates (ADR 0004).
- The CLI uses the real tokenizer (`meta-models/Muse-Glimmer-30B` `tokenizer.json`, from the public, ungated HF repo). The +64 overhead is a conservative estimate, not the renderer. The real renderer check is the cookbook `muse_glimmer_starter prepare` in the smoke run, outside the tests.
- Export **fails** on over-length rows rather than dropping them, because `sft_loop` drops them silently.
- One user turn per row. Multi-turn families are excluded until a later milestone.
- Dev/test intents are drafted from templates, never by a model. You edit `intents_draft.jsonl` and set `reviewed: true`.
- I hand-pick the split assignment for a balanced mix of component types. It's yours to review in `data/splits.yaml`.
- Default counts: train 60 variants per family, dev 12, test 17. These are CLI flags.

## Deferred after review (2a)

- Isolated tests for each of `make_case`'s `sdk_check` and self-score branches. Today one invalid-variant test covers whichever check fires first.
- A test that the RNG seed includes the family name, and one that a prop name changes the fingerprint.
- `export --out` ignores `--data`.
- The tokenizer revision is not pinned. Counts are an estimate anyway.
- In 2a, 36_modal (test) has no train design using Modal, so its result measures Modal zero-shot. The 240 train rows cover only 4 designs. Both are fixed by 2b templates.

## Non-goals

- Templates for more than the 6 families of 2a.
- Multi-turn cases, `existing_surface` generation, and delete-surface cases.
- Running the cookbook, calling Fireworks, or any training. Milestone 3 and the smoke run cover those.
- Checking the semantic match between intent and UI beyond slot-sharing by construction.

## Test command

```bash
cd glimmer-tune && uv run pytest
```

---

# 2b step 0: switch to the v0.9 basic catalog

Status: approved by the user, 2026-10-31. Decision: [ADR 0008](../docs/adr/0008-train-generic-express-on-the-v0-9-basic-catalog.md).

From now on, the data pipeline, the system prompt and the review page use the A2UI v0.9 basic catalog. The 2a data is rebuilt on it, and your 29 approvals carry over.

## Files

| Path | Change |
|---|---|
| `catalogs/basic_v0_9.json` | New. `specification/v0_9_1/catalogs/basic/catalog.json` at 825beac (byte-identical to `v0_9`), byte for byte, with one line inserted after `catalogId`: `"protocolVersion": "0.9",`, which the SDK loader requires. `basic_v1.json` stays for the conversion tests. |
| `catalogs/README.md` | Provenance of both files |
| `a2ui_eval/convert.py` | New. `v1_to_v0_9(messages) -> messages`, a JSON-level conversion (see Seed conversion) |
| `a2ui_eval/seeds.py` | `Seed.messages` is v0.9. The raw v1 messages stay available as `Seed.v1_messages`. |
| `a2ui_eval/data_cli.py` | `CATALOG` → `basic_v0_9.json`. `draft --keep-reviews`. |
| `a2ui_eval/structure.py` | `binding_uses` reads `{"path": p}` (v0.9) as well as `{"@path": p}` (v1). It skips child-list templates (`componentId`), which `refs.py` handles. |
| `a2ui_eval/review.py`, `a2ui_eval/sdk.py` | The review page uses `catalogs/basic_v0_9.json`. `write_basic_v0_9_catalog` is removed, because its enum order differs from the spec file and so would its prompt. The value hints read `call` as well as `@call`. |
| `families/*.yaml` | The Express is re-canonicalised from the v0.9 decompile of the converted seed. Fixes include 09_login-form's `TextField` slots. |
| `tests/conftest.py` | The `sdk` and `index` fixtures use v0.9. New `sdk_v1` and `index_v1` fixtures. |
| `data/train/cases.jsonl`, `data/intents_draft.jsonl`, `data/dev/cases.jsonl`, `exports/sft_train.jsonl` | Rebuilt |

## Seed conversion

Seeds stay verbatim v1 on disk. `v1_to_v0_9` works on JSON only, never through Express, because Express positional slots differ between versions. v1 `TextField(label, value, placeholder, variant, ...)` and v0.9 `TextField(label, value, variant, validationRegexp, ...)` compile silently to different meanings. Per message it does this:
- `version` becomes `"v0.9"`, and a `catalogId` under `.../v1_0/...` becomes the v0.9 basic catalog ID.
- `{"@path": p}` becomes `{"path": p}`, and `{"@call": f, ...}` becomes `{"call": f, ...}`, at any depth.
- A `createSurface` that carries `components` and/or `dataModel` is split into `createSurface` (the other keys), `updateComponents` and `updateDataModel` (`path: "/"`), the shape the v0.9 compiler emits.
- A prop the v0.9 catalog lacks (`placeholder`, `posterUrl`, `steps`) raises. No seed uses one today.

## Criteria → tests

| # | Criterion | Test |
|---|---|---|
| V1 | `basic_v0_9.json` is the upstream spec file byte for byte, plus exactly the inserted `protocolVersion` line. `ExpressSDK` loads it. | `test_v0_9.py::test_vendored_catalog_matches_upstream` (skipped when `../a2ui` is absent), `::test_catalog_loads` |
| V2 | Every converted single-turn seed passes `sdk_check` under v0.9 (the excluded multi-turn seeds are exempt). Its fingerprint under the v0.9 index equals the v1 seed's fingerprint under the v1 index. | `test_v0_9.py::test_seeds_convert`, `::test_fingerprint_version_independent` |
| V3 | The conversion renames `@path` and `@call`, splits `createSurface`, sets the version and catalog ID, and raises on a v1-only prop | `test_v0_9.py::test_convert_shapes`, `::test_convert_rejects_v1_only_prop` |
| V4 | Each template, rendered with its seed's values, reproduces the **converted** seed: equal fingerprint and `exact_match`. This is the existing C5, now against v0.9, and it is the guard against positional slot shifts. | `test_generate.py::test_template_reproduces_seed[family]` |
| V5 | Generation under v0.9 rejects v1-only props, as a compile error, so they never reach a row | `test_v0_9.py::test_v1_only_props_rejected[placeholder,posterUrl,steps]` |
| V6 | The system prompt is built from the v0.9 catalog. C3 still holds, and the prompt names `validationRegexp` but not `placeholder`. | `test_prompt.py` (existing) and `::test_prompt_is_v0_9` |
| V7 | `binding_uses` finds `{"path": p}` and `{"@path": p}` and ignores `{"componentId", "path"}` | `test_refs_structure.py::test_binding_uses_v0_9` |
| V8 | `draft --keep-reviews` regenerates the drafts and carries `intent` and `reviewed` over by `id`. It fails, writing nothing, if a reviewed id is missing from the new drafts or its UI changed. "UI changed" means a different `reference_express` (text and values) or fingerprint (structure); the fingerprint alone ignores text. It reports dropped unreviewed ids. It refuses to run without an existing draft. All JSONL writes are atomic (temp file, then rename). | `test_generate.py::test_keep_reviews_carries_edits`, `::test_keep_reviews_fails_on_missing_reviewed_id`, `::test_keep_reviews_fails_on_changed_fingerprint`, `::test_keep_reviews_fails_on_changed_text_with_same_fingerprint`, `::test_keep_reviews_cli`, `::test_write_jsonl_is_atomic` |
| V9 | After the rebuild, the real data is v0.9: every row's `reference` compiles, has version `v0.9`, and uses the v0.9 catalog ID. The draft has 29 rows, all `reviewed: true`. Intents are identical to before (checked once against git at rebuild time, not by the suite). `data/dev/cases.jsonl` holds the 12 dev rows. | `test_v0_9.py::test_real_data_is_v0_9` |
| V10 | The review page renders the rebuilt draft, and its value hints work on compiled v0.9 messages (separate `updateComponents`/`updateDataModel`, `call`) | `test_review.py::test_real_draft_rows_compile_to_v0_9`, `::test_expected_values_on_compiled_v0_9`, `::test_expected_values_cover_data_text_format_strings_and_events[call,@call]` |
| V11 | All earlier tests still pass. Scorer tests with v1 literals use the `sdk_v1` fixture. | full suite |

## Decisions

- Vendor the spec file, not the SDK's `BasicCatalog("0.9.1").to_json()`. They agree except for list order, but that order reaches the prompt text.
- The prompt is the SDK's `prompt_snippet` for the v0.9 catalog, as-is. v0.9 has no catalog-guidelines block (v1 had about 39 lines), and upstream's v0.9 `rules.txt` is written for JSON, not Express, so it is not added.
- The strict unknown-prop check is the compiler plus the schema (`unevaluatedProperties: false`) under v0.9. No separate checker. Silent positional shifts are caught by V4, not by a checker.
- The scorer accepts both binding syntaxes. Milestone 1's scorer tests keep their v1 literals.
- gt-smoke-1 stays as it is. Its adapter was trained on the v1 prompt and is not a baseline.
- `31_incremental-dashboard` fails v0.9's reachability check on its whole multi-turn block. It is excluded anyway; V2 covers single-turn seeds only.

## Non-goals

- New 2b templates
- Building the test split
- Running a training job

## Test command

```bash
cd glimmer-tune && uv run pytest
```
