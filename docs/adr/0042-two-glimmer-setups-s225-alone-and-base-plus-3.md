---
status: deprecated
---

# Two Glimmer setups are measured as Surface writers: s225 alone, and base + 3 + grammar

**Stopped 2026-11-05 (user).** The Glimmer server smoke test measured 31–117 s to the first token on the serverless training sampler (old issue 11, closed; `docs/issues/11.md` in git history), against §19's assumption of 3–5 s per call. The split is stopped. Spark keeps writing the Surface. The partial code (`explorer/glimmer/`, the Glimmer server) is kept, unused by the agent.

The explorer's Glimmer mode (ADR 0031) offers two setups, chosen by settings (user, 2026-11-05).
- **s225 alone:** run 2's tuned adapter, with no examples and no grammar.
- **Base Glimmer + 3 examples + grammar.** Its examples are new, hand-written macro examples (Brief and Result schemas → macro Surface), not glimmer-tune's generic `fewshot3.jsonl`.

These are the two setups run 2 left level on the gallery (run 2 decision memo, 2026-11-04). s225 writes about 2.4× fewer tokens. Base + 3 has no adapter to host and stays inside the catalog with the grammar.

s225 was trained on generic Express with no macros (ADR 0008). We let it write macros **from the prompt alone** (option a), knowing nobody has measured that. A2 measures it next to Spark and base + 3. If it fails on quality, glimmer-tune run 3 (Brief → macro rows) is the fix, and it must start by 2026-11-30 to be free.

## Considered Options

- **s225 writes generic components only, with code binding Results into the data model (option b).** Rejected for now. It needs a new way to keep data from being retyped, and it changes ADR 0001's mechanism before we know whether option (a) works.
- **s225 with the grammar.** Rejected. In run 2 the grammar didn't help s225: it routed around it through made-up named arguments, and 3 rows hit the token cap.
- **Only base + 3 (§19 as drafted).** Rejected. The user wants s225's speed measured too.

## Consequences

- A2 has three arms (spark, s225, base + 3) instead of two, so it costs about 50% more.
- The Glimmer server is the same for both setups. The checkpoint is a server setting. Examples and grammar are agent settings, sent with each request.
