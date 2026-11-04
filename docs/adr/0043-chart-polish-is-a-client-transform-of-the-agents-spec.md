---
status: accepted
---

# Chart polish is a client transform of the agent's spec

To make charts look better (an area under single-series lines, a hover fade on bars, rounded bars, quieter axes, a themed tooltip), the vendored VegaChart reshapes the Vega-Lite spec the agent sends before it renders it, and the Macros keep writing plain marks. The gradient's colour is a token only the browser knows, and ADR 0026 keeps look out of the agent, so the polish lives with the theme. The agent's only additions are structural: an optional `weight` on Visual Macros and an optional `trend` Result on `Metric`, both drawn from stored rows (ADR 0001).

## Considered Options

- **Macros emit the layered area and the hover params.** Rejected: it puts look into the agent, the gradient would need a colour the agent can't know, and every restyle would change test fixtures.
- **Config only.** Not enough: Vega config can't add a layer or a selection param.

## Consequences

- The spec on screen differs from the spec the agent sent. A test pins each transform. A spec that already has `params`, an `opacity` encoding or a `color` series is left alone.
- The vendored VegaChart drifts further from upstream; its `NOTICE` lists the transforms.
