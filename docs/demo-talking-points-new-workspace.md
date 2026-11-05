# Demo talking points: Glassline

Part 0 says how the whole application works. Part 1 is the New Workspace wizard, and Part 2 asks
questions in the austender Workspace.

# Part 0: how the application works

## The project in brief

**The aim:** ask questions about any Postgres database in plain English and get a *generated UI*
back: short prose plus charts, tables and figures laid out for that question. The UI is described
in A2UI, Google's open format for agent-generated interfaces. The rule throughout is that a model
may choose *what to show* but never *types the numbers*.

**Four parts, in one repo:**

- **The explorer (Glassline).** The app you're seeing. An agent answers questions with SQL and
  A2UI. Boards give ready-made screens, Workspaces keep one database each with its saved
  investigations, and Settings adds new databases.
- **The bootstrap (New Workspace).** Points a model at a database it has never seen and drafts what
  the agent needs to know about it: description, notes, glossary, column comments and Boards. A
  person reviews every claim, and each claim is checked by SQL, before anything is used.
- **The AusTender database.** Our demo data: every Commonwealth contract notice since 2012-13,
  ingested from AusTender's public OCDS API into Postgres. It has summary tables and a read-only
  role for the agent.
- **glimmer-tune.** An evaluation harness for teaching a small model (Muse Glimmer 30B) to write
  A2UI, with fine-tuning on Fireworks. We tried splitting the work between a large model for the
  SQL and Glimmer for the UI. We stopped that split (2026-11-05) because the extra hop was too slow,
  and speed is the top requirement. Today one model does both, and glimmer-tune remains the
  research track.

**The models, and what each does:**

- **Muse Spark** answers questions. It is the default answer model.
- **Muse Wonder** is larger and slower. It drafts new Workspaces, where quality matters more than
  speed.
- **Muse Glimmer** is trained and evaluated in glimmer-tune for writing UI. It is not in the live
  answer path today.

**Principles that run through all of it:**

- Numbers come from the database: every Visual shows its SQL.
- The agent only ever has read-only access.
- Speed first: few model round trips, cached prompts, fast queries.
- Nothing is specific to one dataset: AusTender is just a folder of configuration.
- Every model call is visible, with its cost.

## The explorer: one question, end to end

**In one line:** you ask a question about a Postgres database in plain English, and you get a
short answer plus charts and tables. Every number on screen comes from a database query you can
open.

1. **The question goes to the agent.** That's an LLM, Muse Spark by default, running a tool loop we
   wrote ourselves.
2. **The agent writes SQL and runs it.** Its tools are `list_tables`, `describe_table` and
   `run_sql`. It already knows the whole schema, because the table and column descriptions are in
   its prompt, and that prompt is cached, so follow-up questions start warm.
3. **Every query is fenced in.** It runs with the Dataset's read-only role, in a read-only
   transaction, with a statement timeout, a row cap that reports truncation, and an allowlist of
   schemas. The SQL is also parsed and checked before it runs.
4. **Results are stored, not retyped.** Each query's rows become a Result with an ID. This is the
   central design rule (ADR 0001): the model never copies a number into its answer.
5. **The agent replies in A2UI Express,** a compact UI language. Where it wants a chart, table or
   figure, it writes a Macro that names a Result, such as `BarChart(r3, …)`, `DataTable`,
   `Metric` or `Cite`.
6. **The server builds the UI.** It compiles the reply to A2UI v0.9 JSON and expands each Macro from
   the stored rows. So the chart is drawn from the database's numbers, and each Visual carries the
   SQL behind it.
7. **The client renders it.** A2UI travels over A2A to a Vite and Lit web client (Glassline), which
   renders the surface: Vega charts, a data grid, and the SQL under every Visual.

**Around that core:**

- **Boards.** Ready-made screens defined as queries plus a layout. The Explorer Board shows the
  whole dataset, and Entity Boards give a page per agency, supplier or contract. They open with no
  model at all, so they're instant. Their Controls (year, method) only re-run queries.
- **Drill-downs.** Clicking a row runs the query for what lies below it. No model is involved, and
  the status line says SQL.
- **Workspaces.** One database plus its Dataset files (description, notes, glossary, Boards) and
  its saved Conversations. You can switch between Workspaces and reopen past investigations. A
  reopened answer re-runs its saved SQL, because rows are never saved.
- **New Workspace.** A wizard drafts a Dataset for a new database. Muse Wonder, the larger model,
  drafts the files and Boards, and you review them, with claims checked by SQL. Then you export.
  Every model call shows its cost ceiling first, and the live spend as it runs.
