---
status: accepted (2026-11-04, the user)
supersedes: 0020
---

# Muse Wonder is allowed in the explorer's request path, chosen by configuration

ADR 0020 kept `meta/muse-wonder-1.0` out of the explorer's agent because of speed, before Wonder's speed had been measured. A probe on 2026-11-03 (`docs/scenarios/austender/runs-20261103T060239Z`, 5 scenarios × 2, against Spark's `runs-20261103T051121Z`, same scenarios × 3, same settings and `reasoning_effort=minimal`) showed:

| | Spark | Wonder |
|---|---|---|
| Pass rate | 15/15 | 10/10 |
| Median / p90 wall | 6.3 s / 8.6 s | 7.0 s / 7.6 s |
| Median per model call | 2.9 s | 3.4 s |
| Median output tokens per call | 257 | 342 |
| Clickable Visuals with a Drill query | 20/24 | 16/16 |
| Repairs | 0 | 0 |
| Estimated cost per answer | $0.0067 | $0.0231 (3.5×) |

The cost row is estimated from the logged tokens at ADR 0020's prices, counting reasoning tokens as billed output, not taken from a bill. Without reasoning tokens it is $0.0058 and $0.0196. We don't know whether Meta bills reasoning tokens.

The sample is small and the scenarios produce single-Visual answers, so the probe shows no difference in quality or Drill queries (20/24 against 16/16 is not significant). Wonder was 11% slower per answer at the median and 18% slower per model call. That is a speed close to Spark's, which ADR 0020 had assumed it wasn't.

So Wonder may be the agent's `MODEL`, and the agent may also call it for two named jobs:
- drafting an Entity Board at runtime, for an entity type that has no reviewed Board (ADR 0028). This is always an Ask, confirmed by the user like every model round trip from the UI (§15), with its own setting `BOARD_DRAFT_MODEL`;
- answering questions, when configured.

Spark stays a supported model. Which model is the default for answers is decided by an A/B run on the Board and Controls scenarios, once they exist. Glimmer and Spark Open keep their own scopes (ADR 0003 and later, ADR 0025).

## Considered Options

- **Keep ADR 0020.** Safe, but it rules out a model that measured nearly as fast.
- **Wonder for everything at once.** Cost is about 3.5 times as high, and the sample is too small to change the default.
- **Wonder only off the fast path** (runtime Boards, which are confirmed Asks). This is where it starts. Answers move to Wonder only after the A/B run.

## Consequences

- `AGENTS.md` (root and `explorer/`) and `explorer/.env.example` change when this is accepted: the model rule names Wonder for these uses.
- A Board drafted at runtime is marked unreviewed on screen, kept in memory only, and can be exported for review. It is never written into `datasets/` automatically.
- Tests still never call a real model. The stub model stands in for Wonder as it does for Spark.
- The serving model is already shown on every Level's status line (§16), so users can tell which model answered.

## Note (2026-11-04): slice 5, with `explorer/SPEC.md` §21 (approved 2026-11-04)

- **The runtime Board draft is deferred.** No Dataset has an entity type without a reviewed Board,
  and §20's "Ask within filters" covers questions about a single row. The permission above stays.
- **The A/B has a gate, fixed before the run.** Wonder becomes the default answer model only if:
  its Board-set pass rate is at least Spark's + 10 points; its typed-set runs that are not a pass
  are at most Spark's + 1 (a `needs_review` run is not a pass); and its median Turn wall time is
  at most 1.15 × Spark's in each set. Otherwise Spark stays. Repairs and write attempts are
  reported, not gated. 3 runs per scenario, from one clean commit, with only `MODEL` different;
  5 runs if `scenarios.py compare` calls it close. The result is added here as a further dated
  note.
