---
status: accepted
---

# A name-blind slot score replaces data paths and event names with placeholders, reported next to the literal score

Glimmer's intents never name data paths or event names (glimmer-tune SPEC, rules R1–R3; ADR 0011). The literal slot F1 compares them as text, so an answer that binds `/email` where the reference binds `/form/email` loses slots it had no way to get right. Run 1 scored 0.94 slot F1 on synthetic dev but 0.42 on gallery dev, and we can't tell how much of that gap is the scorer. We add a second score that rewrites every path, event name and event context key to a placeholder before comparing (`"<path>"`, `"<event>"`, `*`). It also blinds bare-path `${...}` interpolations and data model pointers, but keeps the data values. It uses the same component alignment and the same triple counts as the literal score. It is reported next to the literal score. Checkpoint selection stays literal until the run 1 rescore has been read.

## Considered options

- **A learned one-to-one renaming** (map each predicted name to a reference name through the alignment, and require consistent use). This is more faithful: it would catch two fields bound to one path. Rejected for now because it needs its own matching and tie rules. The placeholder score answers the immediate question, and `bindings_valid` plus reading the failures covers the consistency gap.
- **Drop path and event triples entirely.** Rejected: the score would then not notice a missing binding or a literal where a binding belongs.
- **Switch selection to the new score now.** Rejected: nobody has looked at what it rewards yet. Rescoring old predictions is free, so switching later loses no comparability.

## Consequences

- Score files gain `slot_f1_blind` (per row and per source) and `selection_blind`. Old score files lack them until they are rescored.
- A model that reuses one path for two inputs scores full name-blind F1. Reading failures, not this score, has to catch that.
- If the name-blind score becomes the selection score for run 2, it needs the same bootstrap interval as `selection`.
