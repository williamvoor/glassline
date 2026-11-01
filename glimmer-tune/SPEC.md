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

---

# 2b: templates for the remaining single-turn families

Status: draft, 2026-11-01. No new machinery and no new ADR: this applies the 2a template rules (C5, C7, C8, C16) and step 0's v0.9 catalog to every family.

## Files

| Path | Change |
|---|---|
| `families/*.yaml` | 35 new templates, one per single-turn family without one (22 train, 6 dev, 7 test). With the 6 from 2a, that's all 41 non-excluded seeds. |
| `tests/test_generate.py` | `MAY_OMIT` grows only for leaves that are shown conditionally. New `test_every_family_has_a_template`. `test_keep_reviews_cli` finds its dev row by id, not by position, because a test family (00_complex-layout) now sorts first. |
| `tests/test_v0_9.py` | `test_real_data_is_v0_9`: 220 draft rows with 29 reviewed, 1560 train rows, and dev equal to the reviewed dev drafts |
| `data/train/cases.jsonl`, `data/intents_draft.jsonl`, `exports/sft_train.jsonl` | Regenerated. The draft uses `draft --keep-reviews`, so the 29 approvals carry over. |
| `data/dev/cases.jsonl` | Not rebuilt until you review the new dev rows |

## Template rules

- The Express is the canonical v0.9 decompile of the converted seed, with slots substituted. The surface id stays the seed's, as in 2a.
- Each template has at least two intent templates, one detailed and one terse. Every intent names the literal text (including constant labels, markdown prefixes such as `## `, and date formats), the data values, the event names, and the icon names. A heading prefix is written as the literal `## `, never as "(h2)", because "h2" could mean the Text variant instead.
- Data paths are named as `/path` only where the user would refer to them: input values (TextField, CheckBox, ChoicePicker, DateTimeInput, Slider), list sources (`_template`), event context, and values bound by data (for example an icon name read from `/icon`). Display-only paths such as `/holderName` are left to the model. That matches the approved 2a rows (05, 23, 27). The scorer compares data paths literally, so milestone 3 reports a data score that ignores paths alongside the literal one.
- Related values (a person and their contact details, a sport and its stats, a prompt and its icon) are one slot value, so a variant never mixes them.
- Slots vary text, data and, where the seed allows it cheaply, structure (an optional child, a list length). Enum props vary only within the catalog enum (for example `Icon.name`).
- There must be enough unique variants for the default counts: train 60, dev 12, test 17.

## Criteria → tests

| # | Criterion | Test |
|---|---|---|
| B1 | Every family whose split is train, dev or test has a template | `test_generate.py::test_every_family_has_a_template` |
| B2 | Each new template reproduces its converted seed (equal fingerprint and `exact_match`) | `test_generate.py::test_template_reproduces_seed[family]` (C5/V4) |
| B3 | Each new template's variants compile, pass `sdk_check` and self-score `exact_match` | `test_generate.py::test_cases_valid[family]` (C7) |
| B4 | Each intent names every non-boolean scalar slot leaf, except listed `MAY_OMIT` leaves | `test_generate.py::test_intents_mention_slot_values[family]` (C16) |
| B5 | `generate` (60 per train family) and `draft --keep-reviews` (dev 12, test 17) run without a shortfall. The 29 approved rows keep their ids, intents and `reviewed: true`. | Run once at rebuild time. `test_v0_9.py::test_real_data_is_v0_9` is updated to the new draft count, with 29 still reviewed. |
| B6 | `export` succeeds. Drops per family are reported and checked by hand. | Run once at rebuild time |
| B7 | All earlier tests pass | full suite |

## Decisions

- Test is still not built. Building it freezes test, and its new rows need your review first. Modal is now taught in train (29_movie-card), which removes 2a's blocker.
- Dev is rebuilt only after you review the new dev drafts in the review page.
- Gallery surface ids (`gallery-*`) stay constant per family, as in 2a. 2c's synthetic rows vary surface ids.
- 2b varies text and data, not structure: each new family has one fingerprint. Design diversity is 2c's job.
- Deferred after review: some train families still pair loosely related values (26 show and episode, 28 event and time left, 17 event and location, 19 seats and total). They're coherent enough for training and not eval data. Dev/test families share subtrees with train families (for example a row of three value-over-caption stats). The fingerprint guard can't see that, so read dev/test as partial generalisation, not a clean design hold-out.

## Non-goals

- The synthetic generator (2c), multi-turn families, and any training run

## Test command

```bash
cd glimmer-tune && uv run pytest
```

---

# 2c: catalog-driven synthetic surfaces

Status: 2c-1 approved 2026-11-01, then amended the same day so that the vocabulary is written by an LLM (see Vocabulary, S16–S19). 2c-2 and 2c-3 are not approved. Decision: [ADR 0009](../docs/adr/0009-synthetic-surfaces-from-the-catalog-with-skeletons.md). Research facts are from the 2026-11-01 SDK research (SDK = `.venv/lib/python3.13/site-packages/a2ui/`).

Staged so that you see coverage and samples before anything is generated at full size, and so that a real SFT run fits before 2026-11-30:

