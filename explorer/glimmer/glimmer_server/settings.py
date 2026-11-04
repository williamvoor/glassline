"""Settings from the environment, else the repo root's `.env` (EXPLORER_ENV_FILE overrides the path)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_URL = "http://127.0.0.1:8765"
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"  # genUI/.env
LOOPBACK = {"127.0.0.1", "localhost"}


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    api_key: str
    checkpoint: str
    port: int

    def __repr__(self) -> str:  # never show the key
        return f"Settings(checkpoint_label={checkpoint_label(self.checkpoint)!r}, port={self.port})"


def checkpoint_label(path: str) -> str:
    """The last path segment (e.g. `s225-2939ff3b`): safe to show, unlike the full path."""
    return path.rstrip("/").rsplit("/", 1)[-1]


def _env_file_values(env: dict[str, str]) -> dict[str, str]:
    from dotenv import dotenv_values

    path = Path(env["EXPLORER_ENV_FILE"]) if env.get("EXPLORER_ENV_FILE") else ENV_FILE
    if not path.is_file():
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def load(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if env is None else env)
    merged = {**_env_file_values(env), **{k: v for k, v in env.items() if v}}
    key = merged.get("FIREWORKS_API_KEY", "")
    if not key:
        raise SettingsError("FIREWORKS_API_KEY is not set")
    ckpt = merged.get("GLIMMER_CHECKPOINT", "")
    if not ckpt:
        raise SettingsError("GLIMMER_CHECKPOINT is not set (a sampler checkpoint path, e.g. from glimmer-tune/runs/sft2/manifest.json)")
    url = urlparse(merged.get("GLIMMER_URL") or DEFAULT_URL)
    if url.hostname not in LOOPBACK:
        raise SettingsError(f"GLIMMER_URL host must be one of {sorted(LOOPBACK)}: the server is local only")
    try:
        port = url.port or 8765
    except ValueError as e:
        raise SettingsError(f"GLIMMER_URL has a bad port: {e}") from None
    return Settings(api_key=key, checkpoint=ckpt, port=port)
