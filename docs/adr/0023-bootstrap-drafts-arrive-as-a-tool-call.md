---
status: accepted
---

# The bootstrap draft arrives as a `submit_draft` tool call, not through `response_format`

The build prompt asked for the draft as structured output (`response_format: json_schema`) at the end of a `run_sql` tool loop. In LiteLLM 1.106.6 the `meta` provider forwards `response_format` unchanged, but Meta's docs don't say whether it can be sent with `tools` in the same request, and dropping `tools` for a final call whose history holds tool calls is equally undocumented. So the model hands in its draft by calling a second tool, `submit_draft`, whose parameters are the draft schema. Function calling is the one feature verified for both Muse models. We validate the arguments ourselves and return any problem to the model as a tool error, so the design works whether or not Meta constrains tool arguments to the schema.

## Considered Options

- **`response_format` on every call, with tools.** Rejected for now: unverified for this provider; a failure would only show up in the live run.
- **A final call with `response_format` and no tools.** Rejected: one extra call every run, and the same unverified request shape.
- **Responses API.** Deferred (build prompt): LiteLLM support for it with Meta is unverified.

## Consequences

- Revisit once a live run shows `response_format` with `tools` works: constrained decoding would make the validation retry rare.
- The draft schema avoids `oneOf`, keeps `additionalProperties: false` and lists every key in `required`, so it stays inside Meta's strict subset if we later switch.