- **2c-1:** single-turn new surfaces. Catalog model, LLM-written vocabulary, skeletons, growth, values, data model, validation and round trip, deterministic intents, dedup, coverage report, linter, review mode, mixed export. **Checkpoints:** (a) you review the vocabulary diff before the generator uses it; (b) the coverage report and 10 rendered samples go to you before full-size generation. Checkpoint (a) passed on 2026-11-01: you approved the 10 committed vocabulary files as they are.
- **2c-2:** update turns (data-only, components without root, `update=true`, `deleteSurface`) and text-only rows, with their export row shapes.
- **2c-3:** optional intent rewording with `meta/muse-spark-1.3`, plus the drop filter.

The rest of this section specifies 2c-1 in full. 2c-2 and 2c-3 are outlined at the end and get their own criteria before they're built.

## Facts this rests on

- `build_catalog_helpers([sdk.catalog])` gives per-component slot order, required props, enums and checkability. It's the same table the Express compiler uses, so positional order comes from it, never from our own JSON walk (`schema_helper.py:122-250`).
- Function return types come from the catalog JSON. A boolean return is a check, a string return is a value function, and void is an action. `length` and `numeric` need `min` or `max`, through an `anyOf` the helper doesn't carry.
- `Tabs.tabs` and `ChoicePicker.options` must be fully literal: the compiler rejects any `$path` inside them, though the schema allows it.
- The traps in Slider: `value` is required but `min` comes before `max`, so the form is `Slider(_, min, max, $/v)`. `weight` follows the specific props, and `checks` is always last.
- Decompile drops `theme` and `accessibility`, sorts data keys alphabetically, and writes data lines before components. The generator never emits `theme`, `accessibility` or `null` data values. (A probe on 2026-11-01 showed that a `null` data value does round-trip. The generator still avoids it, because a null field is never a plausible value.)
- `DateTimeInput.min`/`max` validate only when bound. The schema puts each literal through a `oneOf` of `format: date/time/date-time`, and formats aren't enforced, so every literal string matches all three and fails. The generator therefore always binds `min`/`max` (probe, 2026-11-01).
- `sdk_check` catches dangling refs, orphans, cycles, bad enums, missing required props and type errors. It doesn't catch a binding with no data value. Our `structure.unresolved_bindings` does.
- The scorer compares data paths and event names literally. Component ids are aligned by position, so ids are free.

## Files

| Path | What |
|---|---|
| `a2ui_eval/synth/catalog_model.py` | `CatalogModel(sdk)`: components with ordered slots (name, required, kind: `text`/`dynamic`/`static-enum`/`static-literal`/`child`/`children`/`action`/`checks`/`map-list`, enum), and functions with arguments and role (`check`/`value`/`action`). Built only from `build_catalog_helpers` plus the catalog JSON. |
| `a2ui_eval/synth/skeletons/*.yaml` | About 20 archetypes. Each has an id, a domain list, a coarse tree whose nodes are fixed components or open slots with constraints (allowed component kinds, depth and width budget), and plausibility tags (form, list, detail, dialog). |
| `a2ui_eval/synth/vocab/*.yaml` | Domain vocabularies (travel, banking, fitness, retail, health, events, education, home, work, media), written by `meta/muse-spark-1.3` and committed (see Vocabulary) |
| `a2ui_eval/synth/vocab_schema.py` | The vocabulary shape and `validate_vocab(doc, catalog_model, eval_texts) -> list[error]` |
| `a2ui_eval/synth/vocab_writer.py` | `write_vocab(domain, model, eval_texts, complete=<llm_call.py subprocess>)`: prompts the model, parses the YAML, validates, retries with the errors, and writes the file only if it's valid |
| `a2ui_eval/synth/llm_call.py` | Standalone single LiteLLM call (JSON on stdin and stdout). `vocab_writer` runs it with `uv run --no-project --with litellm==1.106.6 --with mcp --with python-dotenv`, because LiteLLM 1.106.6 needs `huggingface-hub<2` and glimmer-tune needs `>=2.4`. It uses the same pin and `mcp` workaround as the explorer, and glimmer-tune's own deps and lock are unchanged. |
| `a2ui_eval/synth/skeleton.py` | Loads and validates skeleton files against the `CatalogModel` (build A) |
| `a2ui_eval/synth/grow.py` | Fills open slots with components that fit, within budget, steered by coverage weights |
| `a2ui_eval/synth/fill.py` | Values for every chosen prop: literals that match the type and enum, or bindings (`binding_ratio`). Optional-slot patterns, checks, actions, `formatString`. |
| `a2ui_eval/synth/datamodel.py` | Builds a data model that gives every bound path a plausible value, with nested maps and lists and template items. Paths come from labels, deterministically ("Card holder" → `/cardHolder`). Input paths may start empty, as gallery forms do. |
| `a2ui_eval/synth/pipeline.py` | One surface: skeleton → grow → fill → data → v0.9 JSON → `sdk_check` and binding check → round trip → intent → dedup. `synthesize(n, seed, split, config) -> rows`. |
| `a2ui_eval/synth/intents.py` | Deterministic intent drafts at three detail levels (terse, medium, specific), from the tree. Each states what the scorer needs (see Intents). |
| `a2ui_eval/synth/lint.py` | Plausibility linter (see Linter) |
| `a2ui_eval/synth/coverage.py` | Counts, coverage weights, and the report (JSON plus a Markdown table) |
| `a2ui_eval/synth/structhash.py` | Dedup hash: the fingerprint plus enum props, optional-slot pattern, action kind and check kinds |
| `a2ui_eval/data_cli.py` | `vocab --domain D` (or `--all`), `vocab --check`, `synth --split {train,dev} --count N --seed S`, `coverage`, `lint --gallery`. `export --mix 0.7` adds synthetic train rows to gallery rows, oversampling the gallery. |
| `a2ui_eval/export.py` | Accepts synthetic cases. The split guard checks the skeleton's split. The fingerprint and intent guards also run against dev/test seeds and gallery drafts. |
| `a2ui_eval/review.py`, `review/src/main.ts` | `--synthetic <cases.jsonl> --sample 50 --seed S`: renders a sample, and records approve/reject with a reason in `data/synth/review.jsonl` |
| `data/splits.yaml` | New `skeletons:` (train/dev/held_out lists) and `banned_archetypes:` (test family → description, plus a matcher name in `lint.py`) |
| `data/synth/{train,dev}/cases.jsonl`, `data/synth/coverage.json`, `data/synth/README.md` | Generated output, and the per-pattern coverage table |
| `tests/test_synth_*.py` | Tests (see Criteria) |

