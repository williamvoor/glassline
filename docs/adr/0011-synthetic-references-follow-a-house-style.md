---
status: superseded by ADR-0033 (2026-11-04)
---

# Synthetic references follow a house style, and their intents never name the catalog

Gallery references keep their designers' props even when the intent (now in the user's words, SPEC 2d) doesn't ask for them. Synthetic references used to drop every unstated optional prop. Trained together, the two would teach opposite habits for the same request, and the explorer's users never name props, so the unprompted look *is* the product. We decided that a synthetic reference takes a fixed **house style**, copied from the gallery's majority habits role by role, wherever its intent says nothing about styling. A `specific` intent states departures from it in everyday words, from a committed phrase table.

Synthetic intents also follow 2d: no component, prop or enum names, no paths, event names, regexes, format strings or catalog icon names. Everything the reference holds that the intent doesn't spell out is derived by fixed rules the model can learn: paths from labels (the label rule), event names as `snake_case(label)`, form submits sending every input, regexes from the field kind, and icons from a humanised name table.

## Considered options

- **The target follows the intent (no unstated props):** rejected. It contradicts the gallery rows, and it trains a plain look as the default.
- **Strip unstated props from gallery train targets:** rejected. It throws away the gallery's design quality, which is the best evidence we have of a good look.
- **Keep catalog names in synthetic intents:** rejected. Train and eval intents would then read differently (2d), and users never write them.

## Consequences

- Style variety (non-house values) comes only from `specific` rows. The detail mix is steered by coverage with a 50% cap on specific, and the row count grows until coverage is met.
- Engineering-only distinctions with no everyday phrase (`spaceAround` vs `spaceEvenly`, `h4` vs `h5`, `fit: none`) are never generated.
- The house style is a design opinion. It is judged by eye at checkpoint (b), not only by coverage.

## Superseded (2026-11-04)

The house style taught a look that didn't transfer to realistic asks (ADR 0033). Distilled targets keep whatever style the teacher's best valid candidate has. The style rules are in `glimmer-tune/archive/`.
