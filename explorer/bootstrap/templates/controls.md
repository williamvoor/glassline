# Controls: guidance for drafting Boards

This is guidance for the model that drafts Boards. The agent never loads it. The rules themselves
are in the Board file rules; this says which Control to choose and how to write its queries.

## Which kind suits which column

- **`choice`**: a text or code column with a handful to about a hundred values, where one value at
  a time is the question ("which category?"). Give it `all_label = "All"` and `default = "all"`
  unless the Board makes no sense without a choice.
- **`multi`**: the same kind of column, when comparing a few values together is the question.
  `default = []` means all.
- **`toggle`**: a yes/no flag column, or a filter that is either on or off ("exclude cancelled").
- **`number`**: an integer threshold or a count of periods ("the last N years", "at least N rows").
  Integers only; a coarser or decimal scale is a `choice`.
- **`date`**: a cut-off or start date on a date column. `optional = true` lets the person clear it.

Prefer one or two Controls on the Explorer and none on an Entity Board. A Control that most
questions would not change is noise.

## "All" is a typed NULL

A nullable Control binds NULL for "All" (a `choice` with `all_label`, an empty `multi`, a cleared
optional `date`). Write the filter so NULL keeps every row:

```sql
WHERE (:category IS NULL OR category = :category)          -- choice
WHERE (:categories IS NULL OR category = ANY(:categories)) -- multi
WHERE (:since IS NULL OR start_date >= :since)            -- optional date
```

The agent adds the cast from the Control's `type`, so don't write one yourself.

## Option and range queries

- `options_sql` returns `value` and `label` columns, one row per option, in display order. It
  takes no placeholders: options don't depend on other Controls. A row with a NULL `value` is
  dropped, because NULL is "All".
- `range_sql` returns one row with `min` and `max` columns: integers for a `number`, dates for a
  `date`.
- Keep both cheap: they run before every cold render.
