# Item 7 sample review: generator fixes (2026-11-03)

Source: the 10 item 7 checkpoint samples served by `python -m a2ui_eval.review --synthetic …/item7_samples/cases.jsonl --sample 10`, read in full by the assistant (Claude). Muse Spark Open reviewed the same 10 rows and flagged 1 of the ~8 rows with problems, so its verdicts are not used here.

**Caveat:** this is 10 rows. Before fixing anything in section B or C, count how often each problem occurs in a full-size dry run (a linter rule or a one-off script), and record the counts next to each fix.

Each fix follows AGENTS.md: a failing regression test first. Where possible, add the check as a linter rule (`lint.py`, S8), so it guards every future row.

## A. Item 7 bug: fix before full-size generation

**A1. A checklist item shows its title twice.** In `notification_list/00000`, each item is `CheckBox($playlistTitle, $isShared)` followed by `Text($playlistTitle)`. The checkbox label is bound to the first column (`grow.py` `_item`, the `extra["kind"] == "check"` branch, `label: cols[0]`), and the loop below it still adds a Text for every column, including `cols[0]`.
- Fix: when the item has a checklist, skip `cols[0]` in the value Texts. The intent must then also stop quoting the first column as a separate value; check `intents.py` around line 500, where checklist labels are handled.
- Test: no item has a CheckBox label and a Text bound to the same field (`test_synth_plausibility.py`, next to C5 `test_checklists_bind_per_item`).

## B. Coherence fixes in code (no model, no vocabulary change)

These were accepted as "word-level coherence" for run 1. Item 7 adds more icons and media, so they now show up more. Listed in priority order.

**B1. A heading over a list is unrelated to the list.** Examples: "House rules" over upcoming reservations (`notification_list/00005`), "Mood tags" over playlists (`/00000`), "Confirm transfer" over saved recipients (`/00001`). The heading comes from `b_section` → `_take("section_titles")` (`grow.py:273`), with no link to the list after it (skeleton `notification_list.yaml`: `section`, then `list`).
- Fix: a section heading placed directly before a list uses the list entity's name ("Upcoming reservations"). The skeleton order is already known when the tree is built.
- Test: in every skeleton where `section` is directly followed by `list`, the heading text equals the entity name.

**B2. Icons in icon rows are random topic icons.** Examples: a bin icon next to "Pickup time" and a share icon next to "Downtown Market" (`faq_sections/00003`), a tick next to an email (`tabbed_detail/00002`). `b_icon_row` picks `rng.choice(self._icons())` (`grow.py:460`), and `_icons()` (`grow.py:191`) is every action and entity icon of the topic (add, share, delete, folder).
- Code-only fix: build an icon row only when the field has an icon that fits it. Today nothing gives a field an icon, so this needs C1. Until then, lower the icon-row weight, or allow only the entity icon for that entity's own fields.
- Test: an icon row's icon is never an action icon (delete, share, add).

**B3. Too many standalone media, all labelled with the topic name.** `faq_sections/00000` is a billing screen with 4 audio players, 3 videos and 3 images, all "Media subscription billing". `b_media` (`grow.py:284`) is in `DEFAULT_NESTED` (`grow.py:19`), so any open slot in any skeleton can take it. Per-item media from item 7 now covers the media binding cells, so standalone media are needed less.
- Fix: cap standalone media at one per surface, outside the media skeletons (`media_gallery`, `player_panel`). Then check that coverage still reaches every media cell.
- Test: per-surface standalone media count ≤ 1 outside the media skeletons.

**B4. Decimals on whole numbers.** "Screens allowed: 1.00" (`faq_sections/00000`). `fill.py:265` picks `decimals` from `[0, 2]` regardless of the value.
- Fix: `decimals: 2` only for non-integer values, or for money.
- Test: no `formatNumber(…, decimals: 2)` on an integer value.

**B5. Money and dates shown raw.** "Total trip cost" as a plain number, coverage limits `50000` unformatted (`checkout_summary/00000`), and raw ISO dates ("2027-04-10", "2026-11-10") in several rows. `_value_plan` (`grow.py:345-370`) offers `formatCurrency` as just one option for money labels, and dates get `formatDate` only by chance (`grow.py:357`, and `_column_format` at 50%). The tuning report already lists raw ISO timestamps as a weak spot.
- Check first: how the gallery references show money and dates. If they always format them, make formatting the rule for money labels and ISO dates, and keep the plain binding forms for other kinds.
- Test: per the decision.

**B6. Empty or dangling structure.**
- A tab whose only content is a divider ("Fresh Produce" with a line, `faq_sections/00003`). `b_tabs` (`grow.py:591`) only falls back to a body when there are no children at all.
- A section heading as the last child of its container, with nothing under it ("Invoice List", "Active Plans", `faq_sections/00000`).
- Fix: a tab needs at least one child that isn't a divider, and a section heading must be followed by content in its container.
- Linter rules, with tests that fire on these two rows.

