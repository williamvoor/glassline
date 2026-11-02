---
status: proposed
---

# Muse Wonder is used only by the dataset bootstrap, never in the explorer's request path

Live calls are otherwise limited to `meta/muse-spark-1.3` (plus Muse Glimmer, only for A2UI). The dataset bootstrap tool drafts a new Dataset's notes, glossary and comments once, offline, with a person reviewing every file, so per-call cost ($4.00 / $0.20 cached / $20.00 output per 1M tokens, versus Spark's $1.25 / $0.15 / $4.25, as of 2026-11-02) and latency matter little, and draft quality matters a lot. We allow `meta/muse-wonder-1.0` there, with Spark as the only alternative. The explorer's agent, where speed is the top requirement, never calls Wonder.

## Consequences

- Every factual claim in a draft carries SQL that the tool reruns, so Wonder's output is checked against the data rather than trusted.
- Whether Wonder's output may be used to train Muse Glimmer is a separate, open question (Model API terms); this decision does not allow it.
