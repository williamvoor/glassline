# One merged catalog with its own ID

The agent and the client both use a single catalog, `urn:explorer:catalog:v1`, that merges the basic v0.9 catalog with `VegaChart`, `DataGrid` and `SqlDetails`. With `@a2ui/*@0.14.0`, a surface resolves components only in its own catalog, and a component missing from it renders as nothing, with no error (spike risk 1). Registering basic and VegaChart as separate catalogs, which looks simpler and is what the upstream samples do, gives blank charts after macro expansion. Don't split it back.
