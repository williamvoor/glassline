---
status: deprecated
---

# Spark writes a Brief and Glimmer writes the Surface, behind a setting

**Stopped 2026-11-05 (user).** The Glimmer server smoke test measured 31–117 s to the first token on the serverless training sampler (old issue 11, closed; `docs/issues/11.md` in git history), against §19's assumption of 3–5 s per call. The split is stopped. Spark keeps writing the Surface. The partial code (`explorer/glimmer/`, the Glimmer server) is kept, unused by the agent. This ADR was never accepted. It stays as the record of the design.

Today Muse Spark writes the SQL, the prose and the Surface in one tool loop. We will add a second mode, `SURFACE_MODEL=glimmer`. In that mode Spark's final reply carries a Brief instead of the Surface, and Muse Glimmer writes the Surface from the Brief plus the Result schemas (ADR 0014). Spark keeps every piece of SQL, including the Drill queries (ADR 0027), and every word a user reads as prose, including the FollowUp questions. Code moves the Drill queries and FollowUps from the Brief into the Surface. So Glimmer only lays out Macros over Result IDs and columns, which keeps ADR 0001 and Glimmer's one job (A2UI only).

The public inference API doesn't serve Muse Glimmer, so a small local **Glimmer server** samples it on Fireworks' serverless training pool, by checkpoint path. Every request carries a grammar built from the inference catalog and the Turn's Results.

The default stays `spark` until a measurement passes the gate: equal scenario quality **and** a lower median Turn time. Speed is the project's first requirement, and the split adds a model round trip. Spark's final call (4.2 s at p50) loses only its Surface part, while a Glimmer call costs 3–5 s on the pool. So the split can only win if Glimmer's call overlaps Spark's prose: the Brief comes first in a streamed reply. A paper check on logged scenarios comes before any build. Base Glimmer is tried first. A tuning run on Brief → Macro rows (glimmer-tune run 3) follows only if base Glimmer is promising on speed but short on quality.

Details are in explorer SPEC §19 (draft).

## Considered Options

- **Spark only (today).** It stays the default and the fallback. Rejected as the only mode, because the user wants Glimmer measured in the explorer.
- **An on-demand Glimmer deployment.** This is a supported serving path with predictable latency, but it costs about $8/h for an H100, roughly $5,800 a month always on. Rejected for now: a measurement doesn't need it. Revisit if the pool's latency or Fireworks' terms rule the pool out.
- **Glimmer writes Drill queries and FollowUps too.** Rejected. Glimmer doesn't see the dataset context and isn't allowed to write SQL. Spark already has the reasoning behind both.
- **Tune first (run 3), then integrate.** Rejected as the first step. It spends the data-building effort before knowing whether the split can be fast enough at all.
- **Glimmer imported into the agent.** Rejected. The training SDK and the renderer don't fit the agent's environment under the shared A2UI SDK pin (ADR 0015), so Glimmer runs in a separate process.

## Consequences

- **Two prompt sets per dataset.** Spark's split-mode instructions are a second cached prefix (`explorer-<dataset>-split`). Layer 1 stays shared, so Spark can still write the Surface as the fallback.
- **The Brief's format becomes a contract.** glimmer-tune's run 3 rows must imitate it with templates (ADR 0014), so it is fixed before any tuning.
- **ADR 0008 gains a second track if run 3 happens.** glimmer-tune would then also train on the explorer's inference catalog (Macros plus passthrough), vendored as a catalog file, alongside the generic v0.9 rows. See the note in ADR 0008.
- **Unknowns.** Using the training sampler for serving depends on Fireworks allowing it and keeping checkpoints. Ask before measuring with users. The pool's queueing may make p90 unpredictable.
- **The explorer's request path gains a non-LiteLLM call.** The Glimmer server uses the Fireworks training SDK. The agent itself still uses only LiteLLM.
