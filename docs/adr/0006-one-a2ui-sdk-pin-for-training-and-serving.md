---
status: accepted
---

# One A2UI SDK pin for training and serving

The explorer and glimmer-tune install the A2UI Python SDK (`a2ui-agent-sdk`, `a2ui-core`) from the same A2UI git commit, and the pin is moved in both at once. The model is trained on Express compiled and validated by one compiler, so the explorer must parse, compile and validate its output with that same compiler. glimmer-tune already needs a git pin (`825beac`), because PyPI 0.9.0 lacks `RendererState` and the stateful `parse_response`. The explorer had been on PyPI 0.9.0.

## Considered options

- **Explorer on PyPI 0.9.0, glimmer-tune on git:** rejected. Two compilers can quietly disagree about what valid Express is, and glimmer-tune could not import the explorer's validator and prompt without a dependency conflict.
- **Both on the next PyPI release:** rejected for now. No release has the features glimmer-tune needs, and waiting would block both.

## Consequences

- `a2ui-a2a` is taken from the same commit if PyPI `a2ui-a2a==0.1.0` won't resolve against a git-built agent SDK.
- Moving the pin means re-running glimmer-tune's `build-data` and the explorer's tests together.
