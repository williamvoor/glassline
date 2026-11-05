# Gallery seeds

`gallery/*.json` are copied unchanged from the A2UI repo,
`catalogs/basic/v1/examples/`, at commit `825beac2813a6d0ccef3b6fb4124ad6c3c8419f0`
(the same commit as the SDK pin and `catalogs/basic_v1.json`).

They are licensed under the Apache License 2.0, copyright the A2UI authors. See the
`LICENSE` file at the root of https://github.com/a2ui-project/a2ui.

Each file is one family (ADR 0004). `data/splits.yaml` assigns each family to a split, and
`archive/families/<stem>.yaml` holds its retired slot template. Don't edit these files: if a seed changes,
re-copy it from upstream and bump the commit above.
