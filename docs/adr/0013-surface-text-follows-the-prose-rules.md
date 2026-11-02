---
status: accepted
---

# Text on the Surface follows the prose rules

The model writes plain `Text` on the Surface (headings, notes) as well as Macros, and the effort check showed that text carrying typed numbers and unfilled `Cite` placeholders. We decided that every `Text` the model writes, and every Macro title and label, gets its placeholders filled from Results exactly like the prose. Typed numbers in that Text are logged as bare numbers, not rejected, keeping ADR 0001's warn-only rule. A Repair would cost a model round trip, and the check can't tell a typed count from a year.

## Considered Options

- **Remove plain `Text` from the model's catalog.** Strongest guarantee, but it loses headings and notes or needs a new Macro for them.
- **Repair on bare numbers in Surface Text.** Rejected for speed and false positives (years, financial years).
- **Reject string-literal columns in the SQL guard.** Proposed from a misread of the runs; the text never came from SQL.
