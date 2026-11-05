"""Settings from environment variables, optionally loaded from vicprop/.env.

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
    port: int = 5435
    dbname: str = "vicprop"
    loader_password: str = field(default="", repr=False)
    explorer_ro_password: str = field(default="", repr=False)
    xlsx_path: str = "houses-by-suburb-2015-2025.xlsx"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        return cls(
            host=e.get("POSTGRES_HOST", "localhost"),
            port=int(e.get("POSTGRES_PORT", "5435")),
            dbname=e.get("POSTGRES_DB", "vicprop"),
            loader_password=e.get("LOADER_PASSWORD", ""),
            explorer_ro_password=e.get("EXPLORER_RO_PASSWORD", ""),
            xlsx_path=e.get("VICPROP_XLSX", "houses-by-suburb-2015-2025.xlsx"),
        )

    def loader_connect_kwargs(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": "vicprop_loader",
            "password": self.loader_password,
        }

    def xlsx_file(self) -> Path:
        p = Path(self.xlsx_path)
        if not p.is_absolute():
            p = Path(__file__).resolve().parents[2] / p
        return p
