---
status: accepted (2026-11-05, the user)
---

# New Workspace is a step wizard with inline cards and visible models

Phase 2 put the bootstrap steps into Settings behind one step bar that also held the Workspace list.
Export sat inside Review, so the real order looped (Review → Boards → back to Review → Export), and
you couldn't step back except through one-off buttons. The user found it clumsy (2026-11-05). So
**New Workspace** becomes a wizard of its own at `#/settings/new`: Database → Draft → Review →
Boards → Export (Workspaces SPEC, phase 3). Settings keeps only the Workspace list.

Three rules come with it:

- **No pop-ups.** Every confirmation is an inline card where the action is, never
  `window.confirm` or a modal dialog. The cost ceiling (S5) is printed on the button that spends
  the money ("Draft (at most $1.90)"), so one click is an informed one.
- **Next never calls a model.** Only the Draft and Draft Boards buttons do. Each step's Next is
  gated by that step's own blockers, so Export never sends you back to fix something.
- **Models are on show.** Each allowed model has a fixed name and accent colour. A chip marks
  every place a model works: the step bar, a live run card (calls, tokens, cost against the
  ceiling), and each answer Turn. Steps without a model are tagged SQL. The user wants to show how
  each model is used.

## Considered Options

- **Keep one page and make the step bar clickable.** It's the smallest change, but the Review/Export
  loop stays, and so does the Workspace list posing as step 1.
- **A modal wizard over the explorer.** The steps hold report tables and tabbed file editors,
  which are too big for a dialog, and the user wants no pop-ups.
- **Next starts the Draft after one confirm.** Fewer clicks, but then a Next button spends money.
