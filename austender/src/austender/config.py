"""Settings from environment variables, optionally loaded from austender/.env.

Passwords never appear in repr() or log output: connections are made from keyword arguments, not URLs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_env_file(path: Path = ENV_FILE) -> None:
    """Load KEY=VALUE lines into os.environ without overriding variables that are already set."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


@dataclass(frozen=True)
class Settings:
    host: str = "localhost"
    port: int = 5434
    dbname: str = "austender"
    loader_password: str = field(default="", repr=False)
    explorer_ro_password: str = field(default="", repr=False)
    user_agent: str = ""
    min_interval_s: float = 1.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        return cls(
            host=e.get("POSTGRES_HOST", "localhost"),
            port=int(e.get("POSTGRES_PORT", "5434")),
            dbname=e.get("POSTGRES_DB", "austender"),
            loader_password=e.get("LOADER_PASSWORD", ""),
            explorer_ro_password=e.get("EXPLORER_RO_PASSWORD", ""),
            user_agent=e.get("INGEST_USER_AGENT", "").strip(),
            # Requests stay at least 1000 ms apart (SPEC section 4); a lower setting is raised to that.
            min_interval_s=max(int(e.get("INGEST_MIN_INTERVAL_MS", "1000")), 1000) / 1000,
        )

    def loader_connect_kwargs(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": "austender_loader",
            "password": self.loader_password,
        }
