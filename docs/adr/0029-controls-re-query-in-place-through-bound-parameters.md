---
status: accepted
---

# Controls re-query a Board in place through bound parameters; the client sends option indices, not values

A Board's Controls (a choice, a multi-select of tags, a toggle, a number slider, a date) change what its queries return. A change must be fast, so it can't wait for the model. And the client must not send database text, which §15 already forbids. So each Control is declared in the Board file and bound to `:name` placeholders in the Board's queries, with the same scan, `$n` rewrite, guard and `LIMIT 0` check as Drill queries (ADR 0024). Unlike a Drill query, each Control declares a SQL type, and its placeholder is cast (`$n::text`, `$n::int[]`), because Postgres can't infer the type of `$1 IS NULL`. A choice's options come from a query, and the server keeps them. The client sends the option **indices**, a boolean, a number or an ISO date, never a value's text, and the server validates each one against the Control's declaration. A change re-runs only the queries that use the changed Control, with no new Level and no model call. The reply is an `updateComponents` for the same surface, carrying only the replaced panels under stable component IDs. The server never writes the Controls' data model after creating the surface, so a server update can't echo back as a user change, and a slider being dragged doesn't jump.

The A2UI v0.9 basic input components (`ChoicePicker`, `Slider`, `CheckBox`, `DateTimeInput`) have no action: they only write to the data model. So the client watches each Control's data-model path with `DataModel.subscribe` and sends the `controls` action itself, debounced for sliders. The server builds the Control components from the Board's declarations. They never come from the model's Express, so the model's passthrough list is unchanged.

## Considered Options

- **An Apply button.** Pure basic catalog, but a slider no longer updates as it moves.
- **A custom `Control` component that sends an action on change.** Simple wiring, but it is one more component to keep in step on both sides (ADR 0002), and it duplicates four basic components.
- **Send the selected values as text.** Simpler, but database text would then come from the client, and the server would have to match it against the options anyway.
- **Re-slice rows already in the browser** (Vega-Lite parameters). It can't reach new data, and it skips the server's checks. Still deferred, as in ADR 0024.

## Consequences

- "All" is a typed NULL binding. Board queries are written `(:method IS NULL OR method = :method)` and `(:agencies IS NULL OR agency = ANY(:agencies))`, which run with casts, and the startup check runs each query with every Control at its default and at NULL.
- A multi-select binds an array of its declared type. This extends ADR 0024's bindable types for Controls only. A Drill query still binds scalars.
- A `number` Control is integer-only, because the basic `Slider` has no step. A finer scale is a choice.
- The client sends the full Control state with a sequence number. The newest wins without cancelling running queries, because the executor can't cancel across requests.
- When the user next types a question, the model learns the Controls' state through a synthetic tool result (§15). It carries the Board's Result IDs, so the model can Cite them.
- Controls in the model's own answers (the model declaring parameters for its SQL) aren't covered here. They wait for a measurement of how much longer answers get.
