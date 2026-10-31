---
status: accepted
---

# v0.9 on the wire, version-neutral Express in training

The explorer sends A2UI v0.9 with the v0.9 basic catalog, because `@a2ui/lit@0.14.0` (latest) has renderers for v0.8 and v0.9 only. `@a2ui/web_core` has v1.0 logic, but nothing renders it in Lit, and v1.0 is still a candidate spec. glimmer-tune's seeds are the v1.0 gallery, but the model is trained on Express, not on JSON. The Express compiler picks `path` or `@path` (and `call` or `@call`) from the target catalog's protocol version. All 240 milestone 2a targets compile and validate against both the v1 and the v0.9 basic catalogs, so the protocol version is decided at compile time, not in the training data.

## Considered options

- **v1.0 everywhere:** rejected. We would have to write our own Lit components on `web_core/v1_0`, vendor VegaChart's v1 build, and work around the macros catalog, which is hardcoded to `V0_9_1` (`transformers/macros/expander.py:147`).
- **Convert glimmer-tune's seeds to v0.9:** not needed. Express already compiles to both versions.

## Consequences

> Amended by [ADR 0008](0008-train-generic-express-on-the-v0-9-basic-catalog.md): glimmer-tune trains generic Express on the v0.9 basic catalog, without the explorer's macros or prompt. The first two consequences below no longer apply to glimmer-tune. ADR 0008 also corrects the claim that Express is version-neutral: positional slots follow each catalog's prop order, so the same Express can compile to different props.

- glimmer-tune validates Express against the explorer's catalog (v0.9 basic plus macros) once that catalog exists, not against the v1 basic catalog.
- From milestone 2b on, the training system prompt is imported from the explorer (byte-equality test). The 2a prompt, built from the v1 basic catalog, is used only for the smoke run and the gallery families.
- The v0.9 check accepts props the v0.9 catalog lacks (`TextField.placeholder` and `Video.posterUrl` are v1 only), so "validates against v0.9" does not prove the output is pure v0.9. glimmer-tune needs a strict check that rejects props unknown to the explorer's catalog, and gallery rows that use v1-only props must not reach explorer-shaped training rows.
- Revisit when `@a2ui/lit` ships a v1.0 renderer.