Case shape: as in Case schema, plus `source: "synthetic"`, `skeleton`, `domain`, `detail` (`terse`/`medium`/`specific`), `patterns` (the coverage keys the row hits) and `generator_seed`. `family` is `synth/<skeleton>`, so existing code keeps working.

## Behaviour

- **Size:** depth 2–5 and 5–40 components, both drawn per row from configurable ranges.
- **Coverage-weighted sampling:** each choice (component, optional-slot pattern, binding kind, check, action kind) is weighted by how far its count is from the target N (default 30, configurable). Weights are recomputed per row, deterministically.
- **Coverage keys** (what "every pattern" means):
  - each component, and each (component, property)
  - **optional-slot patterns** per component. A pattern is the set of optional slots that are set, ignoring `weight` and `checks`. Cover every pattern, or the first 16 in a fixed order when there are more. Count trailing-omission and middle-`_` shapes separately.
  - bindings: absolute, relative inside `_template`, nested map path, list index (`/a/0`), `formatString` interpolation, and value functions (`formatNumber`, `formatCurrency`, `formatDate`, `pluralize`)
  - lists: `_template` with relative bindings, and static child arrays, in Row, Column and List
  - checks: `required`, `email`, `regex` with a message, `length` and `numeric` with integer bounds (one-sided and both), several checks on one field, and `and`/`or`/`not` on a Button
  - actions: `Event` with and without context, context referencing form field paths, and `openUrl`
- **Validation:** a row is written only if `sdk_check` passes, `unresolved_bindings` is empty, and `compile(decompile(m)) == m`. Rejections are counted by reason. A round-trip mismatch is logged with the messages, as a possible SDK bug.
- **Dedup:** at most `k` rows (default 3) per struct hash. A row whose plain fingerprint equals a dev/test seed's or draft's is dropped.
- **Determinism:** the same seed and config give byte-identical output.

## Vocabulary

The LLM writes words only. Structure, paths, bindings and data values that the code derives from labels stay deterministic code. The vocabulary is written once per domain, validated, committed and reviewed by you as a diff. After that, generation reads only the committed files, so S5 (determinism) holds and no tests or `synth` runs call the model.

The vocabulary is grouped into **topics**, so that the words on one screen belong together. The generator picks one topic per surface and draws every title, sentence, field, action, list and choice from it. (Without topics, a "Hotel details in Kyoto" heading would sit above a flight sentence and a seat form; refuter review, 2026-11-01.)

One file per domain: the header (`domain`, `model`, `prompt_sha256`) plus `topics` [8, unique names]. Each topic is a mapping with exactly these keys (counts per topic in brackets):
- `name`: sentence case, at most 40 characters, such as "Hotel booking"
- `screen_titles` [4]: headings, at most 60 characters. Unique across the whole domain.
- `section_titles` [4]: sub-headings, at most 40 characters
- `body` [6]: sentences of 5–25 words with **no digits**, because numbers in prose would contradict bound data. Unique across the whole domain.
- `fields` [8]: `{label, kind, examples, options?, min?, max?}`
  - `kind` is one of `text`, `email`, `number`, `date`, `time`, `bool`, `choice`, `long_text`, `secret`
  - `examples` [3–5] are distinct and match the kind:
    - `date`: a quoted `YYYY-MM-DD` string
    - `time`: `HH:MM`
    - `email`: an address on `example.com`, `example.org`, `example.net` or a `.test` domain
    - `long_text`: at least 5 words
    - `bool`: contains both `true` and `false`, so it is exempt from the distinct rule
    - `choice`: one of the options, using at least 2 different options. Choice is also exempt from the distinct rule, because a 2-option field can't have 3 distinct examples.
  - `options` [2–6, distinct] is required for `choice` and not allowed otherwise
  - `min`/`max` are optional, and only for `number`. If given, min < max and every example is within them. They become check and Slider bounds.