**B7. Nested data paths under a random section** (low priority). "Trip title from the 'Day by day plan' part of the data" (`dashboard_grid/00004`). The `nested` option in `_value_plan` (`grow.py:364`) prefixes a random section title. It reads oddly in the intent but does no harm to the reference. Leave it unless B1–B6 are done early.

## C. Vocabulary-level coherence (needs a spec; Spark Open may write it, ADR 0025)

Some coherence problems can't be fixed in code, because the vocabulary has no links between its words: tab titles versus tab content ("Dairy And Eggs" holding pickup details), a button versus the form it submits ("Lock card" submits a statement period), entity icons versus records (a shopping cart on airline transactions).

**C1. Proposal:** extend each topic's vocabulary with links, written by a model and validated like today's vocabulary:
- `fields[].icon`: an icon from the catalog enum that fits the field, or none;
- `section_titles[].fields` / `.entities`: which fields and lists belong under each section, so tabs and headings hold what their titles say;
- `actions[].fields`: which inputs an action submits.

The generator then picks within a group instead of from the whole topic. Spark Open is allowed for this by ADR 0025 once a spec section approves it. The cost is small: 10 domains × 8 topics, with a few calls each.

**Timing:** item 7 replaces the synthetic set (decision 2), and run 2 launches by 2026-11-12. C1 means a vocabulary schema change, new vocabulary files, user review of the diff, and regeneration. That takes about a day and puts the launch at risk. The user decides whether C1 goes into run 2 or waits for run 3. B1–B6 don't depend on it.

## Suggested order

1. A1 (item 7 bug).
2. Count B1–B6 over a dry run.
3. B1, B4 and B6 (small, code-only), then B3 and B5.
4. B2 as a weight change now, and fully with C1.
5. Regenerate, then show 10 new samples to the user.
6. C1, if the user wants it in run 2.

## Dry-run counts (2026-11-03)

Counted over a 3640-row synthetic train run (seed 0, default config, after the A1 fix; no model, no cost). The script is a one-off in the session scratchpad and its checks are heuristics: B1 compares heading text with the list entity's name; B2 counts only icons that are one of the topic's action icons; B5 counts Text bound by a plain path to a money-labelled number or an ISO date. The run has no coverage gaps.

| Item | Rows affected | Instances |
|---|---|---|
| B1 heading over a list it doesn't name | 249 (6.8%) | 249 of 251 heading-then-list pairs |
| B2 icon row using a topic action icon | 315 (8.7%) | 414 of 542 icon rows |
| B3 more than 1 standalone media outside the media skeletons | 372 (10.2%) | standalone media per surface: 0 in 2210 rows, 1 in 878, 2 in 210, 3 in 154, 4+ in 188 |
| B4 2 decimals on an integer | 42 (1.2%) | 60 |
| B5 ISO date shown raw | 211 (5.8%) | 258 |
| B5 money shown raw | 89 (2.4%) | 139 |
| B6 heading last in its container | 121 (3.3%) | 127 |
| B6 tab with only a divider | 23 (0.6%) | 24 |

A1 is fixed (regression tests `test_checklist_label_is_not_repeated` and `test_checklist_intent_names_the_label_once`).

## After the B1, B3, B4, B6 fixes (2026-11-03)

Same 3640-row dry run (seed 0), after the fixes in SPEC "Sample review fixes B1, B3, B4, B6": B1 unrelated headings 249 → 0 rows; B3 more than one standalone media outside media skeletons 372 → 0 (surfaces with 2+ standalone media now occur only in media skeletons); B4 2 decimals on an integer 42 → 0; B6 trailing heading 121 → 0 and dividers-only tab 23 → 0. A refuter found that the first B6 version left dangling dividers and divider-only Cards; runs now drop dividers at either end, and a dividers-only run is empty. B2 (8.2%) and B5 (5.8% raw dates, 2.2% raw money) are unchanged, as planned. Coverage at full size, seeds 0 and 1: no gaps, minimum 30.

## Second review: 20 fresh rows after the fixes (2026-11-03)

