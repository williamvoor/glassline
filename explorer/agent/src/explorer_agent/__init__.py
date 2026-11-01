"""The explorer agent."""

import os

# Set before anything imports litellm:
# - LiteLLM fetches its model price map from GitHub on import unless told to use the copy it
#   ships. The agent must not make network calls it doesn't need, and tests must make none.
# - In its default "DEV" mode LiteLLM loads the nearest .env on import (searching up from its own
#   folder), which would put whatever secrets sit above the venv into every process. Only
#   `__main__` loads configuration, from the file it is told to use.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
