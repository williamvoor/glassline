---
status: accepted
---

# Train, dev and test are split by gallery family

The data comes from the 43 A2UI v1.0 gallery examples. Each example is a family, and a slot template makes many variants of it. Families are partitioned once, by hand, into train, dev and test (about 26/8/9 of the single-turn seeds). They never cross splits, so dev and test measure designs the model never trained on, not just new wording. The exporter adds a guard: it fails on any train case from a non-train family, and drops train cases whose structural fingerprint or intent text appears in dev or test. None of the gallery examples are put in the prompt, because they are eval data.

## Considered options

- **Split variants at random:** rejected. Sibling variants differ only in text, so the test would leak.
- **All gallery examples go to eval, with new train-only families:** rejected for now. It needs more templates before the free SFT offer ends on 2026-11-30. We can revisit if 26 train designs prove too narrow.

## Consequences

- Moving a family between splits after the test set is frozen invalidates the test results.
- Only about 26 designs are seen in training, so the variety within a family has to come from slots: labels, counts and repeated rows.

## Note (2026-11-03)

The split as built (`glimmer-tune/data/splits.yaml`) is 26 train, 7 dev and 8 test families, plus 2 multi-turn families excluded: 43 in all. The "about 26/8/9" above was the plan before the multi-turn seeds were set aside.
