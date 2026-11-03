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
- Dev/test intents are drafted from templates, never by a model. You edit `intents_draft.jsonl` and set `reviewed: true`. (Amended by 2d: a report-only model review stands in for yours.)
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
| V8 | `draft --keep-reviews` regenerates the drafts and carries `intent` and `reviewed` over by `id`. An unreviewed row keeps its old intent only if its UI is unchanged; otherwise it takes the new render. It fails, writing nothing, if a reviewed id is missing from the new drafts or its UI changed. "UI changed" means a different `reference_express` (text and values) or fingerprint (structure); the fingerprint alone ignores text. It reports dropped unreviewed ids. It refuses to run without an existing draft. All JSONL writes are atomic (temp file, then rename). | `test_generate.py::test_keep_reviews_carries_edits`, `::test_keep_reviews_fails_on_missing_reviewed_id`, `::test_keep_reviews_fails_on_changed_fingerprint`, `::test_keep_reviews_fails_on_changed_text_with_same_fingerprint`, `::test_keep_reviews_cli`, `::test_write_jsonl_is_atomic` |
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

# 2d: intents in the user's words

Status: **approved** 2026-11-01. It replaces the 2b register rules for every gallery family (train, dev and test) and amends the 2c-1 intent rules. It follows a model review of the 191 unreviewed drafts (Sonnet, report-only). That review found stale intents (fixed in V8), incoherent slot combinations, duplicate references and spec-style wording.

## Why

Intents read like a prop listing ("a column (spaceBetween, stretched) with an h1 markdown header"). Real users name what they want on screen, not the props that build it. You decided (2026-11-01):
- dev/test intents read **the way a user really asks**, so the model has to choose layout props itself
- train intents move to the same register, so train and eval match

## Register rules (R)

| # | Rule |
|---|---|
| R1 | An intent is a request to an assistant: goal first ("I need…", "Show…", "Make…", or a plain noun phrase). It names no component, prop or enum value: no Column/Row/Card/Text, no justify, align, weight, variant, h1, fit or usageHint. Everyday layout words a user might say are allowed ("side by side", "in a card", "a line between", "at the bottom"). |
| R2 | It states the **content** the user would supply: every heading, label, button text, message and option, plus the data values (names, numbers, dates, URLs). Literal text that a user would quote is quoted. |
| R3 | It never states data paths, event names, regexes, date-format patterns or icon names in catalog form. A user may describe an icon ("a heart") or a format ("the weekday name"). |
| R4 | Each family has at least two intent templates that differ in phrasing and length. Neither has to pin the whole reference. |
| R5 | Related slot values are one bundled slot (2b rule, now enforced for headers with their fields, senders with subjects, channels with conversations, labels with messages). |
| R6 | Dev/test drafts within a family have distinct `reference_express` values. Duplicates are dropped and the slot space must be large enough to fill the count. |

## What this changes elsewhere

- **C16 (slot sharing)** keeps checking every scalar slot leaf **except** leaves keyed `path`, `event`, `icon`, `align`, `justify`, `variant`, `display`, `regex`, `key`, `date_format` and `marks` (markdown heading marks). Stored option values and a picked index are per-family `MAY_OMIT` entries. A leaf a user would type (text, numbers, URLs) must still appear.
- **Scoring:** the reference keeps the gallery's props, so a perfect answer to an under-specified intent can still miss slots. exact_match stops being a ceiling to aim for. Milestone 3 reports, besides the literal slot F1, a score that ignores data paths and event names (it was already planned for paths). Read the numbers as comparisons between models, not as absolute quality.
- **2c-1 intents:** S9's self-check drops paths and event names. It still requires literal text and data values. The detail levels stay, but `specific` states props in everyday words ("equal widths", "spread out top to bottom"), never prop names. "The target follows the intent" stays: an unstated optional prop is left out of a synthetic target.
- **Approvals:** dev/test drafts are reviewed by a model (report-only Sonnet agent). Its approvals count as yours (your decision, 2026-11-01). `SPEC.md` 2a's "you edit and set `reviewed: true`" now means: the review agent's `approve` verdicts are applied with a script, and its `fix`/`reject` rows go back to the templates.
- **The 29 approved rows** are re-drafted too, because their intents are in the old register. Test is not built yet, so nothing is frozen.

## Criteria → tests

| # | Criterion | Test |
|---|---|---|
| D1 | No intent template of any family, outside its slot expressions (slot values are content), contains a component or enum name from the catalog as a whole word (Column, Row, Card, Text, spaceBetween, stretch, h1, caption, …) or a `/path` | `test_generate.py::test_intents_in_user_register[family]` |
| D2 | C16 as amended above | `test_generate.py::test_intents_mention_slot_values[family]` |
| D3 | `draft` gives the default counts with no duplicate `reference_express` within a family | `test_generate.py::test_drafts_have_distinct_references` |
| D4 | Every template still reproduces its seed and every variant self-scores exact_match (C5, C7) | existing |
| D5 | S9 as amended | `test_synth_intents.py::test_required_identifiers_present[detail]` |

## Order of work

1. Rewrite one family (00_complex-layout) as the sample. You approve it.
2. Rewrite the other 40 templates. Re-draft dev/test with `--force` and regenerate train and the export.
3. Run the Sonnet review on the new drafts, report-only. Apply its approvals, fix the templates it flags, and repeat until the rejects are small enough. Then run `build-eval`.
4. Amend the 2c-1 intents (S9, the `specific` wording) as part of A2.

**Progress (2026-11-01):** steps 1–3 are done. All 41 templates are in the user's words. Train was regenerated (1560 rows, export drops 0). The model review took four rounds:
- round 1 (old drafts): 36 approved of 191
- round 2: 107 of 220
- round 3: 216 of 220
- round 4: the last 4 (16_workout-summary) after each workout got its own heading

All 220 drafts are `reviewed: true`, and dev is built (84 rows). Test is **not** built yet, because building it freezes it. Step 4 is still open.

## Non-goals