- `actions` [4]: `{label, event, icon?}`. `event` is snake_case (the gallery convention: `view_details`, `pay_bill`). `icon` is in the catalog's Icon enum.
- `entities` [2]: `{name, icon?, fields: [label…] (2–5, distinct), records [4–6]}` for lists and templates. Each record has exactly the fields. Values are strings, numbers or booleans, never YAML dates. A column has one type: int and float both count as number. Records are distinct.
- `choice_groups` [2]: `{label, multi, options [2–6, distinct]}`. `multi` is a bool: true means multiple selection, false means mutually exclusive or Tabs.

Every label, entity name and choice-group label:
- starts with a capital letter
- has no underscores
- has no internal lower→upper camel boundary, so `InStock` is rejected because it would give `/instock`

Within a topic, field labels and choice-group labels share one set of distinct paths (`path_for_label`), because both bind into the same data model. Each entity's field labels have distinct paths of their own, because they are relative to a template item. Unknown keys are rejected at every level.

Also rejected:
- placeholder text: lorem, ipsum, foo, TODO (upper case), xxx, "example text", "sample text", "placeholder text"
- **eval literals.** An eval literal is a literal text, check message, event name or data-model string in a dev or test seed, draft or case that doesn't also appear in a train seed. Both sides are normalised: words are split at spaces, `_` and camelCase, lower-cased, with leading `#` and trailing punctuation stripped. The rule is:
  - an eval literal of **3 or more words** is rejected when it appears as a whole-word run *inside* any vocabulary string
  - a **2-word literal containing a digit** is rejected on an exact match
  - other 2-word literals ("Email address", "Phone number") and single words are ordinary UI words and are allowed. Banning them led the model to awkward dodges such as "Billing Zip Code Number".

The writer:
- runs three calls per domain, all with `reasoning_effort="low"`, a fixed `prompt_cache_key`, and one byte-identical system prompt (the schema plus the Icon enum):
  1. "Domain: D. List 8 topics." The reply is `<topics>` YAML.
  2. and 3. "Domain: D. Write these topics: …", four topics per call. The reply is `<vocab>` YAML holding `topics:`.
- validates each reply against the topic rules, the requested names, and the screen titles and bodies already taken by earlier batches
- on failure, sends the errors back, up to 2 retries per call
- writes the file only when the merged domain is valid
- runs domains concurrently, at most 4 at a time

The header records `model` and `prompt_sha256`, which is the SHA-256 of the system prompt. Keys come from `genUI/.env` and are never printed. `vocab --check` re-validates every committed file and fails when a file's `prompt_sha256` doesn't match the current system prompt. The test suite runs the same check.

**Re-writing:** a committed file can start failing `vocab --check` because dev or test cases were rebuilt (new eval literals) or because the prompt changed. Re-run `uv run python -m a2ui_eval.data_cli vocab --domain D` (live call) and review the diff.

## Intents

The intent for each row states the purpose (from the skeleton and domain), the components that matter, every literal text, the event names, and the data paths under the 2b rule: input values, list sources, event context, and values bound by data. Display-only paths follow the label rule, so the model can derive them. There are three detail levels:
- **terse:** purpose plus the identifiers the scorer needs
- **medium:** adds the components and their order
- **specific:** adds variants, alignment and formats

Every level names all required identifiers. A self-check verifies this: every event name, stated path and literal text in the reference appears in the intent.

## Linter

These rules run on the folded v0.9 surface:
- every Button has an action
- a form (a skeleton tagged `form`, or two or more inputs) has a submit Event whose context references every input path
- every input value is bound to the data model
- Modal has a trigger Button and non-empty content, and Tabs has two or more tabs, each with content
- Icon names are in the catalog enum
- heading text is at most 60 characters, and body text is at least 3 words
- a List or `_template` source is a non-empty list
- no lorem ipsum

Each rule has an id. The linter also runs `--gallery` on every single-turn converted seed. Seeds that fail a rule by upstream design are listed in a committed allowlist with the reason, and the target is 100% after the allowlist.

## Criteria → tests (2c-1)

