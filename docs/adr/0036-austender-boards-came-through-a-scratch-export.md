---
status: accepted (2026-11-04)
---

# austender's Boards came through a scratch export, with the reviewer's speed edits

The bootstrap drafts Boards only for a dataset draft it holds, and `export --force` replaces
`dataset.toml`, `notes.md` and `glossary.md` along with `boards/`. austender's own files are older
and hand-tuned, so the user chose (2026-11-04) to draft austender with Wonder, export the draft and
its reviewed Boards to a scratch directory, and move only `boards/` into `datasets/austender/`.
Both have the same `allowed_schemas` (`mart`). The agent's `boards` check then passed against the
real Dataset.

The Boards are still Wonder's (ADR 0028: drafted by the bootstrap, reviewed by a person). In
review, three of the Explorer Board's queries were edited, through the bootstrap's re-check and
preview, with the user's approval:
- `contract_count` was dropped from the Treemap query and the trend query, which don't show it;
- `COUNT(*)` replaced `COUNT(DISTINCT notice_id)` in the top-suppliers query, where they are equal
  because no supplier appears twice on a notice.

Its cold render went from 4.8 s to 1.1–1.2 s, against a 1.5 s target.

## Considered Options

- **`export --force` over austender.** It follows the bootstrap's path literally, but swaps the
  hand-tuned notes and glossary for the draft's. That changes answers and the prompt snapshots in
  a step meant only to add Boards.
- **Draft again with a speed rule in the Boards prompt.** That's the generic fix. It costs another
  live run and gives no guarantee, so it's a follow-up (`prompts/boards.md`: don't compute columns
  the layout doesn't show, and avoid `COUNT(DISTINCT)` on large tables).

## Consequences

`datasets/austender/boards/` doesn't match the draft's `dataset.toml`. A later bootstrap export of
austender with `--force` replaces these Boards whole, as phase 3 intends.