- Model-written intents for gallery rows. 2c-3's rewording stays optional.

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
- The scorer compares data paths and event names literally. Component ids are aligned by anchors, root, structure and text similarity (`align.py`), so ids are free.

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
- Decided in A2 grilling (2026-11-01, after 2d). These amend the items above and 2d's "2c-1 intents" note:
  - **House style replaces "the target follows the intent".** Terse and medium targets carry a fixed, deterministic set of default props, worked out from content and role (for example: an actions row is `justify: end`, the submit button is `primary`). The target is still fully determined by the intent plus the house style. Specific intents state only departures from it. Reason: gallery references keep their props when the intent doesn't ask for them (2d), so a no-props synthetic target would teach the opposite habit. The explorer's users never name props, so the unprompted look is the product.
  - **Detail levels in the user's words.** Terse states the purpose and all content and values, with layout words only where the layout isn't the default ("side by side", "in a card"). Medium adds the placement and grouping of every piece ("a heading at the top, then the three fields, with two buttons side by side at the bottom"). Specific adds styling in everyday words ("a large heading", "the buttons spread out", "a password field").
  - **Events.** Event names stay `snake_case(label)` (true for all 320 vocabulary actions) and are never stated. Form submits send every input, as now. Other buttons are bare, or, for a list item, send the item, keyed and pathed by the label rule. The intent says that in words ("pick this flight"). The random display-path context (`fill.py` `event_context`) is removed.
  - **Enum phrase table.** A committed table maps each enum value to its everyday phrase. A few words real users say are allowed ("avatar", "caption", "fill the width"). Pure-engineering distinctions (spaceAround vs spaceEvenly, h4 vs h5, and the like) get no phrase. Those values never appear in synthetic targets, and their coverage keys come off the target list, which SPEC lists. The user approves the table before it is built.
  - **Icons** are stated as humanised names ("a calendar icon", "a back arrow"), with an override table for odd names. Ambiguous pairs (one description fitting two icons) are checked and resolved in the table.
  - **House style source:** the gallery's majority habits, role by role. Screen title `## `, section heading `### ` (markdown marks, never `h1`–`h5`, as in the gallery), secondary text `caption`, Row `align: center`, actions Row `justify: end`, Image `fit: cover`, other props unset. One departure from the gallery majority: the submit Button is `primary`.
  - **Coverage:** house-style values count toward coverage. The terse and medium floors (25% each) stay, and the row count rises until coverage is met. Checkpoint (b) reports the count it took. Above 6000 rows, we revisit.
  - **Phrasing variety:** at least 3 hand-written phrasings per detail level, picked at random. The LLM rewording in 2c-3 stays optional.
  - **Cut order if time runs short before 2026-11-30:** first the size tail, then the milestone 3 runner (compare base and tuned by hand). Never fall back to gallery-only training.
  - **Self-checks (decided by Claude at the user's request):** a terse or medium target holds no enum value outside the house style; a specific intent holds the phrase for every departure (S9 extended); every synthetic event equals `snake_case(label)`. The house style, the phrase table and the icon table are each one module, with tests.
  - **Checkpoint (b) is re-issued after A2 (Q10 confirmed):** a coverage report plus 10 samples, with 3 samples per detail level, approved before full-size generation.

## 2c-1 A2: house style, intents in the user's words, fit and size

Status: **in build** (2026-11-01). It implements every decision in the two grilling blocks above (build A and A2), plus 2d step 4. ADR 0011 records why. It replaces the "Intents" and "Intents (made concrete)" sections for synthetic rows wherever the two disagree.

### Files

- `a2ui_eval/synth/style.py` (**new**): the house style (`house_style(node) -> dict[prop, value | None]`), the set of *style props*, and the phrase table for departures.
- `a2ui_eval/synth/phrases.py` (**new**): the icon phrase table, the regex phrase per field kind, date-format rendering, and the opening sentences for each detail level.
- `a2ui_eval/synth/fill.py`: style values are drawn from the house style or the phrase table (never an unphrased value); events follow the event rules; template list items may hold an item button.
- `a2ui_eval/synth/grow.py`: the item button; the size target draws from the tail.
- `a2ui_eval/synth/intents.py`: rewritten describer; S9 amended.
- `a2ui_eval/synth/pipeline.py`: the detail choice, the house-style normalisation, the fit filter and the size tail.
- `a2ui_eval/synth/skeleton.py`: an optional `requires:` key.
- `a2ui_eval/synth/coverage.py`: `SynthConfig` gains `tail` and `detail_floor`.
- `tests/test_synth_style.py`, `tests/test_synth_phrases.py` (**new**), plus amendments to the existing `test_synth_*` tests.

### Style props and the house style

A *style prop* is one of: `Text.variant`, the heading mark on a title or section Text, `Image.fit`, `Image.variant`, `Row.justify`, `Row.align`, `Column.justify`, `Column.align`, `List.align`, `Button.variant`, `ChoicePicker.displayStyle` and `weight`. Every other prop is content or follows from content, and is stated (or derived) at every level: `TextField.variant` from the field kind, `ChoicePicker.variant` from "pick one" or "pick any", `List.direction: horizontal` from "scrolling sideways", and `Divider.axis` (vertical inside a Row, else unset).

The house style, by role:

| Where | House value |
|---|---|
| title Text | text starts with `## `, no variant |
| section Text | text starts with `### `, no variant |
| kv_label, stat_label | `variant: caption` |
| every other Text | unset |
| every Row | `align: center` |
| actions Row | `justify: end` (and `align: center`) |
| other Row, Column, List | justify and align unset |
| Image | `fit: cover`, variant unset |
| submit Button | `variant: primary` |
| other Button | unset |
| ChoicePicker | displayStyle unset |
| weight | unset |

Departures and their phrases (used only at `specific`). A value with no phrase is never generated:

| Prop | Value → phrase |
|---|---|
| title mark | `# ` → "a big title" |
| section mark | `#### ` → "a small section heading" |
| Text.variant (non-heading) | caption → "in small print"; h4 → "in large text"; unset on a label → "the label in regular text" |
| Image.fit | contain → "shown whole, uncropped"; fill → "squashed to fill its frame" |
| Image.variant | icon → "icon-sized"; avatar → "as an avatar"; smallFeature → "small"; largeFeature → "large"; header → "as a banner across the top" |
| Row.justify | start → "on the left"; center → "centred"; end → "on the right"; spaceBetween → "spread to both ends"; spaceEvenly → "evenly spaced" |
| Row.align | start → "lined up at the top"; end → "lined up at the bottom"; stretch → "all the same height" |
| Column.justify | center → "centred top to bottom"; end → "pushed to the bottom"; spaceBetween → "spread out top to bottom" |
| Column.align, List.align | start → "left-aligned"; center → "centred"; end → "right-aligned"; stretch → "full width" |
| Button.variant | primary (not submit) → "as the main button"; borderless → "as a plain text button"; unset on submit → "as an ordinary button" |
| ChoicePicker.displayStyle | chips → "as chips"; checkbox → "as tick boxes" |
| weight | 1 → "taking up the spare room"; 2 → "taking twice the room" |

Never generated: `h1`–`h3` and `h5` as Text variants, `body`, `Image fit: none` and `scaleDown`, `mediumFeature`, `spaceAround`, justify `stretch`, Column justify `start` and `spaceEvenly`, and Button `default`. Their coverage keys are unaffected, because `prop:` keys are per slot, not per value.

### Detail levels and the mix

- **terse:** purpose, then every piece in tree order: its content and values, plus a layout word wherever the structure isn't a plain stack ("side by side", "in a card", "a group of", "tabs", "a pop-up", "a line").
- **medium:** the same pieces, in sentences that place each one ("at the top", "below that", "at the bottom", "inside the card").
- **specific:** medium plus the phrase for every departure from the house style.

Every level is enough to rebuild the target, given the house style and the derivation rules below. The pipeline fills the tree with variety, then decides:
1. If the tree has a departure whose prop or pattern coverage key is in deficit, and the specific share would stay at or under `1 - 2 × detail_floor` (50%), the row is specific.
2. Otherwise every style prop is reset to the house style, and the row is terse or medium, whichever has fewer rows so far.

Each level has at least 3 openings and 2 joining styles, picked by the row's rng.

### What an intent never states, and how the target derives it

| In the target | Rule |
|---|---|
| input, list, bound-value and media paths | the label rule: `path_for_label(label)`, with ` icon`, ` image`, ` video`, ` audio`, ` count`, ` earliest`, ` latest` and (2026-11-03, item 7 cells) ` description` suffixes; nested values are under `path_for_label(section)`, and the intent says "in the <section> part of the data"; template paths `key_for_label(column)`; static bound items `<list>/<i>/<key>` |
| event names | `snake_case(button label)` (true for all 320 vocabulary actions) |
| event context | the form button sends every input, keyed by `key_for_label(label)`, and the intent says "that submits the form". An item button in a template list sends `{key_for_label(first column): {path: key_for_label(first column)}}`, and the intent says "that sends this item's <column>". Every other button is bare. The random display-path context is gone. |
| ChoicePicker option values | `key_for_label(option label)` |
| regexes (validationRegexp and the regex check) | from the field kind, via its phrase ("letters, numbers and basic punctuation only", "must look like an email address", …) |
| formatDate formats | the intent shows the value rendered ("shown like \"Wed, Mar 4\"") |
| formatString bodies | interpolation: `"<label>: ${path}"`, stated as "\"<label>: \" then the value"; pluralize: stated as "shown as \"3 flights\"" |
| check conditions | from the message, which is quoted |
| icon names | the icon phrase table (59 names, every phrase unique) |

Bound values are stated as "<label> from the data (now X)". Literal values are quoted.

### Item button

A template list item gets an item Button (label from an unused topic action) with a probability that rises with the deficit of `action:event_context` (base 0.2, 0.6 in deficit). A static item never gets one.

### Fit filter

A skeleton's needs are inferred from its required blocks (`field`, `kv` and `stats` need fields of a fitting kind; `list` needs an entity with a non-boolean column; `choices` needs a choice group; `submit` and `actions` need actions). An optional `requires:` list adds semantic needs from a fixed set: `money_field`, `date_field`, `secret_field`, `plural_entity`. Unknown requirements fail loading. The pipeline only draws topics that meet the skeleton's needs. A skeleton with no fitting topic in a domain doesn't use that domain. `checkout_summary` requires `money_field`.

### Size tail

`SynthConfig.tail = (0.15, (25, 40))`: 15% of draws aim at 25–40 components, the rest at 5–24. The accepted rows must have 10–20% at 25–40 components.

### Criteria → tests

| # | Criterion | Test |
|---|---|---|
| A1 | In every terse and medium row, every style prop equals the house style | `test_synth_style.py::test_terse_medium_follow_house_style` |
| A2 | In every specific row, every departure's phrase appears in the intent, and no unphrased value appears anywhere | `test_synth_style.py::test_specific_states_departures`, `::test_no_unphrased_values` |
| A3 | Over a run of 300: terse ≥ 25%, medium ≥ 25%, specific ≤ 50% | `test_synth_pipeline.py::test_detail_floors` |
| A4 | No synthetic intent, outside its quoted strings, has a catalog component name, a 2d prop word, a `/path`, a backtick or an event name | `test_synth_intents.py::test_synth_intents_in_user_register[detail]` |
| A5 | S9 as amended: every literal text and data value appears, with the phrased forms in the table above; paths and event names are not checked | `test_synth_intents.py::test_required_identifiers_present[detail]` (amended), `::test_phrased_literal_missing_is_a_gap` |
| A6 | Every path in the target is derived by the rules above from a label, section or column stated in the intent | `test_synth_intents.py::test_paths_follow_label_rule` |
| A7 | Every event name is `snake_case(label)`, every event context is a form context or an item-button context | `test_synth_style.py::test_event_rules` |
| A8 | Every style value has a phrase or is listed as never generated; every Icon enum name has a phrase; phrases are unique per prop and across icons | `test_synth_phrases.py::test_tables_complete`, `::test_phrases_unique` |
| A9 | Over 300 rows, each level shows at least 3 different openings | `test_synth_intents.py::test_openings_vary` |
| A10 | `requires:` is parsed; an unknown need fails loading; the pipeline never pairs a skeleton with a topic that lacks its needs | `test_synth_skeletons.py::test_requires`, `test_synth_pipeline.py::test_fit_filter` |
| A11 | 10–20% of accepted rows have 25–40 components | `test_synth_pipeline.py::test_size_tail` |
| A12 | `action:event_context` is reached in a 600-row coverage run | `test_synth_coverage.py` (amended) |
| A13 | The existing suite passes | `uv run pytest` |

### Decisions (A2 build)

- Style variety is generated as now (coverage-weighted), but only from phrased values. Normalising to the house style happens after the detail choice and before validation, so dedup, fingerprints and coverage see the final target.
- `DETAILS` keeps its order. The `detail` field on rows stays.
- Bound icons are stated as "a <phrase> icon from the data"; the data value is the catalog name.
- The purpose opening names the screen kind (form, dialog, list, media or detail) as now, in one of the openings.
- Checkpoint (b) is re-run after the refute: a coverage report on 3000 train and 200 dev rows, plus 10 samples with 3 per level. Above 6000 rows needed, we revisit.
- **Amended during the build (2026-11-02), before the refute:**
  - Skeleton-fixed props (`props:` in a skeleton) are style props like any other: stated at specific, reset to the house style at terse and medium.
  - `SynthConfig.always_set` gains `Row: align` and `Image: fit`, since the house style always sets them. Patterns without them leave the coverage universe.
  - Other values never generated, because they are the catalog default with no visible difference: `TextField.variant: shortText` (text, email, date and time fields leave the variant unset), `List.direction: vertical`, and `Divider.axis` outside a Row (a Divider sets `axis: vertical` only inside a Row).
  - A fill with no allowed pattern is a `FillError` (`no_pattern`), not a fallback that could break the rules. The form button may still fall back to a modal trigger. A form whose only buttons are item buttons is a `FillError`.
  - Nested sequences of two or more pieces are put in parentheses at every level, so where a group ends is never ambiguous.
  - formatDate is shown on one fixed example date (2026-03-04), not on the bound value, so a template list's format is stated once.
  - **Bug fixed (regression test first):** bound static-list siblings drew their own format arguments (date pattern, decimals, grouping, currency). They now share them per column (`test_synth_plausibility.py::test_static_list_siblings_share_format_args`).
  - **Divider in a Row:** no train skeleton put a divider inside a Row, so `prop:Divider.axis` became unreachable. `dashboard_grid` gains an optional divider between its two cards, and open slots inside a Row pull dividers by that key.
  - **Size tail:** with draws alone, only 2–5% of rows reached 25 components, because only 4 train skeletons can grow that large. It is now a quota. While accepted tail rows are under 15%, the next draw aims at the tail, may grow open slots to twice their count, prefers skeletons that have reached the tail before, and is rejected (`tail_miss`) if it falls short. After 5 tail draws (`TAIL_PROBES`), a skeleton stays a tail candidate only while at least 10% of its tail draws hit (`TAIL_MIN_RATE`). (Round 1 regression: one early hit kept `search_results` a candidate forever, `test_tail_quota_drops_rare_hitters`.) The cost is about 20% more attempts. **Tail rows come almost only from faq_sections, detail_sheet, checkout_summary and tabbed_detail.** A regression test covers a skeleton that can never reach the tail (`test_tail_quota_skips_small_skeletons`).
  - A12 is checked by the existing 400-row coverage run (`test_every_key_reached`), not a separate 600-row run.
- **Refute round 1 (2026-11-02): FAIL, all BLOCKER/MAJOR fixed with a regression test first.**
  - The tail quota is capped as well as floored: every accepted row of 25 or more components counts, and at quota an untargeted draw that lands in the tail is rejected (`tail_over`). This holds on dev too (`test_size_tail_dev`).
  - A `validationRegexp` is stated as "accepts <phrase>", and a regex check as "checked for <phrase>" after its message. The regex phrases are now nouns ("an email address", "digits only", …).
  - Every formatNumber is stated with "as a number", so a bare one differs from a raw value. S9 requires it.
  - `ChoicePicker.filterable` is set only when true (4 or more options). `false` is never generated.
  - A node's phrases are bracketed right after its own words ("… (taking up the spare room)"). A kv or icon row with phrases of its own reads "the pair (…): A next to B", and a stat reads "a stat (…): …". The tabs list, the records list and static items are bracketed as a group.
  - S9 also requires a CheckBox's state ("ticked", "not ticked" or "left empty") and, for every event, a quoted label whose snake_case is the event name (this covers icon buttons).
  - Medium and specific get a second joining style ("…, followed by …, and finally …").
  - Openings use the right article ("an events app").
  - Build B (built in parallel, `docs/spec-build-b.md`) is merged into the working tree, and the S8 linter runs in the pipeline after validation (`Reject("lint")`). On 400 train and 150 dev rows: 0 train and 2 dev rejections, both `body_words`.
  - Deferred: the fit filter ignores the minimum counts of open slots, which only costs attempts. Rejected: "an email field" stays, because users say it and it is true.
  - **Noted:** the detail mix runs at the cap, about 25/25/50. A departure key is almost always hungry, so it is "coverage-steered" only in that it is capped. Specific rows also carry many weights. Both should be judged on the checkpoint (b) samples.

### Non-goals

- Gallery rows don't change.
- No LLM rewording (2c-3).
- No new skeletons or vocabulary.

## Checkpoint (b), re-issued (2026-11-02)

- 3640 train and 200 dev synthetic rows: every coverage key reaches N=30 (0 below). The tail share is 15.0%. The detail mix at full size is terse 1593, medium 1593 and specific 454: once coverage is met, departures stop being hungry, so specific falls to about 12% (the 25/25/50 seen in 300-row tests is a small-run effect).
- `export --mix 0.7 --max-seq-len 8192`: 5199 rows (3639 synthetic plus all 1560 gallery rows once), tokens max 6882, p50 5388, p95 5958. 1 synthetic train row dropped for matching a synthetic dev fingerprint.
- Two bugs found while reading the samples, each fixed with a regression test first (`test_synth_plausibility.py`): pluralize stripped a bare "s" ("1 itinerarie", "1 classe": 29 rows), and bound media of one kind shared one label-rule path, so a surface repeated one file (176 rows). Irregular plurals no longer get pluralize, and a second bound media of a kind shows its own literal URL.
- Known and not fixed (design, not bugs): coherence is word-level. Any topic action can be the submit button, media is labelled with the topic name, and titles don't always fit the screen kind.
- **Approved by the user (2026-11-02):** 10 of 10 in review mode (`--sample 10 --seed 2026`, verdicts in `data/synth/review.jsonl`). The word-level coherence limits are accepted for SFT run 1.

## SFT run 1: decisions (user, 2026-11-02)

- **Mix:** about 3640 synthetic train rows, so that `export --mix 0.7` uses every one of the 1560 gallery rows exactly once (no repeats, none cut).
- **Training loop:** our own small loop, not the cookbook script's same-file holdout. It saves a sampler checkpoint about every 25 steps and scores the separate dev split (gallery and synthetic reported apart). The checkpoint is picked by dev score, not loss.
- **Hyperparameters:** LoRA rank 16, LR 5e-5, batch 16, 1 epoch. `--max-seq-len` is set explicitly from the export's token report.
- **The system prompt is frozen before launch.** Changing `build_system_prompt` later invalidates the adapter.
- Prep notes (not committed): the Fireworks prep document in the session scratchpad. Training is free for jobs started before 2026-12-01 Pacific; eval sampling is billed.

## SFT run 1: prompt freeze and trainer (spec, 2026-11-02)

Decision: [ADR 0015](../docs/adr/0015-our-own-sft-loop-in-a-separate-trainer-project.md). User choices (2026-11-02): a dev subset on every checkpoint and full dev on base plus the best 2; a separate trainer project; per-token mean loss.

### Behavior

Prompt freeze (main env, `a2ui_eval`):
- `data/system_prompt.txt` holds the exact output of `build_system_prompt(sdk)` on `catalogs/basic_v0_9.json` (no trailing newline added). `data_cli freeze-prompt` writes it. It is committed.
- `export` refuses (ExportError) when `build_system_prompt` differs from the frozen file, unless the file doesn't exist yet.

Dev prompts (main env):
- `data_cli export-dev` writes `exports/sft_dev.jsonl`. It has one row per dev case: the 84 gallery rows (`data/dev`) and the 200 synthetic rows (`data/synth/dev`). Each row has `{id, source: "gallery"|"synthetic", subset: bool, messages: [system, user]}`, with the system text from the frozen file.
- `subset` marks a fixed 64 rows: 32 gallery and 32 synthetic, chosen by `random.Random(seed).sample` over id-sorted rows, with seed 0. The same input always gives the same subset.

Scoring (main env), `python -m a2ui_eval.checkpoints score <predictions.jsonl> --out <scores.json>`:
- Predictions rows: `{id, checkpoint, text, finish: "stop"|"length"|"error"}`. `text` is null on error.
- Each id is looked up in the dev cases and scored with `score_response`. `finish == "length"` is passed as the finish reason, so truncation is detected.
- The summary is per checkpoint and per source: rows, compiles rate, exact_match rate and mean row slot F1, plus `selection`. `selection` is the mean of the gallery and synthetic mean row F1 (macro over the two sources), so the 200 synthetic rows don't swamp the 84 gallery ones. An unknown id or a missing dev row raises an error.
- `selection_ci` is a 95% percentile bootstrap interval for `selection`: 2000 resamples, `random.Random(0)`, with rows resampled with replacement within each source (keeping its row count), so each resample is weighted like `selection`. The scores file records the settings in `bootstrap`, and the CLI's stdout line carries `selection_ci`. The trainer ignores it; selection still uses the point value. Added after run 1, where the 64-row subset ranked `s250` and `s225` 0.007 apart and full dev put them 0.03 apart.

Trainer (`glimmer-tune/trainer/`, its own uv project, Python 3.13, never imports `a2ui_eval`):
- Pins `fireworks-ai[training]==1.2.30` and the cookbook at commit `57968a2` from git, as an exact pin with the lock committed.
- `python -m glimmer_trainer prepare --train ../exports/sft_train.jsonl --dev ../exports/sft_dev.jsonl --prompt ../data/system_prompt.txt --max-seq-len 8192` is local only: it loads the tokenizer and makes no Fireworks call. It:
  - renders every row with the `muse_glimmer` renderer
  - checks that every system message equals the frozen prompt and that no rendered prompt contains `Current date:`
  - checks that every train row has at most `max-seq-len` tokens and positive weights
  - prints rows, steps, the token max/p50/p95 and the planned checkpoints
  - prints an estimate of sampled rows

  It exits non-zero on any failed check.
- `python -m glimmer_trainer train ... --run-id <name> --launch` is the only remote command. Without `--launch` it runs `prepare` and stops. With it:
  1. Opens a serverless LoRA training client: base `accounts/fireworks/models/muse-glimmer-30b`, rank 16, alpha 32, MLP/attention/unembed on.
  2. Saves sampler checkpoint `s000` (base) before step 1.
  3. Trains 1 epoch with batch 16, shuffled with `random.Random(0)` and keeping the last short batch. It uses Adam with lr 5e-5, betas 0.9/0.999, eps 1e-8, weight decay 0 and grad clip 1.0.
  4. Builds datums the cookbook way (`build_supervised_example`, `LAST_ASSISTANT_TURN`, inputs and targets shifted by one). Each assistant-token weight is `1 / (response tokens in the batch)`.
  5. Logs every step to `runs/<run-id>/metrics.jsonl`: step, loss (`loss:sum`), response tokens, grad norm metrics and seconds.
  6. Saves sampler checkpoint `sNNN` every 25 steps and at the last step. Names are 4 characters.
  7. After each save, samples the subset rows for that checkpoint in a background worker pool while training continues. Sampling is greedy (temperature 0) with max tokens 3072 and the renderer's stop tokens. Results go to `runs/<run-id>/predictions/<ckpt>.jsonl`. The text is `get_text_content(parse_response(...))`, and `finish` is "length" when the termination is not clean.
  8. When training ends and every subset sample is in, it scores each checkpoint by subprocess: `uv run --project <glimmer-tune> python -m a2ui_eval.checkpoints score`. It picks the 2 best checkpoints other than `s000` by `selection` (ties go to the earlier step).
  9. Samples all dev rows for `s000` and those two, then scores them. The full sample file is `<ckpt>.full.jsonl`.
  10. Promotes the full-dev winner (other than `s000`) as `glimmer-a2ui-<run-id>-<ckpt>`, then deletes the training session.
- `runs/<run-id>/manifest.json` is written before training and updated at each stage. It records the config, the session and run ids, the checkpoint paths, the scores, the winner and the promoted model. It holds no key.
- On any exception after the session opens, the run writes the error to the manifest and **does not delete the session**, so its checkpoints can still be promoted by hand. It prints the session id.
- The API key comes only from the `FIREWORKS_API_KEY` env var. It is never logged or written. `trainer/.env.example` documents it.

### Criteria → tests

| # | Criterion | Test |
|---|---|---|
| T1 | `data/system_prompt.txt` equals `build_system_prompt(sdk)` byte for byte | `tests/test_prompt.py::test_frozen_prompt_matches` |
| T2 | export refuses a prompt that differs from the frozen file (`checked_system_prompt`, used by `export` and `export-dev`) | `tests/test_prompt.py::test_export_refuses_unfrozen_prompt` |
| T3 | dev export: 284 rows, 32+32 subset, deterministic, frozen system text, no assistant turn | `tests/test_checkpoints.py::test_export_dev_rows_and_subset` |
| T4 | score summary per source, `selection` = macro mean of row F1; truncation via `finish: length`; error row scores 0 | `tests/test_checkpoints.py::test_score_summary_per_source` |
| T5 | unknown id or missing dev row raises | `tests/test_checkpoints.py::test_score_rejects_unknown_and_missing` |
| T5b | `selection_ci`: collapses when every row is equal; stratified by source; brackets `selection`; seeded; narrows with more rows; one source and no rows work; written to the scores file and stdout | `tests/test_checkpoints.py::test_selection_ci_*`, `::test_score_output_carries_selection_ci` |
| T6 | batches: seeded shuffle, size 16, last short batch kept, every row once | `trainer/tests/test_loop.py::test_batches` |
| T7 | datum weights sum to 1 per batch, only on assistant tokens, inputs and targets shifted | `trainer/tests/test_loop.py::test_datums_mean_weights` |
| T8 | checkpoint schedule: s000, every 25 steps, final step; names ≤ 17 chars | `trainer/tests/test_loop.py::test_checkpoint_schedule` |
| T9 | full fake run: order of calls (save s000 before first step, Adam params, subset sampled per ckpt, top-2 full, promote winner ≠ s000, delete session last), files written | `trainer/tests/test_loop.py::test_run_end_to_end_with_fakes` |
| T10 | an exception mid-run records the error and does not delete the session | `trainer/tests/test_loop.py::test_failure_keeps_session` |
| T11 | `prepare` fails on a system prompt that isn't frozen, a `Current date:` in a prompt, an over-length row | `trainer/tests/test_prepare.py` |
| T12 | without `--launch`, `train` creates no Fireworks client (the tokenizer may still be fetched from Hugging Face) | `trainer/tests/test_cli.py::test_train_without_launch_is_local` |
| T13 | the key is never written to the manifest, predictions, metrics or stdout; error text is redacted | `trainer/tests/test_loop.py::test_key_not_leaked` |

Trainer tests use fake service, training, sampler and renderer objects. They never import the Fireworks client network path, never load a real tokenizer, and run with sockets blocked (a `conftest.py` that patches `socket.socket.connect`).

### Amendments from the build (2026-11-02)

- **Full dev reuses the subset answers.** Sampling is greedy, so a finalist's full file is its 64 subset predictions plus the other 220 rows, sampled once. That saves about 190 samples.
- **Sampling failures are per row.** After retries at 2 s and 8 s, a row is recorded as `finish: "error"`, and it scores 0. The manifest records `sample_errors` per checkpoint (and per `<ckpt>.full`). Above 25% error rows in one checkpoint, the run stops with the session kept, because that score would be noise. A training or save failure aborts the run the same way (T10). On any failure, queued sampling is cancelled rather than waited for.
- **`finish` comes from the SDK's `stop_reason`** (`"length"` or `"stop"`), not from `parse_response`'s termination. Muse's parser reports MALFORMED whenever `<|eot|>` is missing from the returned tokens, which may just mean the server stripped the stop token.
- **Promotion** matches the checkpoint by the last segment of `checkpointName` or `name`, because the SDK documents only `name` (`pick_checkpoint`, tested with rows shaped like the SDK's). A missing session name fails fast. If deleting the session fails after promotion, it is recorded as `delete_error` and the run still counts as done.
- **`--launch` needs an explicit `--run-id`.** Unexpected errors print redacted to stderr. A run directory that is not empty is refused.
- **Scorer fix found by T4:** `score_response` gave a missing response slot F1 1.0, because both sides were empty. It now counts the reference's slots, so F1 is 0 (`tests/test_score.py::test_failed_request_scores_zero_slot_f1`). For `expect_ui: false` cases (none in dev yet) a missing response still has 0 reference slots. That gets revisited with 2c-2.
- **Run ids** match `[a-z0-9][a-z0-9-]{0,39}`, so `glimmer-a2ui-<run>-sNNN` fits Fireworks' 63-character model id. A run directory that already exists is refused.
- **Train input** is `exports/sft_mix.jsonl` (`export --mix 0.7`). The real `prepare` on 2026-11-02 found 5199 rows, 325 steps, 14 checkpoints (s000 to s325), rendered tokens max 6843 / p50 5349 / p95 5919, and an estimate of 1556 samples. It found no date and one `Reasoning strength: low.` line.

### Decisions and non-goals

- Selection uses the literal slot F1. The path- and event-blind score is milestone 3 work and not part of this run. Base and tuned are compared on the same scorer, so the comparison stays fair.
- No automatic resume. A crashed run is promoted by hand from the manifest's checkpoint list.
- No LR schedule and no warmup, as in the cookbook starter.
- No deployment. Serving the adapter is a later, paid decision.
- Sampling during training uses a separate sampling client per checkpoint (`create_sampling_client(model_path=...)`). Its effect on step time is logged, not tuned.
- The test split stays untouched. The trainer reads only train and dev exports.

### Test command

```bash
cd glimmer-tune && uv run pytest
cd glimmer-tune/trainer && uv run pytest
```

## Test evaluation (spec, 2026-11-02)

The test split (136 gallery rows, 8 families, frozen in `c868bfa`) is spent **once**, on the final candidate and the base. It is not used to choose run settings or checkpoints. Run 1's `s250` is not that candidate until we decide there is no run 2.

- `data_cli export-test` writes `exports/sft_test.jsonl`: one row per test case, id-sorted, `{id, source: "gallery", family, subset: false, messages: [system, user]}`, system text from the frozen file.
- `python -m glimmer_trainer sample --run runs/<run> --ckpt <name> --cases <export> --out <pred.jsonl> [--limit N]` samples one saved checkpoint of a finished run, by the sampler path in its manifest. Sampling is greedy; max tokens is the manifest's `config.max_tokens` unless `--max-tokens` overrides it. Before creating any client it checks that `--out` doesn't exist, the checkpoint name is in the manifest, `--limit` is at least 1 and the cases file has rows. `--out` is opened with exclusive create, so it is never overwritten. It writes error rows and exits 1 if any row errored.
- `checkpoints score <pred> --out <scores> --split test` needs a prediction for every one of the frozen test cases (`data/test/cases.jsonl`), whatever export `--dev` names. Rows carry `family`. `selection_ci` is a family-level bootstrap (whole families resampled with replacement, row mean of the draw; `bootstrap.clustered_by: "family"`), because rows of one family are correlated and 8 families is few.
- `checkpoints compare <a.json> <b.json>` prints the paired difference b − a in mean row slot F1 and its family-level interval. Both files must have `split: "test"`, and each must cover exactly the frozen test ids.
- Known limit: the interval covers family-level variation only, not sampling noise. Greedy sampling on the shared pool is not bit-exact (see the probe), and each test score comes from a single sampling pass.

Procedure: `export-test`, sample `s000` and the candidate, score both with `--split test`, `compare` them, and record the result here.

Probe (2026-11-02): `sample --limit 1` on dev row `dev/03_calendar-day/0005` against run 1's `s250` sampler path, after the session was deleted, returned a clean answer. So sampler checkpoints survive session deletion. The text differed from run 1's stored answer only in one component id (`actions_row` → `actions`), so greedy sampling on the shared pool is not bit-exact. Both score slot F1 0.765.

Tests: `tests/test_checkpoints.py::test_export_test_rows`, `::test_score_test_split_uses_test_references`, `::test_family_ci_resamples_whole_families`, `::test_family_ci_brackets_the_row_mean`, `::test_compare_ci_is_paired_by_row_and_clustered_by_family`, `::test_score_and_compare_cli_on_test`; `::test_family_ci_takes_the_row_mean_of_each_draw`, `::test_score_test_needs_every_frozen_test_row_even_with_a_partial_export`, `::test_compare_cli_rejects_dev_partial_and_mismatched_files`; `trainer/tests/test_sample.py`.

Refuter round (2026-11-02): FAIL with two blockers. The row-mean statistic was untested (a family-means mutant survived), and a partial `--dev` export bypassed the every-row guard. Both are fixed, with tests that fail on the old code or the mutant. The minor findings were fixed too: compare CLI rejection tests, max tokens from the manifest, exclusive create, `--limit` and empty-file checks, and redaction on client-creation failure.

## Name-blind slot score (spec, 2026-11-02)

Status: decided by the assistant on the user's delegation (2026-11-02). [ADR 0021](../docs/adr/0021-name-blind-slot-score-uses-placeholders.md).

Intents never name data paths or event names (R1–R3, ADR 0011). A correct answer that picks other names still loses literal slot F1. This adds a second score, **name-blind slot F1**, reported next to the literal one. Selection does not change.

### Files

| File | Change |
|---|---|
| `a2ui_eval/align.py` | `triples(..., blind=False)`; `blind_leaf(prop, value)`; `Alignment.blind_score` |
| `a2ui_eval/score.py` | `_data_triples(..., blind=False)`; `result["slot_blind"]`, set wherever `slot` is set |
| `a2ui_eval/checkpoints.py` | rows carry `slot_f1_blind`; sources carry its mean; top-level `selection_blind` (same macro mean); in the CLI stdout line |
| `tests/test_score.py`, `tests/test_checkpoints.py` | tests below |

### Behaviour

Blinding rewrites a component triple `(type, prop, value)` before comparison:
- **Paths:** a leaf whose last key is `path` or `@path` (bindings, relative template bindings, a child-list template's `path`) gets the value `"<path>"`.
- **Event names:** a leaf whose prop ends in `event.name` gets the value `"<event>"`.
- **Event context keys:** the key after `.context.` in a prop becomes `*`.
- **Interpolations:** in a string value, each bare-path `${...}` (the `structure._INTERP` pattern) becomes `${<path>}`.
- **Data model:** a `$data` triple keeps its value, and its pointer becomes `"<path>"`. The `$op` triple is unchanged.

Everything else stays literal: component types, literal text, enums, function `call` names and child references. The alignment is the literal score's alignment, and blinding changes no triple counts, so `n_pred` and `n_ref` match the literal score. Failed, empty and non-compiling responses get `slot_blind` equal to `slot`.

### Criteria → tests

| # | Criterion | Test |
|---|---|---|
| N1 | Renaming a bound path (absolute, relative in a template, a template's list path, an interpolation) leaves blind F1 at 1.0 and lowers literal F1 | `test_score.py::test_blind_ignores_path_names[case]` |
| N2 | Renaming an event name or a context key leaves blind F1 at 1.0 and lowers literal F1 | `test_score.py::test_blind_ignores_event_names` |
| N3 | Data written under a different pointer with the same value: blind F1 1.0. Same pointer with a different value: blind F1 < 1.0 | `test_score.py::test_blind_data_model_keeps_values` |
| N4 | Literal text changes and a binding replaced by a literal still lower blind F1 | `test_score.py::test_blind_still_scores_text_and_binding_kind` |
| N5 | A failed response has `slot_blind == slot`, with F1 0 and the same `n_ref` | `test_score.py::test_failed_request_scores_zero_slot_f1` (extended) |
| N6 | The literal `slot` is unchanged by this work: every existing scorer test passes as-is | existing suite |
| N5b | `slot_blind` equals `slot` (a separate dict) for non-compiling, unextracted and text-only answers; a missing reference surface adds the same `n_ref` to both | `test_score.py::test_blind_parity_on_every_early_return_and_missing_surface` |
| N7 | Score files carry `slot_f1_blind` per row and per source, and `selection_blind` (the macro mean over sources, from `slot_blind`). `selection` is unchanged | `test_checkpoints.py::test_score_reports_blind_next_to_literal`, `::test_blind_selection_is_wired_from_slot_blind_as_a_macro_mean` |

### Decisions and non-goals

- Placeholders, not a learned one-to-one renaming. A model that binds two fields to one path is not caught; `bindings_valid` and the reading of failures cover that. Revisit only if reading failures shows it matters.
- Report-only: checkpoint selection stays on literal slot F1 until the run 1 rescore has been read. The run 2 selection score is decided then.
- No interval on `selection_blind` yet. Add one if it becomes the selection score.
- `exact_match` stays literal.
- v1 catalog shapes are blinded by the same rules (`@path`). Eval data is v0.9.
- The interpolation rule is `structure._INTERP` as it stands. It also matches identifier-like tokens (`${5}`, `${true}`, `${a.b}`), so "Price ${5}" and "Price ${9}" compare equal. Nested bare paths inside an expression interpolation (`${formatDate(value: ${/d}, ...)}`) are blinded, and the outer expression stays literal.
- All event context keys become `*`, so the pairing of keys with values is lost (`{a: $/x, b: $/y}` equals `{a: $/y, b: $/x}`). This is accepted with the placeholder choice.
- The event rule matches the key `event.name` on a key boundary only.

Refuter round (2026-11-02): PASS-WITH-NOTES, with no blocker. N6 was checked on all 1748 run 1 predictions: literal `slot_f1`, `selection` and `selection_ci` are identical to HEAD. Fixed:
- The N7 test couldn't tell blind from literal (MAJOR). The new wiring test kills the refuter's mutants.
- Untested parity paths.
- `slot` and `slot_blind` sharing one dict.
- The event-name match boundary.

Deferred: `align.py` imports the private `structure._INTERP`.

### Rescore of run 1

Every file in `runs/sft1/predictions/` is rescored locally into `runs/sft1/rescore-20261102/` (git-ignored; the original `scores/` stay untouched). A sub-agent reads the 20 lowest name-blind gallery rows of `s250.full` and sorts each into model error or scorer artefact.

### Test command

```bash
cd glimmer-tune && uv run pytest tests/test_score.py tests/test_align.py tests/test_checkpoints.py
cd glimmer-tune && uv run pytest
```

## Run 2 preparation: measurement (spec, 2026-11-02)

Status: built from the reviewer brief of 2026-11-02, with decisions delegated to the assistant. The user dropped Muse Wonder and Gemini baselines.

### Decisions (assistant, on the user's delegation)

- **Name-blind score:** ADR 0021's placeholders stay. A consistent one-to-one renaming would only add a check for two inputs sharing one path. Across the compiled rows of `s225.full` and `s250.full` (526 rows) there are 0 such cases, and 1 in base. Not built.
- **Run 2 scope:** generic only (basic catalog, the current intent input). The explorer-shaped run (Brief plus Result schemas in, Macros out; ADR 0014) is run 3. Its Brief wording and Macro catalog are not specified yet, and the explorer session owns them. Run 3 must start before the free SFT cut-off (2026-11-30).
- **Prompt change for run 2:** only append one line after the SDK snippet. Rule 7's heading comes from the upstream SDK (`a2ui/inference_formats/express/prompt_generator.py`); renaming it would fork upstream text that the explorer must send byte-identically. Not done.
- **Grammar baselines: dropped.** The training SDK's `SamplingParams` has no grammar option (fields: `max_tokens`, `seed`, `stop`, `temperature`, `top_k`, `top_p`). Grammar decoding would need serverless inference (Glimmer's serverless status is unclear) or an on-demand deployment (H100 $8/h). Constrained decoding isn't a serving candidate until a serving path is chosen.

### Run 2 decisions (grilling round, 2026-11-02; the user delegated, then confirmed)

- **Per-row state:** ADR 0022 accepted. In the explorer, SQL computes a Tone column. Generic run 2 references store presentation values per item (`{icon: "check"}` with `Icon($icon)`), the only way the basic catalog can express it.
- **Baselines:** run once, under the frozen run 2 prompt: base, and base plus 3 few-shot examples (3 train gallery rows from different families, seed 0). Sampled tokens are recorded per row. Hard cap US$15.
- **Selection:** run 2 selects on name-blind slot F1. The outside-the-catalog rate is reported, not gated. ADR 0021 is amended.
- **The user's reading of the 84 gallery rows** doesn't block run 2 data. Verdicts feed in before full-size generation.
- **2c-2 deferred** (update turns and text-only rows). The explorer never updates a Surface (one new Surface per Turn), and Glimmer never writes text-only answers there.
- **Timeline:** run 2 launches by 2026-11-12. The run 3 spec (Brief and Macros) comes from the explorer session by 2026-11-20. Run 3 launches by 2026-11-25. Free SFT ends 2026-11-30.
- **Item 7:** coverage per (component, property) × (literal | absolute `$/path` | per-item `$field` in a template), with at least 30 train rows in every cell the schema allows. Per-item rows bind an icon, image, variant or text. State intents ("completed steps") have references that store per-item presentation values. The coverage report and 10 rendered samples come before full-size generation.
- **Item 8:** append one line after the SDK snippet, "Express has no conditionals or operators. Values that vary per item belong in the data model.", and freeze it under ADR 0015. Rule 7's upstream heading stays. Run 1 becomes historical.
- **Item 9:** run 1's settings, except:
  - unembed off, with every `train_*` flag recorded in the manifest;
  - name-blind selection, with the outside-the-catalog rate in subset scoring;
  - sampled tokens recorded per row;
  - `service.close()` called at the end.
  - Still 1 epoch, with a checkpoint every 25 steps.
- **Item 10:** amend ADR 0003 for ADR 0009's exception, and resolve the report's source conflicts. The Meta Model API terms are out of scope (user).

### Behaviour

- **Score files** carry `selection_blind_ci` (the same bootstrap as `selection_ci`, on `slot_f1_blind`; family-level on test) and `scorer: {commit, dirty}` (git HEAD, and whether `a2ui_eval/` had uncommitted changes).
- **Outside-the-catalog check** (`a2ui_eval/catalog_check.py`). A lexer follows `Express.g4`'s tokens. It flags:
  - `operator`: any character sequence the grammar can't tokenise (`?` outside a check rule, comparisons, `==`, `&&`, `||`, `!`, arithmetic, `|>`), and a signed number right after a value (`$a -1`). Also operators inside `${...}` in non-raw strings, which compile but fail at render time.
  - `unknown_function` / `unknown_component`: a call or `?check` whose name isn't a catalog function, a catalog component, or one of `surface`, `deleteSurface`, `Event`, `_template`.
  - `unknown_property`: a keyword argument that the callee doesn't accept (its catalog slots or args, plus the compiler's reserved keywords).
  - Not flagged: `/` or `-` glued to names on both sides (`$/a b/c`, `$/a-b`). These are broken paths, not arithmetic.
- **Rows** carry `outside_catalog` and `outside_catalog_kinds`; sources carry the rate.
- **Export** refuses any train target with a finding (`export_sft(..., catalog_check=)`, always passed by `data_cli export`). The run 1 mix has 0 findings in 5199 rows.
- **Review page, prediction mode:** `python -m a2ui_eval.review --predictions P --scores S` shows gallery dev rows worst-first by name-blind F1, with reference and prediction rendered side by side. Verdicts are `ok | model | reference | scorer` with an optional note, saved next to the scores file (`S` with `.review.jsonl`).

### Criteria → tests

| # | Criterion | Test |
|---|---|---|
| P1 | `selection_ci` and `family_ci` take the score key | `test_checkpoints.py::test_selection_ci_takes_the_score_key` |
| P2 | Score files carry `selection_blind_ci` and the scorer version; no git gives `None`s | `test_checkpoints.py::test_score_output_carries_blind_ci_and_scorer_version`, `::test_scorer_version_without_git` |
| P3 | Legal Express (strings, raw strings, checks, comments, interpolations, Event keywords) has no findings, and every dev reference is clean | `test_catalog_check.py::test_legal_express_has_no_findings`, `::test_every_dev_reference_is_clean` |
| P4 | Section 7's two idioms, and each operator, are flagged | `test_catalog_check.py::test_ternary_icon_from_section_7`, `::test_if_with_a_comparison_from_section_7`, `::test_operators` |
| P5 | Unknown components, functions, checks and properties are flagged | `test_catalog_check.py::test_unknown_names` |
| P6 | Broken paths are not operators (regression from the base rescore) | `test_catalog_check.py::test_broken_paths_are_not_operators` |
| P7 | Rows and sources carry the outside-catalog flag and rate | `test_checkpoints.py::test_score_reports_outside_catalog_per_row_and_source` |
| P8 | Export refuses a target outside the catalog | `test_export.py::test_rejects_targets_outside_the_catalog` |
| P9 | Prediction review: gallery only, worst first, prediction compiled or its error shown, verdicts validated and upserted, served over HTTP | `test_review_predictions.py` |

### Rescore

All 17 run 1 prediction files are rescored into `runs/sft1/rescore-20261102b/` (git-ignored). Literal and blind selection match `rescore-20261102/` exactly. They were rescored after commit `34fcfbd`, so the scorer version they record is clean.

## Item 7 amendment: fixes from the s250 review (2026-11-03)

Status: approved 2026-11-03 (grilling round; the user delegated the decisions, then confirmed). It widens item 7. Source: the refuter's 84 verdicts in `runs/sft1/rescore-20261102b/s250.full.review.jsonl` (model 56, ok 17, reference 5, scorer 6).

### Facts

- `required` passes on `false`: only null, undefined, `""` and `[]` count as empty (`a2ui/typescript/core/web_core/src/common/basic_functions.ts:105`). A `required` check on a CheckBox never blocks anything.
- The generator emits it on two paths: a CheckBox's own checks (`a2ui_eval/synth/fill.py:436`, the `else` branch; 42 train rows) and form-level `and`/`or` checks over the first two inputs (`fill.py:514`; 22 rows). 59 of 3640 synthetic train rows are affected (5 have both); 0 gallery train rows.
- Component checks are coerced by truthiness (web_core `generic-binder.ts`, `extractValidationResult`). `and`/`or` need at least 2 values (catalog `minItems: 2`, and `executeAnd` throws otherwise). `and([$/x])` compiles but is invalid.
- formatString function calls take named arguments only (`expression_parser.ts`, `parseFunctionCall`; spec `a2ui_protocol.md:760`): `${formatDate(value:${/t}, format:'h:mm a')}`. `${formatDate($t, 'h:mm a')}` fails at render time.
- The current train data (3640 synthetic, 1560 gallery rows) has 0 positional formatString arguments and 0 Unreachable components. The model invents both.

### Decisions

1. **Checkbox checks:** neither generator path puts `required` on a boolean input. Export refuses a `required` check, on any component and nested at any depth, whose value is a CheckBox's absolute path. Relative paths inside templates are out of scope: the generator emits only absolute input paths, and no gallery train row has one. Boolean inputs come from the catalog (`CatalogModel.boolean_inputs`: checkable, with a boolean `value`), not from a component name. Coverage targets change: a boolean input's `prop:<Component>.checks` key (`prop:CheckBox.checks`) leaves the universe, because no check is taught for it. Teaching "must be ticked" is deferred, because no clean valid form is known (`and([$/x, $/x])` is the only one).
2. **No patching:** item 7's full-size generation replaces the current synthetic set.
3. **`positional_arg` and `dollar_path`:** two new kinds for the outside-the-catalog check, inside `${...}` in a non-raw string. `positional_arg` flags a call argument with no `name:`, and a comma outside any call (arguments left after the call closed, as in `${pluralize(value: ${n}), '%', '%'}`). `dollar_path` flags a `$` path (`$t`, `$/t`): web_core's paths there are `${/t}`, `/t` or `t`. The Express compiler passes string contents through unchecked, so both fail only at render time. Not flagged: empty argument lists, nested calls, quoted commas or parentheses, a trailing comma, literals or paths with no call, and an escaped `\${`. Both kinds are reported on predictions, and export refuses them. Single-quoted Express strings are scanned too. The lexer is a heuristic that names the usual faults. **`interp_syntax`** covers the rest: any string with `${` that the SDK's port of web_core's parser (`a2ui.core.expressions.expression_parser`) rejects, when no other kind already explains it. That includes s250's `${/change}% ${from: 'from last week'}` (stats-card 0007, 0008, 0010), `${a)}` and an unclosed call. Added in the refuter's fix round. Amended in the build (2026-11-03): `dollar_path` and the stray comma were added after a researcher traced the parser (`expression_parser.ts`). On s250, they flag 4 rows (calendar-day 0002, 0009, 0011 and financial-data-grid 0002). They flag 0 of the 5200 train targets.
4. **Coverage keys `formatString:<function>`:** one per catalog value function except formatString itself (`formatNumber`, `formatCurrency`, `formatDate`, `pluralize` in v0.9). A row counts when a formatString template calls that function. The generator writes only named arguments, and the catalog check refuses positional ones at export. The target is the usual 30. The tests run at target 2; the full-size coverage report checks 30. Amended in the build: one key per function, not one `formatString:call`, because pluralize alone already satisfied a single key, while the model's failures were with formatDate. The generator offers "this formatter inside a template" (`Label: ${fn(value: ${/p}, ...)}`) as a weighted option next to the standalone formatter, and the Intent states the format.
4a. **Money labels** (a data bug found in the build): a label that matches the money words but also names another unit ("Total length hours", "Current Points Balance") is not money, so it never gets `formatCurrency`. A unit after `per` doesn't count ("Price per day" is money). Noun units count only in the plural, so "Item price" stays money.
5. **`unreachable`:** a row finding with a rate per source, like `outside_catalog`. It lists the orphan ids that `check_structure` finds, and `(no root)` for a surface with no root, since nothing is attached then. Export refuses a train row with one. Slot F1 is unchanged. Deferred to 2c-2: export checks only the row's own messages and doesn't fold `existing_surface` in first (no row has one yet). The scorer does fold it in.
6. **Verbatim strings:** an Intent's quoted string must appear on screen exactly as quoted. It may be built from bound data.
7. **Dev fixes** (dev only; test stays frozen):
   - The 5 notification-permission rows (`0000`, `0004`, `0005`, `0006`, `0009`) get Intents that state what their References have: Card, centring, icon, body variant.
   - shipping-status `0002`'s Intent names its header icon.
   - Known bias: notification-permission test scores are understated for the same reason.
   - Done 2026-11-03 in `data/intents_draft.jsonl`, then `build-eval --split dev`. Only these 6 Intents changed; the References are untouched. Each Intent now names its icon with the house phrase (tick, shopping cart, map pin, bell, info) and asks for a centred card, body text and buttons side by side. "Yes and No options" became "buttons", because it read as a choice picker. Written by the assistant on the user's delegation, so they await the user's read.
8. **Verdict changes:** shipping-status `0002` goes from scorer to model, because templates over data are the wanted behaviour. The 5 stats-card scorer verdicts stand: a literal equal to the rendered bound text is a known false negative, and the scorer is not changed before run 2.
9. **Reference version:** score files carry `references: {commit, dirty}` for `data/dev`, `data/synth/dev` and `data/test`. The synthetic dev set is half of every dev score, and decision 2 replaces it (refuter finding). **No rescore** (amended 2026-11-03): decision 7 changed Intents, not References, and slot F1 compares with the Reference, so rescoring run 1 and s250 would reproduce the same numbers. Their predictions for those 6 rows answered the old Intents. The effect of the fix shows when run 2 is sampled on the new ones.
10. **Priority** if time runs short before the 2026-11-12 launch: 1, 3–5, then 7–9. Decisions 7–9 don't affect training data.

### Criteria → tests

| # | Criterion | Test |
|---|---|---|
| R1 | A synthetic row never has `required` on a CheckBox's own checks (regression) | `test_synth_pipeline.py::test_checkbox_never_gets_required` |
| R2 | A form-level `and`/`or` check never uses `required` on a boolean input (regression) | `test_synth_pipeline.py::test_form_check_skips_boolean_inputs` |
| R3 | Export refuses `required` on a CheckBox path: its own check, a form-level `and`, a nested condition | `test_export.py::test_rejects_required_on_checkbox` |
| R3a | Boolean inputs come from the catalog | `test_synth_catalog.py::test_boolean_inputs` |
| R4 | Positional arguments, stray commas and `$` paths inside `${...}` are flagged | `test_catalog_check.py::test_positional_args_in_format_string` |
| R4a | Legal forms are not flagged: named arguments, empty argument lists, nested calls, quoted commas, no call | `test_catalog_check.py::test_named_args_in_format_string_are_legal` |
| R5 | Unreachable components are flagged per row, with a rate per source | `test_checkpoints.py::test_score_reports_unreachable_per_row_and_source` |
| R6 | Export refuses a target with an Unreachable component | `test_export.py::test_rejects_unreachable_components` |
| R7 | Every `formatString:<function>` key is reached, and those rows pass the catalog check | `test_synth_coverage.py::test_format_string_call_key` |
| R7a | Row keys count a call inside a formatString template, and not a standalone call | `test_synth_coverage.py::test_format_string_call_row_keys` |
| R7b | Labels that name another unit are not money, but "per <unit>" labels are (regression) | `test_synth_plausibility.py::test_money_labels_have_no_other_unit` |
| R7c | Every `${...}` string in generated references parses with the SDK's port of web_core's parser | `test_synth_coverage.py::test_generated_templates_parse_in_web_core` |
| R7d | The Intent states a nested formatter's format | `test_synth_coverage.py::test_intent_states_a_nested_format` |
| R8 | Score files carry the references version | `test_checkpoints.py::test_score_output_carries_references_version` |
| R8a | The references version tracks every eval source, including synthetic dev | `test_checkpoints.py::test_references_version_tracks_every_eval_source` |
| R5a | A rootless surface is reported as `(no root)`, by the scorer and by export | `test_checkpoints.py::test_a_rootless_surface_is_unreachable`, `test_export.py::test_rejects_unreachable_components` |

## Run 2 items 8 and 9: build (2026-11-03)

### Item 8, the prompt line

- `a2ui_eval/prompt.py`: `RUN_2_RULE` goes between the upstream snippet (byte-identical) and the reasoning line.
- `data/system_prompt.txt` was re-frozen with `freeze-prompt --force`, adding 2 lines. Run 1's adapter is now historical (ADR 0015).
- Every export after this freeze carries the new prompt. The current synthetic set can't be exported anyway until it is regenerated (decision 2 of the item 7 amendment).

### Item 9, trainer settings (`trainer/glimmer_trainer/loop.py`)

- **LoRA targets:** `Config.train_mlp`, `train_attn` and `train_unembed` default to on, on and **off**. They are passed to `create_lora_training_client` and recorded through `manifest.config`.
- **Selection:** `SELECTION_KEY = "selection_blind"` drives the subset ranking, the finalists and the winner, and the manifest records it as `selection_key`. `subset_scores` and `full_scores` change from one float to `{selection_blind, selection, outside_catalog}`. The outside-catalog value is the share of rows with a finding; it is reported, not gated. Run 1's manifest keeps its old shape.
- **Sampled tokens:** every prediction row carries `tokens`, the number of completion tokens sampled. An error row has 0, and failed retries aren't counted.
- **`service.close()`:** called once the run has finished and the session delete has been attempted, and never after a failure. The SDK's `close()` also closes its managed handle, and a failed run keeps its session so checkpoints can be promoted by hand. A failed close is recorded as `close_error`.
- **Unchanged:** 1 epoch (one pass of `batches`), a checkpoint every 25 steps.
- **Out of scope:** `glimmer_trainer sample` already closes its sampler. Its service client is short-lived and isn't covered by item 9.

### Criteria → tests

| # | Criterion | Test |
|---|---|---|
| I8 | The run 2 line follows the upstream snippet, and the frozen prompt matches | `tests/test_prompt.py::test_run_2_rule_follows_the_sdk_snippet`, `::test_frozen_prompt_matches` |
| I9a | Unembed is off and every `train_*` flag is in the manifest | `trainer/tests/test_loop.py::test_run_2_settings_are_recorded` |
| I9b | Ranking and winner use the name-blind score; the manifest keeps both scores and the outside-catalog rate | `trainer/tests/test_loop.py::test_selection_is_name_blind_with_the_outside_catalog_rate` |
| I9c | Prediction rows carry the sampled token count | `trainer/tests/test_loop.py::test_sampled_tokens_are_recorded_per_row` |
| I9d | The service is closed after the delete (even a failed one), never after a failure, and a failed close is recorded | `trainer/tests/test_loop.py::test_run_end_to_end_with_fakes`, `::test_failure_keeps_session`, `::test_failed_delete_is_recorded`, `::test_failed_close_is_recorded` |
| I9e | Error rows carry `tokens: 0`; the checkpoint interval defaults to 25 | `trainer/tests/test_loop.py::test_error_rows_carry_zero_tokens`, `::test_run_2_settings_are_recorded` |

## Run 2 item 7: binding cells (spec, 2026-11-03)

Status: written by the assistant on the user's delegation (2026-11-03). Item 7 of the run 2 decisions asks for coverage per (component, property) × (literal | absolute `$/path` | per-item `$field` in a template), with at least 30 train rows in every cell the schema allows.

### Facts

- **Bindable props** (catalog `Slot.bindable`): Text.text, Image.url/description, Icon.name, Video.url, AudioPlayer.url/description, and the inputs' labels and values. DateTimeInput also has min and max.
- **No variant is bindable in v0.9.** Text.variant and Button.variant are static enums. So "per-item rows bind … a variant" falls outside the schema, and per-item state shows through a bound icon, image or text instead.
- **Current train data (5200 rows), zero cells:**
  - Icon.name per item: **0**. This is the pattern s250 faked with conditionals.
  - Image.description, absolute and per item.
  - Video.url, AudioPlayer.url and AudioPlayer.description per item.
  - CheckBox.label and CheckBox.value per item.
  - Every other cell outside the house rules is already above 30.
- **76 of 160 vocabulary entities have a boolean field** (Verified, Approved, Graded, …). Today, boolean fields are dropped from list columns.

### Decisions

1. **Cell keys** `cell:<Component>.<prop>:<literal|absolute|item>`. In a row, a prop's form is:
   - absolute: a path starting with `/`, including `/list/0/field` in a static list;
   - item: a relative path;
   - for a function call: the form of the path it wraps, or of the `${...}` paths in a formatString;
   - literal: anything else.
2. **The universe** is every bindable slot × the three forms, except where house rules forbid a form. These exclusions are derived from the catalog, not from component names:
   - A checkable component (an input) keeps only `label:literal` and `value:absolute`. Inputs are always bound, and their bounds always bound (SPEC 2c-1 facts).
   - A boolean input also gets `label:item` and `value:item`, for checklists.
   - Every other cell needs 30 train rows.
3. **Per-item state, built by code (ADR 0022):** a list whose entity has a boolean field may show it per item in one of two ways. The field's value is in the record either way, and the intent quotes it.
   - **State icon:** each record also stores `<field> icon` (for example `vegetarianIcon`, by the label rule): a tick when true, and a cross, warning triangle or error icon when false (whichever of these the catalog enum has). Amended in the build: one true icon only, because padlock, bell and eye pairs don't read as "true is good". The item shows `Icon($vegetarianIcon)`. The Intent says, in the user's words, which icon goes with which state ("a tick when its \"Verified\" is true, otherwise a cross").
   - **Checklist:** each item is a CheckBox with `label` bound to its first column and `value` bound to the boolean field. The first column is the label, so the item has no value Text for it (fix from the sample review, `docs/item7-sample-review-20261103.md` A1). While their cells still need rows, a list whose entity has a boolean field prefers a state icon or checklist over media (p 0.75 within the hungry draw, which itself happens with p 0.6), which fit any list (amended 2026-11-03: after A1 shifted the random stream, state icons fell to 3 in 1000 rows). Templates only (amended after the refuter's review): 2–4 bound static CheckBoxes make a form, and the linter's `form_submit` rule rightly rejects a form with no submit button.
4. **Per-item media:** each record may also carry a media URL (Image, Video or AudioPlayer, chosen by coverage weight), built by code from the record's first column. Its description, if set, is bound to the first column. Images that aren't per item can bind `description` next to a bound `url` (`Image.description:absolute`).
5. **All list modes:** template lists use relative `$field`, bound static lists use `/list/i/field`, and literal static lists use literals. A per-item prop never takes its first sibling's value; siblings share only their enums and weights. While an absolute per-item cell (Icon or media) still needs rows, a static list leans bound (p 0.8). Checklists are templates only (decision 3).
6. **Report before full size:** the coverage report and 10 rendered samples (mixing all three per-item kinds) come before full-size generation, as item 7 requires.

### Criteria → tests

| # | Criterion | Test |
|---|---|---|
| C1 | Row keys give each bindable prop's form | `test_synth_coverage.py::test_cell_keys_by_form` |
| C2 | The universe has the cells of decision 2, from the catalog | `test_synth_coverage.py::test_cell_universe_follows_house_rules` |
| C3 | Every cell is reached in a generated run | `test_synth_coverage.py::test_every_key_reached` (existing) |
| C4 | A state icon's record icon matches its boolean, and the Intent names both icons and the field | `test_synth_plausibility.py::test_state_icons_follow_their_field`, `::test_state_rule_is_stated_once` |
| C4a | In every list mode, item i shows record i's own icon and media; checklists are template-only (refuter blocker) | `test_synth_plausibility.py::test_per_item_values_track_their_record` |
| C5a | A checklist item shows its first column once: as the checkbox label, with no Text bound to the same field, and the Intent doesn't name it twice (sample review A1, 2026-11-03) | `test_synth_plausibility.py::test_checklist_label_is_not_repeated`, `::test_checklist_intent_names_the_label_once` |
| C5 | A checklist binds label and value per item, and passes the linter | `test_synth_plausibility.py::test_checklists_bind_per_item` |
| C6 | Per-item media URLs are unique per record and per surface | `test_synth_plausibility.py::test_media_never_repeated_in_a_surface` (extended to per-item URLs) |
| C7 | All rows still compile, round-trip, pass the catalog check and parse in web_core | `test_synth_pipeline.py::test_rows_valid`, `test_synth_coverage.py::test_generated_templates_parse_in_web_core` |

### Test command

```bash
cd glimmer-tune && uv run pytest tests/test_synth_coverage.py tests/test_synth_plausibility.py tests/test_synth_pipeline.py
cd glimmer-tune && uv run pytest
```

## Sample review fixes B1, B3, B4, B6 (spec, 2026-11-03)

Source: `docs/item7-sample-review-20261103.md`, with counts from a 3640-row dry run (same file). The user approved B1, B4, B6, then B3 with a coverage recheck. B5 waits for a check of how gallery References show money and dates. B2 and C1 are not in this section.

### Decisions

1. **B1 (6.8% of rows):** a section heading directly before a list, among the same siblings, takes the list entity's name. The section title it drew is dropped.
2. **B6:**
   - A section heading that is the last of its siblings is dropped (3.3% of rows), unless it is the only one; then the whole run of siblings is empty and the caller's fallback applies.
   - A tab whose content is only dividers also gets a body text (0.6%). If the topic's body texts have run out, the surface is rejected (`GrowError`) and regenerated.
   - Dividers don't dangle either (refuter finding: dropping a heading left `[body, divider]` runs and divider-only Cards). A run of siblings drops dividers at either end, and a run that is only dividers is empty: an open slot returns nothing so the caller falls back, and a skeleton list raises `GrowError` (rejected and regenerated). The same happens to a skeleton list left with only a heading.
   - All are tree rules in the grower, applied wherever siblings are built (skeleton lists and open slots). No linter rule (non-goal: the grower makes them impossible).
3. **B4 (1.2%):** `formatNumber` sets `decimals: 2` only for a non-integer value. Integers get no `decimals` or 0. List siblings still share the first item's format, so a column looks alike.
4. **B3 (10.2%):** outside skeletons tagged `media`, a surface has at most one standalone media block (`b_media`). Per-item media (item 7) is not capped. The coverage report must still have no gaps at full size, on seeds 0 and 1.

### Criteria → tests

| # | Criterion | Test |
|---|---|---|
| B1 | A heading directly before a list names the list's entity | `test_synth_plausibility.py::test_heading_before_a_list_names_it` |
| B6a | No sibling run ends with a section heading after other content | `test_synth_plausibility.py::test_no_trailing_heading` |
| B6b | Every tab has content other than dividers | `test_synth_plausibility.py::test_tabs_hold_more_than_dividers` |
| B6c | No run of siblings is only dividers, or starts or ends with one | `test_synth_plausibility.py::test_no_dangling_dividers` |
| B4 | No `formatNumber` with 2 decimals on an integer value | `test_synth_plausibility.py::test_no_decimals_on_integers` |
| B3 | At most one standalone media per surface outside media skeletons | `test_synth_plausibility.py::test_standalone_media_capped` |
| C3 | Every key still reached | `test_synth_coverage.py::test_every_key_reached`, plus the full-size report on seeds 0 and 1 |

## Sample review fixes B5 and B2 (spec, 2026-11-03)

The user approved both before regeneration.

### Facts

- **Gallery practice (train and dev References; test not read):**
  - Dates appear only through `formatDate`: 456 times, 0 bound raw, 0 ISO literals.
  - Bound money appears only through `formatCurrency`: 544 times, 0 raw. The one exception is 13 hand-typed "$12.99"-style strings in `00_row-layout`.
- **Synthetic Text displays today** (2994 grown surfaces):
  - Dates: 656 of 886 raw (233 bound by path, 224 in a plain `Label: ${/date}` template, 199 literal).
  - Money: 137 of 212 raw or number-formatted (41 path, 36 plain template, 31 literal, 29 `formatNumber`).
- **Icon rows** pick any topic icon (`_icons()`: action and entity icons), so 402 of 552 icon rows in a 3640-row run show an action icon (delete, share, add) next to an unrelated field.

### Decisions

1. **B5:** a displayed ISO date or money-labelled number (`is_money`) is always shown through its formatter, as the gallery does.
   - In a value plan, it is either the standalone formatter or the same formatter inside a template (`Label: ${formatDate(...)}`). It is never a literal, a plain path, a section-nested path or a plain template.
   - List columns: an ISO date always uses `formatDate`, and money always uses `formatCurrency`.
   - A static list with such a column is bound, because a literal can't be formatted from the data.
   - A count (`pluralize`) of an entity whose name contains a money word isn't money and isn't affected.
   - Per-item labels (checklist labels, per-item media descriptions) show a column as it is. So they use the list's first column that isn't a date or money, and a list with no such column gets no checklist or per-item media (refuter finding: a date first column was shown raw).
2. **B2:** an icon row's icon fits its field, chosen in code from the field's kind, then its label:
   - by kind: email → mail, date → calendar, time → event, money → payment;
   - by label words: phone → phone, address, location, city, venue, place or destination → map pin, a person word (full, first or last name, owner, contact, host, guest, member, author, instructor, coach, doctor, agent, payee, holder; not bare "name") → person, rating or stars → star, photo, image or picture → photo.
   - No word icon for a count, kind or measure of a thing (count, capacity, number of, type, style, layout, level, score): "Guest count" and "Room type" get none. Amended after the refuter found about half the person icons on non-people ("Item name").
   - Only icons in the catalog enum are used. A field with no fitting icon gets no icon row; the open slot picks another block. Action and entity icons are never used for icon rows.
3. **Coverage** must still reach every key at full size, on seeds 0 and 1.

### Criteria → tests

| # | Criterion | Test |
|---|---|---|
| B5a | No displayed date or money is literal, a plain path or a plain template | `test_synth_plausibility.py::test_dates_and_money_are_formatted` |
| B5b | List columns of dates and money use their formatter, and such static lists are bound | `test_synth_plausibility.py::test_date_and_money_columns_are_formatted` |
| B2 | An icon row's icon is its field's fitting icon, never an action or entity icon | `test_synth_plausibility.py::test_icon_rows_fit_their_field` |
| B2a | The field-icon table, with over-matches as negatives | `test_synth_plausibility.py::test_field_icon_table` |
| B5c | No emitted Text, label or description shows a raw date; per-item labels never use a date column | `test_synth_plausibility.py::test_no_raw_date_or_money_is_shown`, `::test_per_item_labels_are_never_dates` |
| C3 | Every key still reached | `test_synth_coverage.py::test_every_key_reached`, plus the full-size report on seeds 0 and 1 |

## Sample review fixes D3–D7 (spec, 2026-11-03)

Source: `docs/item7-sample-review-20261103.md` ("Second review"), plus the user's reject of a seed-21 sample ("Alignment is not correct"). The user approved D3–D7. D1 is skipped, and so are D2 and C1: the user doesn't count mismatched wording (titles, tabs, buttons that don't fit their screen) as a problem for run 2.

### Facts

- **D7, rendered** (seed-21 `dashboard_grid/00007`, screenshot through the review page): each item of a side-by-side list item Row gives its image and its button `weight` (flex-grow). The free space then depends on each item's text widths, so "Flu", "Tetanus" and "COVID booster" start at different x positions, and the items don't line up. An image "as a banner across the top" (`variant: header`) inside a Row is tall and leaves large gaps.
- **D5:** among vocabulary entity names, the plural last words that aren't countable things are "totals", "standings", "hours" and "status" ("status" would also singularise to "statu").
- **D6:** the long-text regex is `^[\s\S]{5,}$` (5 characters), while its length check's minimum is 2. The secret regex (8) and its length minimum (8) already agree.

### Decisions

1. **D3:** a new linter rule `repeated_value`. Two static Text components on one surface show the same string: a literal (heading mark stripped) or an absolute path's value, compared case-insensitively. Strings with no letter or under 4 characters don't count, and template items are not checked. The pipeline rejects such a surface and regenerates it, like every other linter rule.
2. **D4:** a run of siblings never has two dividers next to each other. The grower collapses them, with the other B6 rules.
3. **D5:** `plural_nouns` returns None for "totals", "standings", "hours", and any last word ending in "us". Those entities get no count row.
4. **D6:** a length check's minimum is at least the regex's minimum for that kind: long text 5, secret 8, others 2. Messages follow the number.
5. **D7:**
   - A child of a list item that is a Row (items side by side) gets no `weight`. List siblings already share one layout (proto); this keeps their columns lined up. A stacked item (Column) may still weight its children.
   - An Image inside a Row never gets the `header` variant ("as a banner across the top").
   - Not done: the containers inside a screen that says "Everything is left-aligned" may still choose their own alignment. In the render that wasn't visible, because a parent with `align: start` shrinks a Row to its content.
6. **Coverage** must still reach every key at full size, on seeds 0 and 1. The gallery still passes the linter with the existing allowlist, or the new rule gets justified allowlist entries.

### Criteria → tests

| # | Criterion | Test |
|---|---|---|
| D3 | `repeated_value` fires on a repeated literal and on a literal equal to a bound value; it ignores short and non-letter strings | `test_synth_lint.py::test_rule_fires[repeated_value]`, `::test_repeated_value_scope` |
| D4 | No run of siblings has two adjacent dividers | `test_synth_plausibility.py::test_no_adjacent_dividers` |
| D5 | `plural_nouns` refuses non-countable last words | `test_synth_plausibility.py::test_plural_nouns_refuse_uncountable` |
| D6 | A length minimum is never below the kind's regex minimum | `test_synth_plausibility.py::test_length_minimum_matches_regex` |
| D7a | No weighted child inside a side-by-side list item | `test_synth_plausibility.py::test_side_by_side_items_have_no_weights` |
| D7b | No banner Image inside a Row | `test_synth_plausibility.py::test_no_banner_image_in_a_row` |
| S8 | Synthetic rows and the gallery still pass the linter | `test_synth_lint.py::test_gallery_passes`, `::test_synthetic_passes` (existing) |
| C3 | Every key still reached | `test_synth_coverage.py::test_every_key_reached`, plus the full-size report on seeds 0 and 1 |

## Run 2 item 10: source conflicts resolved (2026-11-03)

The conflicts are listed in `docs/tuning-report-20261102.md` section 4 ("Conflicts in the sources"). The Meta Model API terms stay out of scope (user).

| Conflict | Resolution |
|---|---|
| ADR 0003 bans model-written dataset text; ADR 0009 has Spark write the vocabulary | ADR 0003 amended: spec-approved, model-written *words* are allowed, but Express targets and reasoning traces never are (ADR 0025 keeps the same line). Notes added to ADR 0009 and to ADR 0014, which repeated the unqualified rule. |
| Longest 2a row: 5,418 or 5,814 tokens | Unresolved, and moot: the 2a export is gone and run 1's mix replaced it. No decision depends on it. |
| Run 1 mix max: 6882 (export) or 6843 (`prepare`) | `prepare` is authoritative: it renders each row with Glimmer's tokenizer and gates `max_seq_len`. Export's own count (`approx_tokens`, characters / 3) is a stand-in. The 39-token gap is a difference of method, not of data. |
| ADR 0004 says about 26/8/9 families; `splits.yaml` says 26/7/8 with 2 excluded | `splits.yaml` is right (43 families in all). A note was added to ADR 0004. |
| Free SFT cut-off: 2026-11-30, or 2026-12-01 08:00 UTC | Fireworks' pages say "until November 30, 2026", with no time zone, and the 08:00 UTC form has no source. Plan on 2026-11-30 as the last day to start a job. AGENTS.md's "before 2026-12-01 Pacific" is the latest possible reading, not the plan. |
| Memory notes vs ADR 0008 | The assistant's memory file was corrected: v0.9 basic catalog with no macros (ADR 0008), and ADR 0003 as amended. |
| `selection_ci` missing from run 1 score files | Expected: those files predate the feature. The `runs/sft1/rescore-*` files carry it, and the originals stay untouched. |

## 2c-2 and 2c-3 outline

- **2c-2:** generate a base surface and apply a mutation: add, remove or replace a subtree, change data, replace the root with `update=true`, or delete the surface. The row is `existing_surface` = the base, and the target is the update. Export gains a two-turn row: user₁, assistant₁ = base Express, user₂, assistant₂ = update. Text-only rows (5–10%) get templated replies and `expect_ui: false`. The struct hash covers the base and the mutation, since `fingerprint()` gives every rootless surface the same value. Note that the scorer's `extracted` flag is inverted for `expect_ui: false`.
- **2c-3:** reword each intent into 1–3 phrasings, drop any rewording that loses a required identifier (S9's check), and log the model per row. Keys come from the repo root's `.env` (`genUI/.env`) without printing them. Tests stub the model.

## Test command

```bash
cd glimmer-tune && uv run pytest -n auto --dist loadgroup -m "not slow"   # while iterating: 648 tests, ~11 s
cd glimmer-tune && uv run pytest -n auto --dist loadgroup                 # before "done", a commit or a review: 790 tests, ~1:50
cd glimmer-tune && uv run pytest                                          # the same suite serially, ~6:30
```

### Test tiers (2026-11-03)

- **`slow`** marks tests that generate many rows or every gallery family. `tests/conftest.py` sets it from the fixtures a test uses (`SLOW_FIXTURES`) or from its name (`SLOW_TESTS`, which lists the tests measured at 2 s or more without such a fixture). A new test on a heavy fixture is marked automatically.
- **The fast tier is for iteration only.** Generation regressions (the A1 sample-size breakage, coverage reach) show up only in the slow tier, so "done" always means the whole suite.
- **`pytest-xdist`** is a dev dependency. Each test file stays on one worker (`xdist_group` by file), so its module fixtures are built once. `test_generate.py` spreads across workers. The floor is now `test_synth_pipeline.py`, at about 100 s on one worker.
- The trainer's suite (`cd trainer && uv run pytest`) takes seconds and is unchanged.
- AGENTS.md's command list should point here. That edit waits until the other session's AGENTS.md changes are committed.