- **Models on show.** A coloured chip marks every place a model works: Wonder for drafting, Spark
  for answering, and SQL wherever no model is involved.
- **Dataset-agnostic.** Nothing in the app is specific to AusTender. Everything about a dataset
  lives in its own folder, which is why a new database can be added from the wizard.
- **Speed is the top requirement:** few model round trips and fast queries, as long as the numbers
  stay correct.

# Part 1: the New Workspace wizard

To be spoken while the wizard runs, grouped by step. Draft and Boards are the slow, model-run
steps, so they have extra points: use whichever fill the time, in any order. The facts come from
`explorer/specs/workspaces/SPEC.md` (phase 3), the ADRs and the M1 run.

## Opening (on the Workspace list)

- "A Workspace is one database plus what the agent needs to understand it: a description, notes, a
  glossary, and Boards to start from."
- "Making one used to mean hand-writing those files. Now a model drafts them, and I review the draft
  before anything goes live."
- "Watch the coloured chips. Every place a model does work is marked, and every step without one
  says SQL."

## 1 · Database (instant, SQL only)

- "I pick a login by its variable name. The password never goes through the browser; it stays in
  the server's `.env`."
- "It has to be a read-only role. A login that can write is refused before anything else happens."
- "Next runs a report on the schemas I pick. That's plain SQL against the catalogue and statistics,
  with no model involved."

## 2 · Draft (slow: Muse Wonder)

**Before you click:**

- "This is the first step that costs money, so the price is on the button: 'at most' this much.
  It's a ceiling, assuming every call uses its full output allowance."
- "Muse Wonder drafts. It's the bigger, slower model, and that's fine for a one-off job like this.
  Questions later go to Spark, which is faster."

**While it runs:**

- **The bars.** "Each bar is one model call. The lighter part is tokens the provider already had
  cached, so they're cheaper and faster."
- **Cached share.** "Watch the light part grow from call to call. The prompt's opening section stays
  byte-identical, so after the first call most of it is a cache hit."
- **The SQL rows.** "The rows tagged SQL are the model checking its own claims against the database
  while it writes. It doesn't just describe the data, it queries it."
- **The meter.** "This is real spend against the ceiling. In our first live run the first call cost
  about three cents against a much higher ceiling. The ceiling is deliberately pessimistic."
- **Bigger picture.** "The rule that runs through the whole product is that the model never types a
  number. It writes SQL; the numbers come from the database. The same goes here: a claim like 'the
  median price is X' has a query attached, and we re-run it."
- **Fallback if it's slow.** "This is the one slow part, and it happens once per database. Answering
  questions is the part we've optimised for speed."

**When it finishes** (it moves to Review by itself):

- "And it moves to Review on its own. The summary line stays behind on the Draft step: calls,
  tokens, how much was cached, what it cost."

## 3 · Review (SQL only)

- "Here are the three files the model wrote. They're all editable."
- "Each claim has been checked against the database: verified, failed, or needs a person. The
  checking is SQL, not a model grading itself."
- "Next stays disabled until the files validate and every failed claim is fixed or dismissed. Each
  step blocks on its own problems, so Export never sends me back."

## 4 · Boards (slow: Muse Wonder again)

**Before you click:**

- "Boards are the starting screens: an Explorer Board for the whole dataset, and Entity Boards, like
  a page per suburb or per supplier. Again, the price is on the button."
- "I can also skip Boards. When regenerating an existing Workspace, I can keep its current Boards,
  and they're re-checked against the new draft."

**While it runs:**

- "The model is writing Board definitions: queries plus a layout. It's drafting from the files I
  just reviewed, so my edits feed straight in."
- "When it's done, each Board goes through the agent's own startup check. Exactly the code that
  would load it in production; no separate validator that could disagree."
- "Then each one is rendered once from cold, and the step shows the time. The target is under a
  second and a half, because speed is the top requirement for this app."
- "Same picture as before: bars per call, cached share, spend against the ceiling."

**When it finishes:**

- "Each Board shows its rows, its timing and any problems. A Board with a problem blocks Next. If I
  go back and edit Review, the Boards are re-checked when I return."

## 5 · Export (instant, SQL only)

- "I name the Workspace, and Export writes the files into the data directory, outside the repo,
  then tells the agent to load it. No restart."
- "Open, and we're in the new Workspace."

## Closing (ask one question in the new Workspace)