| # | Criterion | Test |
|---|---|---|
| S1 | `CatalogModel` lists every catalog component and function, with the SDK's slot order and required set. Slider's order is `label, min, max, value`. Tabs.tabs and ChoicePicker.options are `map-list` and literal-only. | `test_synth_catalog.py::test_matches_sdk_helper`, `::test_slider_order`, `::test_map_lists_literal` |
| S2 | Adding a fake component or prop to a copy of the catalog shows up in the model and as a coverage gap. Nothing is hard-coded. | `test_synth_catalog.py::test_reads_catalog_changes` |
| S3 | Every emitted row passes `sdk_check`, has no unresolved bindings, and round-trips (`compile(decompile(m)) == m`). Its `reference_express` is the canonical decompile, with no keyword args for props. | `test_synth_pipeline.py::test_rows_valid` (200 rows, several seeds) |
| S4 | A tree that fails validation or the round trip is rejected with a reason and never written. A Tabs title binding is one such forced case. | `test_synth_pipeline.py::test_rejects_invalid`, `::test_rejects_round_trip_mismatch` |
| S5 | Same seed and config give byte-identical output. A different seed gives different output. | `test_synth_pipeline.py::test_deterministic` |
| S6 | Coverage: after a run of the configured size, every component, (component, prop), optional-slot pattern, binding kind, list kind, check kind and action kind reaches N. The test runs at small N. | `test_synth_coverage.py::test_every_key_reached`, plus one test per category: `::test_components`, `::test_slot_patterns`, `::test_bindings`, `::test_lists`, `::test_checks`, `::test_actions` |
| S7 | Regression for the v1/v0.9 TextField slot bug: a generated TextField with `validationRegexp` set compiles to `validationRegexp`, never `variant`. Every generated component's compiled props equal the props the generator meant to set. | `test_synth_pipeline.py::test_textfield_slots_v0_9`, `::test_intended_props_compile` |
| S8 | The linter passes on 100% of synthetic rows, and on 100% of gallery seeds after the allowlist. Each rule fires on a hand-made bad surface. | `test_synth_lint.py::test_gallery_passes`, `::test_synthetic_passes`, `::test_rule_fires[rule]` |
| S9 | Every intent names every event name, literal text and required path of its reference, at every detail level | `test_synth_intents.py::test_required_identifiers_present[detail]` |
| S10 | Dedup keeps at most `k` rows per struct hash. A row whose fingerprint equals a dev/test seed or draft is dropped. | `test_synth_pipeline.py::test_dedup_cap`, `::test_eval_fingerprint_dropped` |
| S11 | Splits: each skeleton is in exactly one of train/dev/held_out. `synth --split dev` uses only dev skeletons. No synthetic row goes to test. Every banned archetype's matcher rejects a surface built to mirror its test design, and passes the other skeletons. | `test_synth_splits.py::test_skeleton_partition`, `::test_split_routing`, `::test_no_test_rows`, `::test_banned_archetypes[family]` |
| S12 | `export --mix 0.7` gives about 70% synthetic rows (within 1 row of the target), oversamples gallery rows by repeating them, and is deterministic. The split guard rejects a synthetic row from a non-train skeleton. C11–C14 still hold. | `test_export.py::test_mix_ratio`, `::test_mix_rejects_dev_skeleton`, existing export tests |
| S13 | Synthetic review mode serves a deterministic sample of `--sample` rows rendered as v0.9, and records `{id, verdict, reason}`. Reject needs a reason. | `test_review.py::test_synthetic_sample`, `::test_synthetic_verdict_requires_reason` |
| S14 | The coverage report lists every key with its count, and the Markdown table is written to `data/synth/README.md` | `test_synth_coverage.py::test_report_table` |
| S15 | All earlier tests pass | full suite |
| S16 | `validate_vocab` accepts a hand-made valid file and gives the right error for **each** rejection rule in Vocabulary, one parametrised row per rule. The eval-literal rule rejects a 3+-word literal inside a longer string and a snake_case event matching spaced eval text. It allows a digit-free 2-word literal. | `test_synth_vocab.py::test_valid_accepted`, `::test_rule_rejects[rule]`, `::test_eval_literal_matching` |
| S17 | Writer, with a stubbed model: invalid output is retried with the errors in the next message, valid output is written with `model` and `prompt_sha256`, and after the retries run out nothing is written and the command exits non-zero. Topic batches are merged, and a screen title already used by an earlier batch is sent back as an error. The system prompt is byte-identical across domains and calls. | `test_synth_vocab.py::test_writer_retries_with_errors`, `::test_writer_writes_valid`, `::test_writer_gives_up`, `::test_writer_rejects_taken_titles`, `::test_prompt_stable_prefix` |
| S18 | Every committed vocab file passes `validate_vocab` and its `prompt_sha256` matches the current system prompt. A stale hash fails the check. | `test_synth_vocab.py::test_committed_vocab_valid`, `::test_check_flags_stale_prompt` |
| S19 | The tests never call a model. Importing `data_cli` or `vocab_writer` doesn't import LiteLLM, and glimmer-tune's environment doesn't contain it. | `test_synth_vocab.py::test_no_llm_import_at_module_load` |

Checked by hand, not by the suite: a reject rate below 10% on a 50-row reviewed sample. First target: 3–5k train rows and about 200 dev rows across 6 or more dev skeletons.

## Decisions

- Targets are positional only (canonical decompile). There are no keyword-argument targets.
- Text-only replies are templated (2c-2). Intent rewording uses `meta/muse-spark-1.3` (2c-3), logged per row. Muse Glimmer is never used for data generation.
- No skeleton mirrors a test design (ADR 0009). Test stays gallery-only.
- Synthetic surface ids vary (`<domain>-<skeleton>-<n>`). Gallery ids stay constant.
- Images, audio and video use `https://example.com/...` URLs. They're never fetched.
- The generator never emits `theme`, `accessibility`, `null` data values, or bindings inside `Tabs.tabs`/`ChoicePicker.options`, because none of them round-trip.
- `and`/`or`/`not` inside checks: the research didn't test this. S6's check category confirms it compiles; if it doesn't, it's removed from the coverage keys and the change is recorded here.
- Dev reports gallery rows and synthetic rows separately.
- Vocabulary is written by `meta/muse-spark-1.3`, once per domain, and committed. Rejected alternatives: code-only vocabulary (too little variety for ~5k rows) and the LLM writing whole screens (that brings back invalid trees and two spellings). Paths are derived from labels by code, never written by the model.
- Vocabulary is shared by train and dev skeletons. A split leak through distinctive phrases is blocked by the eval-literal rule, not by separate vocabularies. Generic UI words are shared on purpose, so synthetic dev measures structure, not unseen wording. A consequence: generic dev and test labels such as "Email Address" (`32_advanced-form-validator`) can appear in train vocabulary, so they aren't held out. Test (gallery only) measures real-world wording.
- Casing variety (Title Case vs sentence case) is left to the generator, which can apply deterministic casing transforms. The vocabulary doesn't have to vary it.
- Deferred from the vocabulary review: repeats across domains (labels such as "Date of birth" in several domains are fine).

