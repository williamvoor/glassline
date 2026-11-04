---
status: accepted
---

# The v2 swap-in gate is gallery dev plus a held-out explorer slice

Gallery dev has 7 families. With family-level intervals it can't resolve a paired difference smaller than about ±0.15 (Phase 1 report, 2026-11-04), so "clearly beats base + 3" was close to unreachable for any adapter. We decided (user, 2026-11-04) that a v2 adapter is judged on gallery dev plus an **explorer-shaped dev slice**. The slice holds about 30 cases from 6 schema-bank domains held out of training, and each domain counts as a family. That gives about 13 families. The adapter replaces base + 3 + grammar as the explorer default if, paired with family-level intervals, all hold: the point gain in name-blind slot F1 is at least +0.05, the lower bound is above −0.05, and the output-token p95 is no worse.

The slice's references come from Glimmer base: best-of-8 with the grammar and 3 examples, filtered by validator layers 1–3. A person picks one candidate per case and may edit it. They are not written by Spark Open, because a Spark Open reference would favour a student distilled from Spark Open.

## Considered options

- **Keep the gallery-only gate with "lower bound > 0":** rejected. It ends v2 as "unproven" whatever the adapter does.
- **Lower bound > 0 on the combined set:** rejected as likely unreachable with about 13 families.
- **Open the test split earlier:** rejected. The test split is spent once, on the final candidates (ADR 0033).

## Consequences

- The slice's domains must stay out of the schema bank's train domains (`data/intents/slice_domains.json`). The export leakage guards read the slice cases like other eval cases.
- References picked from Glimmer base favour answers that base Glimmer can write. A person picking and editing them limits this, but slice scores still read as comparisons between models, not as absolute quality.
- "Held out" means the exact domain. Some slice domains have near neighbours in the train bank
  (museum collection and art gallery sales; warehouse inventory and camping gear inventory;
  dental practice and veterinary clinic). Intent text is kept apart by the near-duplicate check,
  but the slice's families are less independent of train than "13 families" suggests.
- The gate is a judgment threshold, not a significance test. A pass means "no evidence of harm and a gain worth having".
