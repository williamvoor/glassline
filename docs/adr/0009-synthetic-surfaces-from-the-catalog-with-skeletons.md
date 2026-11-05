---
status: superseded by ADR-0033 (2026-11-04)
---

# Synthetic training surfaces come from the catalog schema, shaped by skeletons

Training on the 26 train gallery designs, about 60 text variants each, memorised the templates by step 9. We add a second source of data. A generator reads the v0.9 basic catalog through the SDK's own signature tables (`build_catalog_helpers`), grows component trees inside about 20 hand-written skeletons (layout archetypes such as a settings list or a checkout), fills the trees with domain vocabulary, and emits v0.9 JSON. The JSON is kept only if the SDK validates it and if compiling its canonical decompile gives back the same messages. Training targets stay the canonical, positional-only decompile (ADR 0003), so one UI has one spelling.

Splits follow ADR 0004 with skeletons in place of families: each skeleton is train-only, dev-only or held out. **Test stays gallery-only.** No skeleton may mirror a test design. A banned-archetype list in `data/splits.yaml` maps each test family to the archetype it would leak, for example 36_modal to "a heading and a button that opens a modal". Modal and the other components still appear inside other skeletons.

## Considered options

- **Purely random trees from the schema:** rejected. The model would learn to draw odd UIs. Skeletons and a plausibility linter keep the trees realistic. The linter is checked against the gallery first.
- **More hand templates only:** rejected as the main source. Variety stays capped by 43 designs, and every new design costs hand work. Hand templates stay as about 30% of the training mix.
- **Keyword-argument targets for coverage:** rejected. Targets with two spellings for the same UI make the training signal noisier. The scorer already accepts keyword-argument predictions.
- **The LLM writing whole screens:** rejected. It would bring back invalid trees and more than one spelling for the same UI. The LLM (`meta/muse-spark-1.3`) writes only the domain vocabulary: titles, labels, sentences, actions, records and icons. The vocabulary is written once, validated, reviewed and committed, so generation stays deterministic. Paths are derived from labels by code.
- **Vocabulary written only by hand or code:** rejected. It doesn't give enough variety for about 5,000 rows.
- **Hard-coding component knowledge in the generator:** rejected. Re-reading the catalog means a catalog change shows up as a coverage gap or a test failure, not as silent drift.

## Consequences

- Multi-turn rows (data-only update, component update, `surface(id, update=true)`, `deleteSurface`) and text-only rows need new export row shapes and a new dedup hash. `fingerprint()` gives every surface without a root the same value.
- Intents are written by code from the tree first. An optional rewording by `meta/muse-spark-1.3` comes later, with the model logged per row. A rewording that drops a required identifier is rejected.
- Vocabulary is shared by train and dev. No vocabulary string may equal a literal text in a dev or test seed or draft. That rule, not separate vocabularies, stops words leaking from eval into train.
- Dev mixes gallery and synthetic rows, so the two are reported separately. A synthetic dev score says nothing about real-world UIs; test does.

## Note (2026-11-03)

Spark writing the vocabulary is an exception to ADR 0003's "no model-written dataset text". ADR 0003's amendment of 2026-11-03 records it and its limits: words only, never Express targets.

## Superseded (2026-11-04)

ADR 0033 replaces skeleton-built training data with teacher candidates filtered by a layered validator. The vocabulary step's hosted Model API output stays out of training data. The generator, skeletons and vocabulary are in `glimmer-tune/archive/`. The 200 synthetic dev rows were deleted on 2026-11-05.
