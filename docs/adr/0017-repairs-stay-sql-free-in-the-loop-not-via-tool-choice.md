---
status: accepted
---

# Repairs stay SQL-free in the loop, not through `tool_choice`

A Repair re-renders the view from the same Results and must not run SQL. The llm-loop spec sent Repair calls with `tool_choice="none"`, noting that Meta's support for it was unverified. A live check on 2026-11-02 showed that Meta rejects it: *only `"auto"` is supported for `tool_choice`; `"none"`, `"required"`, and named function choices are not currently supported* (HTTP 400). So every Repair failed, and any UI error ended the Turn with "The model service failed". No earlier live run had needed a Repair, so this went unseen. Repair calls now send no `tool_choice`. The loop already refused to run tool calls returned during a Repair, and counted them as a failed Repair; that is now the only guard.

## Considered Options

- **Drop `tools` from Repair calls.** Rejected: the tool list is part of the cached prompt prefix, and changing it would likely make the Repair call miss the cache.
- **Run the tool calls if a Repair makes them.** Rejected: a Repair should re-render, not re-query. It would cost round trips and could change the Results the prose already cites.

## Consequences

- Don't add `tool_choice` back without checking the provider accepts the value. The regression test's stub rejects anything but `"auto"`.
