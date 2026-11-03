---
status: accepted
---

# Look and feel lives in the client; the agent sends structure, not style

The explorer takes its look from the "Follow the Contract" prototype and must support light and dark themes. We decided the agent's output carries structure only (a Card around each Visual) and no colours, fonts or Vega `config`. All styling is CSS tokens in the client, mapped onto A2UI's `--a2ui-*` variables, and the vendored VegaChart builds its Vega config from the same tokens when it renders. The viewer's colour scheme is only known in the browser, and a chart drawn with the agent's colours could not follow it.

## Considered Options

- **Vega theme in the Macros** (the first plan). Rejected: the agent can't know the viewer's scheme, so colours would live in two places and dark mode would need the client to override them anyway.
- **A2UI surface `theme` (`primaryColor`) from the agent.** It sets one colour inline per component and would override the page tokens, so it is not sent.

## Consequences

- Restyling never changes the agent, the Express prompt or the test fixtures.
- The vendored VegaChart differs from upstream in how it builds its default config; the change is noted in its `NOTICE`.
