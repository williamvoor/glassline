# Run 2 s225: gallery rows, worst first (2026-11-04)

Source: `runs/sft2/predictions/s225.full.jsonl`, scored in `runs/sft2/scores/s225.full.json` (name-blind slot F1). 84 dev gallery rows in 7 families. This is a written review. To read the rows yourself, open the review page:

```bash
uv run python -m a2ui_eval.review --predictions runs/sft2/predictions/s225.full.jsonl --scores runs/sft2/scores/s225.full.json
```

## Family means

| Family | s225 | base + 3 | base + 3 + grammar | Rows that don't compile (s225) |
|---|---|---|---|---|
| 33_financial-data-grid | **0.084** | 0.273 | 0.664 | 10 / 12 |
| 21_shipping-status | 0.434 | 0.330 | 0.330 | 2 / 12 |
| 32_advanced-form-validator | 0.553 | 0.434 | 0.338 | 0 |
| 03_calendar-day | 0.573 | 0.607 | 0.604 | 0 |
| 10_notification-permission | 0.629 | 0.741 | 0.741 | 0 |
| 14_sports-player | 0.660 | 0.436 | 0.366 | 1 / 12 |
| 27_stats-card | 0.731 | 0.495 | 0.489 | 0 |

## 1. 33_financial-data-grid: invented conditional styling (10 of 12 rows score 0)

The intents give a signed "24h Change" (`0.8`, `-1.1`). The reference shows it plainly: `Text(formatString("${change}%"), _, 1)`. s225 tries to style or sign it by the sign of the value on 11 of 12 rows, in five different syntaxes, none of which is Express:

```
change_data = Text(formatString("${/change}%"), if($change >= 0, "body", "body"))        # 0001, 0002, 0005, 0009
change = Text(formatString("${change >= 0 ? '+' : ''}${change}%"), $change >= 0 ? "body" : "body")   # 0003, 0011
change_value = Text(formatString("${/change}${/change >= 0 ? ' ▲' : ' ▼'}"), if $change >= 0 then "body" else "body")  # 0004, 0010
change_cell = Text($change24h, $change24h >= 0 ? "body" : "body")                         # 0007
```

- Both branches are often the same (`"body"`, `"body"`). The model has learned that variant can't vary, but it still writes the conditional.
- The run 2 prompt line ("no conditionals") didn't stop it.
- Inside list templates it also writes absolute paths in interpolations (`${/change}`, `${/name}`). Those bind to the root, not to the item.
- **With the grammar** (`s225+g`), the family still scores 0.084. With `if(` blocked, the model routes the same idea into a made-up named argument (`ifelseGreaterThanOrEqualToZero=$change, "positive", "negative"`) or into `${… ? … : …}` inside strings. The grammar leaves both open: named-argument names are free, and string contents aren't parsed.
- The untuned base with 3 examples has the same habit: operators on 7 of 12 rows, and 5 compile. With the grammar, all 12 compile, none goes outside the catalog, and the family scores 0.664. So the grammar redirects the untuned model into valid Express, while the tuned model routes around it.

**What it means for run 3.** Training has no rows that show a signed number plainly. A handful of synthetic rows with negative values and plain `formatString`/`formatNumber` display would target this directly.

## 2. 21_shipping-status 0010, 0011: conditional icons (score 0)

```
step3_indicator = Icon(if($/currentStep == "outForDelivery" or $/currentStep == "delivered", "check", "moreHoriz"))
step_icon = Icon(if($done, "check", "arrowForward"))
```

This is the same habit as above: a state turned into a choice inside a property. Item 7 (each item's own state icon from a field) should have taught `Icon($icon)`. On these two rows it didn't carry over.

## 3. 14_sports-player 0007: array passed as children (score 0, doesn't compile)

`stats_row = Row($/stats, "spaceAround")` passes the data array straight in, without `_template($/stats, stat_template)`, so `stat_template` can't be reached. This is a one-off.

## 4. Rows at 0.26–0.45 that render fine: style differences the score penalises

Most of the other low rows (shipping 0003–0008, form-validator 0000/0001/0004/0007, calendar 0004/0009) are plausible surfaces that differ from the reference in style, not in what they show. For example:

- **Literal or bound text.** s225 inlines `Text("Estimated delivery: Today by 8 PM")`, where the reference binds `$/eta`.
- **Static list or template.** It writes five hand-built step Rows instead of `_template($/steps, …)`.
- **Flat or nested data paths.** It uses `$/emailAddress` where the reference uses `$/formData/email`. It writes `?length(5, 5, …)` where the reference uses `?regex("^[0-9]{5}$", …)`.
- **Real but small errors.** A stray positional argument (`TextField(…, _, r"^\+?…$", ?regex(…))`), an icon chosen for a step the intent didn't give one for.

From reading these rows, the blind slot score underrates such rows next to their rendered result. Below about 0.45, the score mixes real failures (sections 1–3) with style choices. A render-based check would separate the two. I haven't measured that.

## Summary

| Cause | Rows (s225 gallery) | Fixed by the grammar? |
|---|---|---|
| Conditional styling or icons (`if`, `?:`, `>=`) | 13 (11 financial, 2 shipping) | Not for s225: it moves into named arguments and strings. Yes for base + 3 (financial 0.273 → 0.664) |
| Array as children, unreachable template | 1 | No |
| Style divergence from the reference (renders fine) | most of the 0.26–0.45 band | n/a |
