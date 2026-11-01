# Context Map

## Contexts

- [Explorer](./CONTEXT.md): the A2UI data explorer, which answers plain-English questions about a Postgres dataset with prose and generated UI
- [glimmer-tune](./glimmer-tune/CONTEXT.md): the eval harness and training-data factory for Muse Glimmer 30B writing A2UI Express

## Relationships

- **glimmer-tune → Explorer**: glimmer-tune trains and evaluates the model that the Explorer may later call to generate A2UI. Both share A2UI's own terms (Surface, Catalog, Express) from upstream.
- **AusTender**: a Dataset that the Explorer serves. Its own terms live in `austender/SPEC.md`, not in either glossary.