Source: 400 train rows generated with the current generator (seed 7, default config, written to the assistant's scratchpad; `data/synth` untouched), of which 20 were picked at random (seed 7). The assistant wrote an answer key before any model reviewed them: 11 rows to reject, 16 major problems. Spark Open (twice) and Sonnet 5.5 then reviewed the same rows with the same checklist prompt (comparison below). Row ids are from that seed-7 run, not from `data/synth`.

B1, B3, B4 and B6 didn't come up again. These are new findings, with the row that shows each one.

**D1. A checklist hides what its tick means** (item 7, decision 3). Each item is `CheckBox($planName, $emergencySupport)`: the box is labelled with the item's name but ticked for a different field. "Emergency support" (`checkout_summary/00003`) and "Walk Ins Welcome" (`dashboard_grid/00021`) never appear on screen, so a reader sees a ticked plan or clinic and can't tell why. A checkbox also reads as something to tick, not as a fact about the record.
- Options: label the box with the field ("Walk-ins welcome") and show the item name as text next to it; or use checklists only where the field is a choice the user makes (for example, "Selected"); or drop checklists in favour of state icons for facts. The intent changes to match.
- Test: no checklist's label is the item name while its value is a different field, unless the label states that field.

**D2. Buttons that don't fit what they submit.** This is the most common major problem: 6 of the 11 rows to reject.
- "Call Trainer" submits a session date (`player_panel/00004`).
- "Change pharmacy" submits a refill quantity (`search_results/00001`).
- "Cancel booking" is the main button that submits a "review before confirming" form (`settings_form/00004`).
- "Upgrade Plan" submits a cancellation form (`wizard_step/00014`).
- "Contact support" opens a pop-up about upcoming payments (`confirmation_dialog/00019`).
- "Edit cart" opens a pop-up about contact details and rewards (`confirmation_dialog/00002`).
- Cause: the submit button is any topic action, and the pop-up content is any section and body text (the same root cause as C1). Code-only options: give the form's submit role to actions whose label suggests submitting (save, add, send, submit, confirm, book, log), never to cancel or delete actions; and make a pop-up's heading the button's own label. The full fix is C1's `actions[].fields`.
- Test: a form's submit button is never a cancel or delete action; a modal's first heading names its trigger.

**D3. One value shown twice on a screen.** "Category" and "Spending Category" both show "Housing" (`detail_sheet/00029`). "Title" and "Job title" both show "Support Specialist" (`faq_sections/00003`). Two fields from different parts of the data carry the same example value.
- Fix: within one surface, two value displays can't show the same value under labels that differ only by a prefix; or the data model picks different examples.
- Test: no surface shows the same non-trivial string value under two labels.

**D4. Two dividers in a row** (`detail_sheet/00029`: "then a line, then a line"). The B6 fix drops dividers at either end of a run, but not two next to each other.
- Fix: collapse adjacent dividers. Linter rule.

**D5. Counts with nonsense nouns.** "Reimbursement totals: 4 totals" (`checkout_summary/00001`). The count pattern pluralises the label's last word, which isn't always a countable thing ("totals", "progress", "summary").
- Fix: use the count pattern only when the label names countable things. A stop list ("totals", "progress", "summary", "status", "history") or a vocabulary flag would do.

**D6. Validation messages that contradict the rule.** "Cancellation reason" has the regex `^[\s\S]{5,}$` (five or more characters), but its length check says "needs at least 2 characters" (`wizard_step/00014`). Two independent picks set the same constraint.
- Fix: when a regex and a length check land on the same field, derive both from one minimum.
- Test: a field's regex minimum and its length-check minimum agree.

Still open from section B (as planned): money shown raw ("Monthly Limit 400", "Expected salary 52000") is B5, and the folder icon on courses and the error icon for a disliked meal are B2.

### Reviewer comparison (for automated review)

Same checklist prompt for both models (`review_prompt_v2.txt` in the assistant's scratchpad), Spark Open at high reasoning. Scored against the answer key:

| | Spark Open run A | Spark Open run B | Sonnet 5.5 |
|---|---|---|---|
| Verdict agrees with the key | 15/20 | 16/20 | 15/20 |
| Rejects caught (of 11) | 8 | 9 | 11 |
| False rejects (of 9) | 2 | 2 | 5 |
| Major problems found (of 16) | 9 | 8 | 12 |
| Cost | $0.40 | $0.40 | in plan |

- Spark Open is cautious and not stable: the two runs at temperature 0 differ on 3 of 20 verdicts. Taken together they find 11 of 16.
- It misses meaning-level problems: neither run caught D1. Nobody caught D4, D5 or D6, so the linter has to.
- The answer key is the assistant's own judgement, which may favour Sonnet. 20 rows is a small sample.
- Use: Spark Open, run twice and merged, as a cheap first pass on checkpoint samples (about $0.04 per row). Its rejections are worth reading; its approvals are not a substitute for the user's own read. At about $0.02 per row per run, reviewing the whole synthetic set is not affordable.

### Suggested order

1. D2's code-only part (submit role and pop-up heading). It is the most common major problem.
2. D1 (a decision on how checklists show their field), then D3–D6. Each is small, and D4–D6 belong in the linter.
3. Regenerate, then 20 new samples: the user's read plus Spark Open run twice.
