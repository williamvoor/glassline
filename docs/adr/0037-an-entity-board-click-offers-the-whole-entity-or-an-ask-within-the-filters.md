---
status: proposed
---

# An Entity Board click offers the whole entity or an Ask within the filters

ADR 0028 opens an Entity Board for the whole entity and drops the clicked view's filters. That holds for a click from an answer, but it misreads an onward click: a supplier row on the Defence Board asks "what did Defence buy from this supplier?", and the Supplier Board answers a different question (the user, 2026-11-04, manual check 3 of slice 4 Part B). ADR 0028's way out, "its Controls let the user narrow it", doesn't exist: the Entity Boards have no Controls, and an agency isn't a Control anyway, it is the Agency Board's key.

So every row click that would open an Entity Board now asks first, with two choices: **open the Entity Board** (the whole entity, no model, as in ADR 0028) or **Ask within these filters** (the existing confirmed Ask, so the model writes SQL that keeps the clicked view's filters, period, and on a Board its key and Controls). No Board file is added or changed. ADR 0028 stands for what an Entity Board shows.

## Considered Options

- **Pair Boards** (an Agency × Supplier Board keyed on both). Instant and exact, but every pair of entity types needs one more drafted and reviewed Board file, and Board keys would need several parts. The user doesn't want more pregenerated Board files.
- **Carry Controls over by name.** Instant, but it does nothing today (Entity Boards have no Controls) and can never keep the parent Board's key.
- **The server adds the filters to the target Board's SQL.** Not possible in general: Board queries are arbitrary SQL.
- **Ask only when filters would be lost** (an Entity Board, or Controls off their defaults). One click fewer in the common case. Rejected by the user (2026-11-04) in favour of the same choice on every click, so the behaviour is predictable.

## Consequences

- Every Entity Board click costs one more click. The Board path stays without a model call, and the top row's prefetch still applies, so it opens as fast as before once chosen.
- The filtered path costs a model run (about 10–20 s) and is only as correct as the model's SQL, which is shown as for every Ask.
- A click on the Explorer tab at its defaults still shows both choices, though the Ask then has no filters to keep beyond its period.
- ADR 0028's "its Controls let the user narrow it" is withdrawn; this ADR replaces that consequence.
