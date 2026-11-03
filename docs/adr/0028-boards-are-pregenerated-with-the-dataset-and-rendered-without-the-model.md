---
status: accepted
---

# Boards are written with the Dataset, reviewed, and rendered from their queries without the model

Users want a rich first view, like the "Follow the Contract" prototype's Explorer tab: several Metrics, charts and tables with controls. They also want a row click (a supplier, an agency) to open a whole page about that entity. If a model writes those pages per request, every view costs a model run (about 7 s), and speed is the explorer's first requirement. So a **Board** (a named set of parameterised queries, Controls and a layout of Macro calls) is written once with the Dataset in `datasets/<name>/boards/`, drafted by the bootstrap tool and reviewed by a person. The explorer renders a Board by running its queries and expanding its Macros, with no model call. A Board with no key is the Dataset's landing page. A Board keyed on a column (an **entity Board**) opens from a click on a row that carries that column. ADR 0001 holds: the file stores SQL and Macro calls, never numbers, and every Visual still shows its SQL.

## Considered Options

- **The model writes every page** (the main answer, or an Ask on click). Richest and fitted to the question, but it pays a model run for every view. This remains the path for anything a Board doesn't cover (an Ask).
- **The model writes the entity pages ahead, in its answer** (Drill queries grown into whole pages). Clicks would be instant, but every answer gets much longer, and output time is the largest cost in an answer.
- **Boards drafted at runtime by Muse Wonder for entity types without one** (ADR 0030). Kept as a later fill-in, marked unreviewed. It is not the default, because nobody reviews the Board before users see it.
- **Generate UI code.** Rejected, as in ADR 0024: it breaks ADR 0001 and 0002.

## Consequences

- The landing page and entity Boards cost database time only. Their Results can be cached, because the data changes only when it is loaded.
- Board files are Dataset configuration, so the code stays dataset-agnostic. The explorer checks every Board at startup and refuses to start on a broken one, as it does for other Dataset configuration.
- An entity Board shows the entity across the whole Dataset and doesn't keep the clicked answer's filters (ADR 0027's reason for model-written Drill queries). It says so in its subtitle, and its Controls let the user narrow it.
- The bootstrap tool gains a step that drafts Boards and a preview to review them.
- When a Board's rows are clicked, the same rules as everywhere else apply: a Drill query, another entity Board, or an Ask.
