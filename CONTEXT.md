# Explorer

A data explorer that answers plain-English questions about a Postgres database with short prose plus a generated UI, where every number shown comes from the database. This glossary covers the explorer itself; terms of a dataset (such as AusTender's "contract" or "supplier") belong to that dataset's own glossary.

## Language

### Data

**Dataset**:
One database plus the configuration and hand-written notes that describe it to the agent (allowed schemas, notes, glossary). One running agent serves one Dataset.
_Avoid_: Database (for the whole bundle), source, domain

**Data dictionary**:
The descriptions of every table and column in the Allowed schemas, stored in the database itself. The agent's only description of the data.
_Avoid_: Schema prompt, docs

**Dataset context**:
The Dataset's notes, glossary and Data dictionary, as given to the agent. Identical for every question.
_Avoid_: Schema prompt, domain layer

**Allowed schema**:
A database schema the agent may read from. Anything outside the Allowed schemas does not exist as far as the agent is concerned.

**Result**:
The stored rows, columns and SQL of one query the agent ran in a Conversation, referenced by a Result ID such as `r3`. Results are the only source of numbers on screen.
_Avoid_: Query result, dataset, data frame

### Conversation

**Conversation**:
The sequence of Turns sharing one context, together with its Results. It lives only as long as the running agent.
_Avoid_: Session, chat, thread

**Turn**:
One user question and the agent's answer to it: prose plus at most one Surface.
_Avoid_: Message, exchange

**Surface**:
The generated UI attached to one Turn.
_Avoid_: Panel, widget, card

### Generated UI

**Macro**:
A component the agent writes that refers to a Result instead of carrying data; it is expanded into displayable components from that Result's rows. The agent never types data values itself.
_Avoid_: Template, helper

**Visual**:
A chart, table or metric on a Surface, built by a Macro from one Result. Every Visual shows the SQL behind it.
_Avoid_: Widget, graphic

**Repair**:
A further attempt at a Turn's Surface after it failed the checks, using the same Results.
_Avoid_: Retry (also used for model API retries), fix-up

**Cite**:
The Macro that places values from a Result inside a sentence, either in the Turn's prose or on its Surface, so numbers in text are never retyped.
_Avoid_: Fact (reads as a data concept, as in "fact table"), quote
