"""The Workspace list as Settings writes it (explorer/specs/workspaces/SPEC.md phase 2, D3–D5).

Settings is the list's only writer: every change is a whole-file atomic write (temporary file,
then rename). Keys are the agent's (`WORKSPACE_KEYS`); the agent validates each entry when it
reloads, and a hand-edited entry it can't write back makes the write fail with a reason.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from explorer_agent.config import WORKSPACE_KEYS, WORKSPACES_FILE

DSN_VAR = re.compile(r"[A-Z][A-Z0-9_]*_DATABASE_URL")  # fullmatch
RELOAD_TIMEOUT_S = 120  # a reload builds the new Workspace: pool, checks, Board checks


class RegistryError(ValueError):
    """A list change that can't be made. `status` is the HTTP status to answer."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def list_path(data_dir: Path) -> Path:
    return data_dir / WORKSPACES_FILE


def read_entries(data_dir: Path) -> list[dict[str, Any]]:
    path = list_path(data_dir)
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise RegistryError(f"{WORKSPACES_FILE} is not valid TOML: {e}", 500) from None
    items = data.get("workspace", [])
    return [dict(i) for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _value(v: Any) -> str:
    if isinstance(v, bool) or not isinstance(v, (str, int)):
        raise RegistryError("a Workspace entry holds only strings and integers", 500)
    return json.dumps(v, ensure_ascii=False) if isinstance(v, str) else str(v)


def render(entries: list[dict[str, Any]]) -> str:
    out = ["# The explorer's Workspace list (specs/workspaces/SPEC.md). Written by Settings.\n"]
    for e in entries:
        out.append("\n[[workspace]]\n")
        for k, v in e.items():
            if k not in WORKSPACE_KEYS:
                raise RegistryError(f"unknown key {k}", 500)
            out.append(f"{k} = {_value(v)}\n")
    return "".join(out)


def write_entries(data_dir: Path, entries: list[dict[str, Any]]) -> None:
    """The whole list, written atomically; tomllib reads back what JSON strings write."""
    text = render(entries)
    tomllib.loads(text)  # never write a list the agent can't read
    fd, tmp = tempfile.mkstemp(prefix=".workspaces-", suffix=".toml", dir=data_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, list_path(data_dir))
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


CLI_DSN_VAR = "BOOTSTRAP_DATABASE_URL"  # the bootstrap CLI's login, never a Workspace's (D2)


def declared_connections(env: Mapping[str, str]) -> list[str]:
    """Names of the set `<NAME>_DATABASE_URL` variables a Workspace may use. Never a value."""
    return sorted(k for k, v in env.items() if DSN_VAR.fullmatch(k) and k != CLI_DSN_VAR and v.strip())


def unused_connections(env: Mapping[str, str], data_dir: Path) -> list[str]:
    """Names of declared `*_DATABASE_URL` variables no Workspace uses (S1). Never a value."""
    used = {e.get("dsn_env") for e in read_entries(data_dir)}
    return [k for k in declared_connections(env) if k not in used]


def kind_of(entry: Mapping[str, Any], data_dir: Path, repo_root: Path) -> str:
    raw = Path(str(entry.get("dataset_dir", ""))).expanduser()
    path = (raw if raw.is_absolute() else repo_root / raw).resolve()
    return "data" if path.is_relative_to(data_dir.resolve()) else "repo"


def workspace_dir(data_dir: Path, name: str) -> Path:
    """`workspaces/<name>/` in the data directory, refused if it would resolve anywhere else."""
    root = (data_dir / "workspaces").resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise RegistryError("bad Workspace name")
    return path


def remove_dir(data_dir: Path, name: str) -> None:
    path = workspace_dir(data_dir, name)
    if path.exists():
        shutil.rmtree(path)


async def reload_agent(agent_url: str, client: httpx.AsyncClient | None = None) -> tuple[int, Any]:
    """The agent's `POST /workspaces/reload`: (status, body). 0 when the agent can't be reached."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=RELOAD_TIMEOUT_S)
    try:
        r = await client.post(f"{agent_url.rstrip('/')}/workspaces/reload", json={})
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None
    except httpx.HTTPError:
        return 0, None
    finally:
        if own:
            await client.aclose()
