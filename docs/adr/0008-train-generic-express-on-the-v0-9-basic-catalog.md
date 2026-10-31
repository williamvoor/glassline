---
status: accepted
---

# Train generic Express on the v0.9 basic catalog

glimmer-tune tunes Muse Glimmer to write better generic A2UI Express, not the explorer's macro replies. From milestone 2b, training rows, eval cases and the system prompt use the A2UI v0.9 basic catalog with no explorer macros. `@a2ui/lit`, the only renderer we run, renders v0.9 and not v1.0, so the model is trained against the catalog we serve and can see.

The two basic catalogs differ very little. They have the same 18 components and the same functions. Four props differ: `TextField.placeholder`, `Video.posterUrl` and `Slider.steps` exist only in v1, and `TextField.validationRegexp` exists only in v0.9. No gallery seed uses any of them. Bindings and calls are written `path`/`call` in v0.9 JSON and `@path`/`@call` in v1, but Express writes both the same way.

Express is **not** fully version-neutral, which corrects ADR 0005. Positional slots follow each catalog's prop order. v1 `TextField(label, value, placeholder, variant, …)` and v0.9 `TextField(label, value, variant, validationRegexp, …)` both accept `TextField("Password", $/p, _, "obscured")`, but under v0.9 "obscured" becomes a validation regex. It compiles without error. So the 2a templates are re-canonicalised from v0.9, and gallery seeds are converted from v1 at the JSON level, never through Express.

## Considered options

- **v1 basic catalog (what 2a used):** rejected. It follows the newer spec, but v1.0 is still a candidate and nothing we run renders it. Training on v1 and serving v0.9 puts a slightly different catalog in the prompt at inference than in training.
- **v0.9 basic plus the explorer's macros, with the explorer's prompt (ADR 0005's plan):** rejected. It ties the tuned model to one app, and the goal is generic Express.

## Consequences

- This replaces ADR 0005's plan to import the explorer's prompt and its macros catalog into glimmer-tune. ADR 0005's decision (v0.9 on the wire) stands.
- The 2a artefacts were built on v1 and must be rebuilt on v0.9: the train cases, the draft references and fingerprints, `data/dev/cases.jsonl` and the system prompt. Approvals carry over by row ID, because intents don't change.
- gt-smoke-1 used the v1 prompt. Its adapter is not comparable with 2b runs.
- Moving to v1 later means regenerating the data and retraining, not redesigning, because the templates are Express.
