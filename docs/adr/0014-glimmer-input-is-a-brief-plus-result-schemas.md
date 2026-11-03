---
status: accepted
---

# Glimmer's input is a Brief from Spark plus Result schemas from code

In the planned two-model split, Spark (`meta/muse-spark-1.3`) runs the SQL tool loop and tuned Muse Glimmer writes the Surface. We decided Glimmer's input has two parts. Spark writes a short Brief in its final model call, saying which Result answers which part of the question and how to show it. Code then appends each Result's ID, columns with their types, and row count, taken from the store. Spark knows what each Result is for, but it can misstate columns. Code states them exactly and costs nothing. Writing the Brief in the call Spark already makes adds no model round trip, which keeps speed first. Spark also keeps the Turn's prose, with its `Cite` placeholders, in that same final call: it has the reasoning behind the answer, and prose needs no layout skill. Glimmer writes only the Surface.

## Considered Options

- **Brief from Spark only.** Proposed to keep the tool portable to any annotated database. Rejected: the Result schemas come from the store, not from the dataset, so they work on any database with no per-dataset code. Without them, Glimmer's output would depend on Spark copying column names correctly, and a wrong name means a Repair.
- **Input assembled by code only** (user question plus Result schemas). Rejected: the question doesn't say which Result goes where or how to lay out the Surface, so Glimmer would have to guess what Spark already knows.

## Consequences

- Glimmer's training rows must have this input shape: a Brief plus Result schemas. The current glimmer-tune rows (an intent in the user's words with data values inline, ADR 0008 and glimmer-tune SPEC 2d) do not, so this has to be settled in the data before the SFT run that the explorer will use.
- Training rows can't have Spark-written Briefs (dataset text isn't model-written, ADR 0003), so their Briefs come from templates. In the explorer, Spark writes them freely. Glimmer will see Briefs in the explorer that look somewhat different from its training Briefs. The schema part is identical in both, which limits the gap. The Brief's wording should be specified tightly enough that templates can imitate it.
- Glimmer's output will reference Results through the explorer's Macros (ADR 0001), because the input refers to Results by ID and generic Express alone can't show their rows without retyping them. This is agreed in principle (2026-11-02), but acting on it waits until the first Glimmer tuning run, on generic Express as ADR 0008 planned, has been evaluated. The split's spec and any change to ADR 0008 come after that.

## Note (2026-11-03)

ADR 0003 has since been amended: spec-approved, model-written *words* are allowed (ADR 0009's vocabulary). Model-worded Briefs would therefore need a spec section, not a new ADR. Until one exists, Briefs come from templates as decided above.
