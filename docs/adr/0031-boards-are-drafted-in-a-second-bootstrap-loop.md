---
status: proposed
---

# Boards are drafted in a second bootstrap loop, as TOML checked by the agent's own code

Board files (ADR 0028) need the reviewed dataset files (notes, glossary, comments) as input. They
also need many retries against checks that only the database can run (`LIMIT 0`, output column
kinds, Drill queries). So the bootstrap drafts them in a second Muse Wonder loop, after the dataset
draft, with its own `submit_boards` tool. Each Board arrives as the whole TOML file in a string
and is checked by `explorer_agent.boards` (`parse_board`, the set rules, `check_board`), so a
Board the bootstrap accepts is one the agent will start with.

## Considered Options

- **Add Boards to `submit_draft`.** One model call less, but a single long loop whose failure
  loses the dataset files too, and the Board checks need the reviewed comments, which arrive
  later.
- **A nested JSON schema for Board keys.** Validates shape in the tool call, but it duplicates
  `parse_board` and drifts from it. ADR 0023 already has us validate tool arguments ourselves.
- **Render A2UI in the bootstrap UI for the preview.** Closest to what users see, but it pulls the
  client's renderer into a dev tool. The preview shows tables, and the reviewer opens the Boards
  in the explorer before committing.
