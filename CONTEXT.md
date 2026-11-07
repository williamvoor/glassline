# Explorer

A data explorer that answers plain-English questions about a Postgres database with short prose plus a generated UI, where every number shown comes from the database. This glossary covers the explorer itself; terms of a dataset (such as AusTender's "contract" or "supplier") belong to that dataset's own glossary.

## Language

### Data

**Connection**:
One named, read-only login to a Postgres database. Each distinct login is one Connection; several Datasets may share it.
_Avoid_: Database (for the login), DSN, data source

**Dataset**:
The configuration and hand-written notes that describe part of a Connection's database to the agent (allowed schemas, notes, glossary). Each Workspace has exactly one Dataset. Datasets are deployment data, not part of the explorer (ADR 0045).
_Avoid_: Database (for the whole bundle), source, domain

**Workspace**:
One Dataset together with its saved Conversations: what the user switches between. A Workspace can be unavailable (its database can't be reached, or its login can write); its Conversations are kept but can't be opened until it is available again.
_Avoid_: Project, database, Connection, Dataset (for the pair)

**Draft**:
A proposed Dataset (and optionally its Boards) written by a model from a database's report, which the user reviews and edits before it becomes a Workspace. Nothing in a Draft is used to answer questions until it is exported.
_Avoid_: Bootstrap (for the result), proposal, generated dataset

**New Workspace**:
The guided steps that turn a database login into a Workspace: Database, Draft, Review, Boards, Export. **Regenerate** runs the same steps for an existing Workspace, keeping its database and name.
_Avoid_: Bootstrap, setup, onboarding

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
The sequence of Turns sharing one context, together with its Results. It is saved in its Workspace and can be resumed; a saved Result keeps its SQL, not its rows, which are fetched again when needed.
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

**Sidebar**:
The client's dark left column: the brand, the Workspace picker, saved Conversations, the investigation log of Log entries, and the composer.
_Avoid_: Nav, history

**Canvas header**:
The bar above the Canvas: the tab strip and Settings.
_Avoid_: Top bar, toolbar

**Canvas**:
The client's main area, below the Canvas header: the selected Tab.
_Avoid_: Main view, page

**Tab**:
One Level on the Canvas, opened by a typed question, an Entity Board, a Drill-down or an Ask, and kept in a strip for the whole Conversation. The Explorer Board is always the first Tab. Not A2UI's `Tabs` component, which can appear inside a Surface.
_Avoid_: Page, panel, window

**Log entry**:
One thing done in the investigation, listed in the Sidebar: a question, opening a Board, a Drill-down, an Ask, or the Control changes made on one Tab in a row. Selecting a Log entry shows its Tab.
_Avoid_: Step (a step is a status-line step of a model run), cycle, event

**Trace**:
A record, for debugging, of everything the Explorer did behind a Conversation: each call to and from a model and each SQL query run against the Dataset. It is made of **Trace events**. A Trace is recorded only while the user has it switched on, and never holds data rows. A Conversation's Trace is grouped by Turn, then by Level; a New Workspace or Regenerate run has its own Trace, kept with its Workspace. A Trace is not the investigation log: Log entries are what the user did, and Trace events are what the Explorer did.
_Avoid_: Debug log, log (taken by Log entry), audit

### Interaction

**Action**:
Something the user does on a Surface (clicking a mark, a row or a button, or changing a Control). A click produces a new Level and Tab; a Control change updates its Tab in place.
_Avoid_: Event, interaction, user action

**Drill query**:
A query the agent writes ahead of time for a Visual, giving the next level of detail below any one of its rows, with that row's values filled in when it is used.
_Avoid_: Drill-through, sub-query, template query

**Drill-down**:
An Action that opens the next level of detail below one row of a Visual by running its Drill query. The agent is not consulted.
_Avoid_: Drill-through, zoom, filter

**Ask**:
An Action that consults the agent, always confirmed by the user first: about a row without a Drill query, about a row that could open an Entity Board but is asked about within the filters of the view it was clicked in, about a Drill-down, or through a Follow-up.
_Avoid_: Question (a question is typed), request, query

**Follow-up**:
A button the agent offers on a Surface for a next step it can't already show; pressing it is an Ask.
_Avoid_: Suggestion, chip, next step

**Control**:
An input on a Board (a choice, a multi-select, a toggle, a number slider or a date) bound to placeholders in the Board's queries. Changing it re-runs those queries in place, without the agent. A click on a Board's row keeps its Controls' values (a Drill-down, or an Ask within filters). A question typed while the Board is in view doesn't have to.
_Avoid_: Filter (a filter is part of a query), input, widget

**Breadcrumb** (retired by SPEC §18):
Was the list of a Turn's Levels above its Surface; replaced by Tabs.

### Generated UI

**Board**:
A page written with the Dataset and reviewed by a person: named, parameterised queries, Controls and a layout of Macro calls. It is rendered by running its queries, without the agent.
_Avoid_: Dashboard, template, view

**Explorer Board**:
The Dataset's landing Board, which has no key and is shown as the first Tab, titled "Explorer".
_Avoid_: Explorer (alone, it names the app), home, landing page, overview

**Entity Board**:
A Board keyed on one column, about that one entity across the whole Dataset. A click on a row that carries that column offers it, next to an Ask within the clicked view's filters.
_Avoid_: Detail page, profile

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