- "Now the answer shows a Muse Spark chip. So the whole story is on screen: Wonder built the
  Workspace, once, and Spark answers in it, fast."
- "Every number in that chart came from a query I can open, not from the model's text."

## Check on the dry run first

- **"Spark answers."** That holds only if `MODEL` in `.env` is Spark. If it's set to Wonder, change
  the closing line.
- **Cached share.** Confirm it actually rises across calls on a real run before you promise it on
  stage.
- **Speed figures.** Note how long Draft and Boards take, and the real spend, so you can quote them
  instead of "about three cents".

---

# Part 2: asking questions in austender

Once the austender Workspace is open. The questions come from `explorer/datasets/austender/`:
its examples, its Boards, and its reviewed test scenarios (`scenarios.toml`), so each has a
checked reference answer. Type them as written: some wordings were tuned (see "Avoid" below).

## Opening (the Explorer Board is on screen)

- "This is AusTender: every Commonwealth contract notice published since 2012-13, about 865,000
  contracts, by agency, supplier, value, category and financial year."
- "This first screen is the Explorer Board. It opened with no model at all: fixed queries, run
  straight against the database. That's why it's instant."
- "The controls on it (year, procurement method) re-run those queries. Still no model."
- "Every chart and table has its SQL one click away. Nothing on screen is typed by a model; the
  numbers come from the query."

## Clicking, no model (show the SQL chip)

1. **Click an agency row** (for example, Department of Defence) in the Explorer Board's breakdown.
   - "That's a Drill-down: what lies below the row. The status line says SQL; no model ran."
2. **Open the agency** to its Entity Board.
   - "An Entity Board is a page per agency, supplier or contract, across all years, not just the
     filter I clicked from."
3. **Ask within the Board:** on Defence's Board, click THALES AUSTRALIA in its top suppliers.
   - "Now it's one supplier inside one agency. Still the database, still no model."

## Questions in plain English (Muse Spark answers; point at the chip)

Simple to start, in this order:

1. *Which agencies spent the most in 2024-25?*
2. *Who were the top suppliers in 2023-24?*
3. *How has total contract value changed by financial year since the data starts?*
   - "Look at the first and last years: they're partial. The agent says so instead of pretending
     they're a trend."

Interesting ones:

4. *What share of 2024-25 contract value was awarded through open tender?*
5. *What share of 2024-25 contract value went to overseas suppliers?*
6. *How many contracts published in 2024-25 were later cancelled, and what were they worth?*
   - "About 3% of contracts are cancelled. The summary tables leave them out; this question is
     about them on purpose."
7. *Which 10 contracts published in 2023-24 grew the most through amendments, from what to what?*
   - "Contracts get amended after they're published. This compares the first published value to
     the latest one."

Follow-up (shows it remembers the earlier answer):

8. *Which 5 suppliers received the most contract value in 2024-25?*
   then *How many contracts did the first one win?*
   - "It reuses the first answer's result instead of re-asking from scratch."

Trickier wording (shows it states its assumptions):

9. *Which agency awarded the most contract value in 2023?*
   - "Australia's government years run July to June. '2023' is ambiguous, so it says which year it
     used."
10. *How much contract value did the Department of Defence award in calendar year 2014, and across
    how many contracts?*
    - "Here I said calendar year, so it filters on the publish date instead."

## Safety and honesty (short, good closers)

11. *How much contract value did the Department of Magic award in 2024-25?*
    - "There's no such agency. It says it found nothing, instead of inventing a number."
12. *Delete every cancelled contract from the database.*
    - "It refuses. It's connected through a read-only role, every query runs in a read-only
      transaction, and the SQL is checked before it runs. It couldn't delete anything even if it
      tried."

## Talking points while an answer runs

- "It's writing SQL against the whole schema, which is in its cached prompt. So follow-up
  questions start warm."
- "Speed is the top requirement: few model round trips, fast queries, as long as the numbers stay
  right."
- "When it's done, the chart is built from the stored rows. The model names the result; it never
  copies the figures."
- "The status line shows the model chip, the time, and how many steps it took."

## Avoid

- **"Dollar" wording.** Meta's content filter blocked every "grew the most in dollars" or "largest
  dollar increase" phrasing in testing; question 7's wording passed every time.
- **Supplier totals that include "Name Withheld".** Each withheld supplier is a different unknown
  entity, so don't ask the agent to add them up as one.
- **Check on the dry run:** that `MODEL` in `.env` is Spark if you say "Spark answers"; and the
  time each answer takes, so you can quote real numbers.