## Non-goals (2c-1)

- Update turns, delete turns, text-only rows (2c-2) and LLM rewording (2c-3)
- Training runs
- Generating the full-size set before you've seen the coverage report and 10 samples

## 2c-1 build A: skeletons and the generator

Status: built in the session of 2026-11-01. This part of 2c-1 builds the skeletons, banned archetypes, grow, fill, data model, intents, struct hash, coverage and the pipeline, with the `synth` and `coverage` commands. **Build B** comes later and covers the plausibility linter rules (S8), `export --mix` and the export guards for synthetic rows (S12), and review mode (S13). Checkpoint b (the coverage report plus 10 samples) comes after build A. In build A, `lint.py` holds only the banned-archetype matchers.

### Skeleton file

Each skeleton lives in `a2ui_eval/synth/skeletons/<id>.yaml` and has these keys:
- `id`: equals the file stem
- `tags`: a subset of `form`, `list`, `detail`, `dialog`, `media`
- `domains`: a list of vocabulary domains, or `all`
- `tree`: one node

A node is one of three things:
- **A fixed container**, `{<Component>: {<slot>: node | [node…], props: {…}}}`. The component must exist in the `CatalogModel`, and each slot must be a `child` or `children` slot. `props` fixes enum props, such as `{align: stretch}`.
- **A block**, either a bare name such as `title`, or `{block: <name>, optional: <p>, …params}`. `optional` is the probability that the block is included.
- **An open slot**, `{open: [<block>…], count: [lo, hi]}`. It is filled with `count` blocks of the listed kinds, chosen by coverage weight, within the row's depth budget.

The blocks, which live in `grow.py`, are these. Each one draws its words from the surface's one topic.

| Block | Builds |
|---|---|
| `title`, `section`, `body` | a heading Text (variant or markdown prefix), a sub-heading Text, or a body Text |
| `kv` | a Row of a label Text and a bound value Text. The value can be a plain absolute binding, a nested map path, a list index, `formatString`, `formatNumber`, `formatCurrency`, `formatDate` or `pluralize`. |
| `stats` | a Row of 2–4 Columns, each holding a value Text and a caption Text |
| `field` | an input for one topic field, chosen by the field's kind:<br>• text, email, secret, long text or number → TextField, or a Slider for a number with bounds<br>• date or time → DateTimeInput or TextField<br>• bool → CheckBox<br>• choice → ChoicePicker<br>It may carry checks. |
| `choices` | a ChoicePicker from a choice group. `multi` maps to `multipleSelection`, otherwise it is `mutuallyExclusive`. |
| `actions`, `submit` | a Row of 1–3 Buttons, or one primary Button. Each Button's child is a Text or an Icon. The action is an Event (bare, with context, or with form context) or `openUrl`. |
| `media`, `icon_row` | an Image, Video or AudioPlayer with an `https://example.com/...` URL; or a Row holding an Icon (literal or bound name) and a Text |
| `divider` | a Divider, with the vertical axis only inside a Row |
| `list` | the records of one entity, in a Row, Column or List. It is either a `_template` with relative bindings, or static children with list-index bindings. |
| `tabs` | Tabs with 2–3 tabs. Titles come from section titles, and each tab holds a nested group. |
| `modal` | a Modal whose trigger is a Button and whose content is a Column holding a section, a body and optionally actions |
| `group`, `card` | a Column, Row or Card around a nested open slot |

