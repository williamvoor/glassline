---
status: accepted
---

# Text on the Surface follows the prose rules

The model writes plain `Text` on the Surface (headings, notes) as well as Macros, and the effort check showed that text carrying typed numbers and unfilled `Cite` placeholders. We decided that every `Text` the model writes, and every Macro title and label, gets its placeholders filled from Results exactly like the prose. Tab titles in `Tabs` are filled the same way (amended 2026-11-02). Typed numbers in that Text are logged as bare numbers, not rejected, keeping ADR 0001's warn-only rule. A Repair would cost a model round trip, and the check can't tell a typed count from a year.

**Amendment (2026-11-02): warn-only re-checked; dates no longer count.** In the 50 logged answers of the effort check (`docs/effort-check/agent-*.log`), every one logged bare numbers, but about 95% of the tokens were pieces of dates ("2023, 24" from "2023-24"; "1, 2024, 30, 2025" from "1 Jul 2024–30 Jun 2025"). Only 7 answers carried a real typed figure: "about 6.6%" (Q5, 5 runs), "from ~$8m to ~$1.57bn" (Q3) and "50–90bn" (Q4). All were correct roundings of the reference values. That doesn't justify a Repair on every reply, so warn-only stands. The check now skips dates, financial years and years 1900–2099, so the log shows the figures that matter. If those turn out to be wrong in later runs, revisit then.

## Considered Options

- **Remove plain `Text` from the model's catalog.** Strongest guarantee, but it loses headings and notes or needs a new Macro for them.
- **Repair on bare numbers in Surface Text.** Rejected for speed and false positives (years, financial years).
- **Reject string-literal columns in the SQL guard.** Proposed from a misread of the runs; the text never came from SQL.
