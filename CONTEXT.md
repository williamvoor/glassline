# Explorer

A data explorer that answers plain-English questions about a Postgres database with short prose plus a generated UI, where every number shown comes from the database. This glossary covers the explorer itself; terms of a dataset (such as AusTender's "contract" or "supplier") belong to that dataset's own glossary.

## Language

### Data

**Dataset**:
One database plus the configuration and hand-written notes that describe it to the agent (allowed schemas, notes, glossary). One running agent serves one Dataset.
_Avoid_: Database (for the whole bundle), source, domain

**Data dictionary**:
The descriptions of every table and column in the Allowed schemas, from the database's own comments, with any Comment overlay taking precedence. The agent's only description of the data.
_Avoid_: Schema prompt, docs

**Comment overlay**:
Descriptions of tables and columns kept with the Dataset's configuration instead of in the database, each replacing the database's description of the same table or column.
_Avoid_: Comment file, external comments

**Dataset context**:
The Dataset's notes, glossary and Data dictionary, as given to the agent. Identical for every question.
_Avoid_: Schema prompt, domain layer

**Allowed schema**:
A database schema the agent may read from. Anything outside the Allowed schemas does not exist as far as the agent is concerned.

**Result**:
The stored rows, columns and SQL of one query run in a Conversation, written by the agent (directly, or as a Drill query), referenced by a Result ID such as `r3`. Results are the only source of numbers on screen.
_Avoid_: Query result, dataset, data frame

### Conversation

**Conversation**:
The sequence of Turns sharing one context, together with its Results. It lives only as long as the running agent.
_Avoid_: Session, chat, thread

**Turn**:
One typed question and everything explored from it: a stack of Levels, of which one is shown at a time. The first Level is the agent's answer to the question.
_Avoid_: Message, exchange

**Level**:
What one question or Action produced inside a Turn: prose plus at most one Surface.
_Avoid_: Page, step, view

**Surface**:
The generated UI of one Level.
_Avoid_: Panel, widget, card

### Interaction

**Action**:
Something the user does on a Surface (clicking a mark, a row or a button) that produces a new Level in the same Turn.
_Avoid_: Event, interaction, user action

**Drill query**:
A query the agent writes ahead of time for a Visual, giving the next level of detail below any one of its rows, with that row's values filled in when it is used.
_Avoid_: Drill-through, sub-query, template query

**Drill-down**:
An Action that opens the next level of detail below one row of a Visual by running its Drill query. The agent is not consulted.
_Avoid_: Drill-through, zoom, filter

**Ask**:
An Action that consults the agent, always confirmed by the user first: about a row without a Drill query, about a Drill-down, or through a Follow-up.
_Avoid_: Question (a question is typed), request, query

**Follow-up**:
A button the agent offers on a Surface for a next step it can't already show; pressing it is an Ask.
_Avoid_: Suggestion, chip, next step

**Breadcrumb**:
The list of a Turn's Levels shown above its Surface, which lets the user go back to an earlier Level.
_Avoid_: History, trail, path

### Generated UI

**Macro**:
A component the agent writes that refers to a Result instead of carrying data; it is expanded into displayable components from that Result's rows. The agent never types data values itself.
_Avoid_: Template, helper

**Brief**:
A short instruction written by the model that ran the queries, telling the model that writes the Surface which Result answers which part of the question and how to show it. It names Results by ID and carries no data values.
_Avoid_: Prompt, intent (an intent is a user's own request, as used in training data), plan

**Visual**:
A chart, table or metric on a Surface, built by a Macro from one Result. Every Visual shows the SQL behind it.
_Avoid_: Widget, graphic

**Tone**:
A row's state, written as a value from a small fixed vocabulary (such as positive, negative, done, pending) in a column of the Result, and computed by the query. A Macro decides how each Tone looks; the model only names the column.
_Avoid_: Status (an ordinary data column), style, variant (a catalog property)

**Repair**:
A further attempt at a Level's Surface after it failed the checks, using the same Results.
_Avoid_: Retry (also used for model API retries), fix-up

**Cite**:
The Macro that places values from a Result inside a sentence, either in a Level's prose or on its Surface, so numbers in text are never retyped.
_Avoid_: Fact (reads as a data concept, as in "fact table"), quote