When a surface has any inputs, its first `submit` Button (or else the first Button that isn't a Modal trigger) gets an Event whose context holds every input path. The linter rule needs this only for 2 or more inputs, but a lone input is still sent with its form. A surface with 2 or more inputs and no Button is rejected (`fill: form_without_submit`). Every skeleton that can hold fields contains a `submit` block or an `actions` block, and the skeleton loader enforces this.

Splits: `data/splits.yaml` gains two keys:
- `skeletons:`, with `train`, `dev` and `held_out` lists
- `banned_archetypes:`, mapping each test family to `{description, matcher}`

`load_splits` ignores both keys. There are about 22 skeletons: about 14 train, 6 dev and 2 held out.

### Config, coverage keys and weights

`SynthConfig` holds these defaults:

| Setting | Default |
|---|---|
| `depth` | (2, 5) |
| `size` | (5, 40) |
| `target` (N) | 30 |
| `dedup_k` | 3 |
| `binding_ratio` | 0.5 |
| `pattern_cap` | 16 |
| `always_set` | `{TextField: [value], DateTimeInput: [enableDate, enableTime], ChoicePicker: [variant]}`. Patterns without these slots aren't keys. The reasons:<br>• an unbound input fails the linter<br>• a DateTimeInput with neither flag set can't pick anything, so the flag matching the field kind is true and the other is false<br>• a multi choice group needs `multipleSelection` (build-A review, 2026-11-01) |

Depth counts levels, with root as level 1.

Coverage keys are computed from the **emitted JSON**, never from what the generator intended. The universe of keys comes from the `CatalogModel` (S2):
- `component:<C>`
- `prop:<C>.<p>`
- `pattern:<C>:<slots>`. These are the first `pattern_cap` patterns in the order (size, slot positions), and each must contain `always_set`.
- `shape:<C>:trailing|middle`, for each shape the patterns can reach
- `binding:` followed by one of:
  - `absolute`, `relative`, `nested`, `index`, `interpolation`
  - one key per value-role function except `formatString`
- `list:template:<C>` and `list:static:<C>`, for every component with a `children` slot
- `check:` followed by one of:
  - one key per check-role function
  - `length:min|max|both`, `numeric:min|max|both`
  - `multi`
- `action:` followed by one of:
  - `event_bare`, `event_context`, `event_form_context`
  - one key per action-role function

Weighting:
- A choice's weight is `max(0, N − count) + 1`, summed over the keys it would hit. Weights are recomputed per row, and only accepted rows count.
- Open slots weight each block kind by the keys of its components.
- Within a block, choices weight their specific keys. A choice is one of: a component, an optional-slot pattern, an enum value, a binding kind, a check kind or an action kind.

### Pipeline and rows

`synthesize(n, seed, split, config) -> (rows, report)`. The skeletons come from `split`, and `held_out` and `test` are refused. Each attempt runs these steps in order:
1. Draw the skeleton (balanced by row count), the domain, the topic, the depth and the size.
2. Grow, then fill, then build the data model, then emit v0.9 messages: `createSurface{sendDataModel: true}`, `updateComponents`, and `updateDataModel{path: "/"}` when there is data.
3. Validate, in this order:
   - **sdk:** `sdk_check(wrap(decompile(m)))`
   - **round trip:** `compile(decompile(m)) == m`
   - **unresolved:** `unresolved_bindings`, where input paths count as allowed
   - **size and depth:** within range
   - **banned:** no banned-archetype matcher matches
   - **eval literal:** no literal text leaks an eval literal (vocabulary rule, applied to generated strings such as check messages)
   - **intent:** the S9 self-check passes
   - **eval fingerprint:** the fingerprint is not a dev or test seed's or draft's
   - **dedup:** the struct hash has fewer than `k` rows

`report.rejected[reason]` counts each failure, including build failures: `grow` (no room within the budget), `fill`, `data_conflict` (two labels give one path) and `decompile`. The run stops at `n` rows, or after `20 × n` attempts with a shortfall in the report.

Row ids are `synth/<split>/<skeleton>/<nnnnn>`, and the surface id is `<domain>-<skeleton>-<n>`. The row also carries `topic`, as well as the case-shape fields above. `detail` rotates terse, medium, specific. `patterns` is the row's coverage keys.

`data_cli synth --split {train,dev} --count N --seed S [--target N]` writes `data/synth/<split>/cases.jsonl` and prints the report. `data_cli coverage` writes `data/synth/coverage.json` and the table in `data/synth/README.md`.

### Intents (made concrete)

Every intent states:
- the domain and topic purpose
- every literal text, quoted exactly, including markdown prefixes, check messages, option labels and values, Tabs titles, URLs and format strings
- every event name
- every non-boolean data value
- the paths:
  - input value paths, list sources, event context paths, bound Icon names, DateTimeInput bounds, nested paths and index paths, written as `/path`
  - **display-only paths** (a Text bound to `path_for_label(L)` while `L` is stated) are stated only at `specific`. Elsewhere the model derives them by the label rule.
  - template-relative paths follow the same rule, through the entity's field labels

The three levels add detail like this:
- **medium** adds the component types in tree order
- **specific** adds the enum props (variants, alignment, justify, fit, axis, display style) and the display paths

The self-check is `intent_gaps(row) -> list[str]`, and it runs on the reference JSON, not on the generator's tree. **Decision:** data values are stated because the scorer compares them, and a target holding values that the intent never gave would teach the model to make them up. This extends the 2c Intents text, which listed only the literal text.

### Banned archetypes

There is one matcher per test family, written as a predicate on the folded v0.9 components. Each one is tight enough to catch a near-copy without catching generic forms, lists or cards:

| Test family | The matcher catches |
|---|---|
| `00_complex-layout` | a root Column whose children are [Text, Row of 2 or more TextField, Text] |
| `02_email-compose` | under one Column, 3 or more consecutive Rows of [Text, Text], then a Divider, then a Column of 3 or more Text, then a Row of 2 or more Buttons |
| `12_chat-message` | a template item that is a Row of [Image, Column[Row[Text, Text], Text]] |
| `16_workout-summary` | a Column of [Row[Icon, Text], Divider, Row of 3 Columns each holding 2 Text, Text] |
| `20_restaurant-card` | a Column of exactly [Image, Column] whose inner Column starts with a Row[Text, Text], holds a Row that starts with Icon "star", and has no Button. The train seed 29_movie-card is a near twin, with a Modal beside the details and a Button in them, so the matcher excludes those. |
| `25_contact-card` | a Column of [Image, Text, Text, Divider, Column of 3 or more Row[Icon, Text], Row of 2 or more Button] |
| `30_live-invitation-builder` | a Row of 2 Columns, where one holds an input and the other holds a Card |
| `36_modal` | a Column of exactly [Text, Modal], where the Modal's content is a Column holding at most 2 Text and nothing else |

S11's banned test checks two things: each matcher accepts its own converted test seed, and it rejects 50 rows generated from every train and dev skeleton.

### Criteria covered by build A

S2 (the coverage half), S3, S4, S5, S6, S7, S9, S10, S11, S14 and S15. S4's forced round-trip mismatch is a `theme` on `createSurface`, which decompile drops. S6 runs at N = 2. S10's eval-fingerprint test injects a fingerprint. Extra build-A tests:
- `test_synth_skeletons.py::test_skeletons_load` covers every node kind, unknown components or slots, and a fields skeleton without a submit.
- `test_synth_pipeline.py::test_split_refuses_test_and_held_out`
- `test_splits.py` still passes with the new keys.

### Decisions (build A)

- Grow and fill hold component *semantics*, such as "a bool field becomes a CheckBox" or "the variant follows the field kind", because the schema can't supply them. Slot order, required props, enums, patterns and function roles still come only from `CatalogModel`. A catalog component with no block never gets generated, and it shows up as a coverage gap (S2).
- `pluralize` nouns come from an entity name whose last word ends in "s" ("Available flights" → flight/flights), inside `formatString`, in gallery style. It counts the records, bound at `path_for_label(name + " count")`.
- `formatCurrency` is used only for labels matching price, cost, total, amount, fee, balance, fare, rent, budget or payment. The currency is drawn from AUD, USD, EUR and GBP and stated in the intent.
- Check messages are templated in code, for example "<Label> is required", and are guarded by the pipeline's eval-literal step.
- `not` appears on a submit Button as `not(length(<path>, max: 2))`, with the message "<Label> must be longer than 2 characters".
- Held-out skeletons are listed but not generated in 2c-1.
- The items of a static list, and a template item, share one look. The icon, the column formats, the slot pattern, the enum props and the weight are drawn once per list. Static item *i* shows record *i*, as a literal or bound to `/<entity>/i/<field>`.
- Secret fields are never prefilled. Media URLs are unique within a surface.
- The purpose line names the screen kind from the emitted content: form if it has inputs, else dialog, list, media or detail. It doesn't come from the skeleton tags.
- The enum props stated at `specific` are read from the `CatalogModel`'s static-enum slots. `Icon.name` is excluded because it is always stated.
- The S9 self-check matches a number only when it stands alone, not inside a longer number.
- For build B: the export guards must also block a synthetic train row whose fingerprint equals a synthetic dev row's. Today `eval_prints` covers only gallery dev and test seeds and drafts.
- Decided after the build-A review (grilling, 2026-11-01). These are to be built before full-size generation:
  - **The target follows the intent.** At terse and medium, the Reference leaves out every optional prop the Intent doesn't state, such as weight, variants and alignment. Only specific rows carry full prop variety. Coverage then comes mostly from specific rows, so the detail mix or the row count has to change (see the next round).
  - **Skeletons and topics must fit.** A code-side filter checks that a topic has what a skeleton's blocks need. The vocabulary doesn't change, because checkpoint (a) approved it.
  - **Size tail:** about 15% of rows have 25–40 components. Build B's export checks the token lengths.
  - **Detail mix is steered by coverage.** A row that would hit a starved prop or pattern key is rendered as specific. Terse and medium each keep a floor of at least 25% of rows.
  - **Terse states layout in plain words.** "Side by side" means a Row, "in a card" means a Card, and stacked is the default Column. A terse target is then fully determined by its intent.
  - **Fit filter:** needs are inferred from the blocks (a list needs an entity, a field block needs fields of a fitting kind). A skeleton can also add an optional `requires:` list for semantic needs, such as `money_field` for checkout.
  - **Held out** skeletons are a reserve. They are never used for training or dev, and are generated only to probe generalisation later. "Family" means gallery designs only (`glimmer-tune/CONTEXT.md`).

## 2c-2 and 2c-3 outline

- **2c-2:** generate a base surface and apply a mutation: add, remove or replace a subtree, change data, replace the root with `update=true`, or delete the surface. The row is `existing_surface` = the base, and the target is the update. Export gains a two-turn row: user₁, assistant₁ = base Express, user₂, assistant₂ = update. Text-only rows (5–10%) get templated replies and `expect_ui: false`. The struct hash covers the base and the mutation, since `fingerprint()` gives every rootless surface the same value. Note that the scorer's `extracted` flag is inverted for `expect_ui: false`.
- **2c-3:** reword each intent into 1–3 phrasings, drop any rewording that loses a required identifier (S9's check), and log the model per row. Keys come from `explorer/.env` without printing them. Tests stub the model.

## Test command

```bash
cd glimmer-tune && uv run pytest
```
