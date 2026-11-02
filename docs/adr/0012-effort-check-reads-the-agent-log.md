---
status: accepted
---

# The effort check reads the agent's log for tokens and SQL

The reasoning effort check needs each run's token counts, SQL attempts, bare numbers and failure cause. None of these is in the A2A reply, and the agent already logs all of them. So the harness starts its own agent process for each effort level and reads the slice of that process's log written during each run. Runs are sequential, so a slice belongs to exactly one run. We decided against adding a metrics part to the A2A reply: that would change the agent and the client contract just for an eval.

## Consequences

The harness depends on the wording of those log lines. Its tests parse fixture lines in the current format, and the agent's own tests don't pin that wording, so changing a log line means updating the harness. The bare-numbers line is the exception: its wording is a shared constant (`ui.BARE_NUMBERS_LOG`) that the harness imports (ADR 0013).
