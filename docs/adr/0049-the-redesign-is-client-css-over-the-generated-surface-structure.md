---
status: proposed (2026-11-06, awaiting the user's review)
---

# The redesign is client CSS over the generated Surface's structure

SPEC §24 gives the explorer one flat look: a dark Sidebar holding the brand, the Workspace
picker, the log and the composer, and a light Canvas header holding the tabs, Settings and New
conversation. A design mock-up asked for more than that. It wanted a highlighted Metric, a
named extreme under a Metric, a "preliminary" Control option, distinct Treemap shades,
title-cased labels and a Control that moves a marker on a chart.

We built only what the client can style from what it already receives. Everything that needs new
data stays out: no new Board, Macro or catalog fields, and no agent change. ADR 0026 (the agent
sends structure, never style) and ADR 0001 (every number from a Result) hold. Title-casing is
rejected outright, because it would rewrite database text.

A2UI renders components without their IDs, so the client can't address `board_heading` or
`board_controls` by name. The CSS finds them by position instead, on Board tabs only
(`.tab-view.board`). The heading is the first Column of the Surface's root. With three children
it holds a title, a question and a scope. The Controls Card is the root's second child. This
couples the client to the shapes that `heading_components` and `build_board` in `boards.py`
produce. If either changes, the styling degrades to the plain A2UI look; nothing breaks.

## Considered options

- **Add a variant or an emphasis field to the Board heading's Text**, so the client styles by a
  name. That is cleaner, but it is a catalog and agent change for a look.
- **Render the Board heading in the client** from tab metadata. That splits one Surface into
  two sources.

## Consequences

- A change to the Board heading or Controls layout in `boards.py` must check the §24 selectors
  in `client/index.html`.
- The Workspace badge is CSS (`data-initials` and `::before`), so a button's text stays the
  Workspace's name, which the tests and assistive technology read.
