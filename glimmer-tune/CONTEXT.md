# glimmer-tune

The eval harness and training-data factory for teaching Muse Glimmer 30B to write A2UI Express. It turns gallery designs and catalog-driven synthetic surfaces into eval cases and SFT rows.

## Language

### Sources

**Seed**:
One vendored A2UI gallery example, converted to v0.9. It is the ground truth for its Family.
_Avoid_: Example, sample

**Family**:
One gallery design and all the cases made from its Seed. It applies to gallery designs only.
_Avoid_: Skeleton (for synthetic rows), template

**Template**: _(retired 2026-11-04 with the run 2 generators; see `archive/`)_
A hand-written slot file that varies one Family's text and data to produce many Gallery rows.
_Avoid_: Skeleton

**Skeleton**: _(retired 2026-11-04 with the run 2 generators; see `archive/`)_
A layout archetype for synthetic surfaces: fixed containers plus Blocks and Open slots, filled from one Topic. It is the synthetic counterpart of a Family for splits.
_Avoid_: Family, template, archetype (except in Banned archetype)

**Block**: _(retired 2026-11-04 with the run 2 generators; see `archive/`)_
A named unit that a Skeleton places, such as a field, a key/value row or a list, which becomes a small subtree of components.
_Avoid_: Widget, component (a Block is usually several components)

**Open slot**: _(retired 2026-11-04 with the run 2 generators; see `archive/`)_
A place in a Skeleton that is filled with a number of Blocks of allowed kinds, chosen by coverage.
_Avoid_: Hole, placeholder

**Vocabulary**: _(retired 2026-11-04 with the run 2 generators; see `archive/`)_
The committed, LLM-written words for one Domain: titles, sentences, fields, actions, entities and choice groups.
_Avoid_: Dictionary, word list

**Domain**: _(retired 2026-11-04 with the run 2 generators; see `archive/`)_
A subject area for Vocabulary, such as travel or banking.
_Avoid_: Dataset, category

**Topic**: _(retired 2026-11-04 with the run 2 generators; see `archive/`)_
A coherent group of words within one Domain's Vocabulary. One surface draws all its words from one Topic.
_Avoid_: Theme, subject

### Cases and rows

**Case**:
One evaluation or training item: an Intent, a Reference, and the existing surface it applies to.
_Avoid_: Example, sample, test

**Intent**:
The user's request in plain English that a Case's Reference answers. It states every identifier the scorer needs.
_Avoid_: Prompt, query, instruction

**Reference**:
The v0.9 messages a Case expects, and their canonical Express decompile, which is the training target.
_Avoid_: Gold, answer, label

**Detail level**: _(retired 2026-11-04 with the run 2 generators; see `archive/`)_
How much of a Synthetic row's Intent spells out, in the user's words: terse (purpose and content), medium (adds where each piece goes) or specific (adds styling). Whatever the Intent leaves unsaid follows the House style.
_Avoid_: Verbosity, difficulty

**House style**: _(retired 2026-11-04 with the run 2 generators; see `archive/`)_
The fixed look a Synthetic row's Reference takes wherever its Intent says nothing about styling. A specific Intent states only the departures from it.
_Avoid_: Defaults, theme

**Gallery row**:
A Case made from a Family's Template.
_Avoid_: Hand row, real row

**Synthetic row**: _(none remain: the 200 synthetic dev rows were deleted on 2026-11-05)_
A Case grown from a Skeleton by the generator.
_Avoid_: Generated row, fake row

### Splits and leaks

**Split**:
Where a Family or a Skeleton belongs: train, dev or test for Families, and train, dev or held out for Skeletons. Test is gallery-only.
_Avoid_: Partition, fold

**Held out**: _(retired 2026-11-04 with the run 2 generators; see `archive/`)_
A reserve split for Skeletons. Its rows are never trained on and never used for dev, and are generated only to probe generalisation later.
_Avoid_: Test (for Skeletons), excluded

**Excluded**:
A Family that is in no split, because the harness can't make its Cases yet (the multi-turn seeds).
_Avoid_: Held out

**Banned archetype**:
The layout of one test Family that no Skeleton may mirror, as checked by a matcher.
_Avoid_: Blocked design

**Eval literal**: _(retired 2026-11-04 with the run 2 generators; see `archive/`)_
A distinctive string from dev or test data that never appears in train data. Vocabulary and Synthetic rows must not contain one.
_Avoid_: Leak string

**Coverage key**: _(retired 2026-11-04 with the run 2 generators; see `archive/`)_
One catalog feature that synthetic data must exercise a target number of times, such as a component, a prop, a slot pattern, a binding kind, a check kind or an action kind.
_Avoid_: Feature, pattern (a slot pattern is one kind of Coverage key)

### Review

**Verdict**:
A reviewer's judgement on one scored prediction, naming who is to blame for its score: ok (the score is fair and the output is fine), model (the output is wrong), reference (the Reference asks for things its Intent never says) or scorer (the output is right but the score says otherwise).
_Avoid_: Label, grade, rating

**Unreachable component**:
A component that is defined in an output but never attached to the tree under the surface's root, so nothing shows it.
_Avoid_: Orphan, dead component

**Verbatim string**:
A string the Intent quotes, which must appear on screen exactly as quoted. It may be built from bound data, as long as the rendered text matches.
_Avoid_: Literal string (a Verbatim string need not be a literal)
