---
status: accepted
---

# Per-row state on a Visual comes from a Tone column that SQL computes

Questions often ask for rows to look different by their state: "show losses differently", "mark completed steps". Run 1 of the Glimmer tuning showed that a model with no way to express this invents conditionals (`$x ? a : b`, `if($change >= 0, …)`) that Express and the v0.9 basic catalog don't have (glimmer-tune tuning report, section 7). In the explorer, Glimmer never sees rows. A Macro builds each Visual from the stored Result (ADR 0001), and Glimmer sees only the Brief and the Result schemas (ADR 0014). So the question is not how Glimmer binds per-row values, but where a row's state is computed and how a Macro is told to use it.

We decided (2026-11-02): Spark computes the state in SQL, as a column whose values come from a small, fixed Tone vocabulary (for example `positive`, `negative`, `neutral`, `done`, `pending`). The Brief names the column. Glimmer passes it to the Macro by name, for example `DataTable(r3, tone="change_tone")`. The Macro maps tone values to presentation (text variant, icon, emphasis) in code. No expression language reaches the model or the renderer.

## Considered options

- **(a) SQL computes it (chosen).** Spark adds a derived column during the tool loop, such as `CASE WHEN change >= 0 THEN 'positive' ELSE 'negative' END AS change_tone`.
  - The state is data, so it is shown in the Visual's SQL and checked like every other number (ADR 0001).
  - It costs no extra model round trip: it is part of a query Spark already writes.
  - Glimmer sees the column in the Result schema and needs no new skill beyond naming it.
  - The vocabulary keeps Spark from having to know catalog icon names or variant enums.
  - Cost: Spark has to anticipate the presentation need while querying. If it doesn't, Glimmer can only show the rows plainly. That's a weaker answer, but not a wrong one.
- **(b) Code computes it.** A Macro or post-processor adds presentation fields to the Result, by rule: a signed delta column is coloured by sign, a boolean column becomes a tick.
  - It is free and deterministic, and doesn't depend on any model.
  - It covers only the cases the rules foresee. "Completed steps" in a status column of free text is out of reach.
  - It is a good default under (a) for the obvious cases, not a replacement for it.
- **(c) The catalog supports it.** A custom explorer catalog function such as `select(cond, a, b)` plus comparison functions, implemented by the renderer.
  - This needs renderer work in the Lit client and changes the merged catalog (ADR 0002).
  - It teaches the explorer's Glimmer the opposite of the generic training, where state lives in the data.
  - It gives the model an expression language in the Surface, which makes every Visual harder to check against its SQL.
  - In the explorer it doesn't even fit the input: Glimmer doesn't see row values, so it can't write conditions over them with any confidence.

## Consequences

- The tone vocabulary and each Macro's `tone=` argument become part of the explorer's Macro spec (owned with the two-model split, ADR 0014). This ADR fixes only the principle.
- Spark's instructions gain a short paragraph: when the question asks for rows to look different by state, add a tone column from the vocabulary and name it in the Brief.
- Glimmer training for the explorer (run 3) needs Briefs that name a tone column, and references that pass it to the Macro.
- The generic run 2 is unaffected. The basic catalog has no logic, so its references store presentation values per item in the data model (`{icon: "check", …}` with `Icon($icon)`), whatever the explorer picks.
- Rule-based tones from (b) can be added later as defaults, with no change to the model's side.
