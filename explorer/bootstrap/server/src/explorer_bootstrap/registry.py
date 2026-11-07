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

from explorer_agent.config import (
    CLI_URL_ENV,
    CONNECTION_URL_ENV,
    CONNECTIONS_FILE,
    LEGACY_LOGIN_KEY,
    WORKSPACE_KEYS,
    WORKSPACES_FILE,
    ConfigError,
    connection_entry,
    parse_connections,
)

DSN_VAR = CONNECTION_URL_ENV  # fullmatch
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
            if k not in WORKSPACE_KEYS and k != LEGACY_LOGIN_KEY:  # an unmigrated entry survives a write
                raise RegistryError(f"unknown key {k}", 500)
            out.append(f"{k} = {_value(v)}\n")
    return "".join(out)


def _write_atomic(target: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=".toml", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_entries(data_dir: Path, entries: list[dict[str, Any]]) -> None:
    """The whole list, written atomically; tomllib reads back what JSON strings write."""
    text = render(entries)
    tomllib.loads(text)  # never write a list the agent can't read
    _write_atomic(list_path(data_dir), text)


# --- Connections (explorer/specs/connections/SPEC.md, ADR 0051) ----------------------------------


def connections_path(data_dir: Path) -> Path:
    return data_dir / CONNECTIONS_FILE


def read_connections(data_dir: Path) -> list[dict[str, Any]]:
    """The Connection list as written. A list that isn't valid as a whole can't be changed."""
    path = connections_path(data_dir)
    if not path.is_file():
        return []
    got = parse_connections(path.read_text(encoding="utf-8"))
    if got.file_error:
        raise RegistryError(f"{CONNECTIONS_FILE}: {got.file_error}", 500)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [dict(i) for i in data.get("connection", []) if isinstance(i, dict)]


def render_connections(items: list[dict[str, Any]]) -> str:
    out = ["# The explorer's Connections (specs/connections/SPEC.md). Written by Settings.\n"
           "# Each names the .env variable that holds a read-only login; never the login itself.\n"]
    for c in items:
        out.append("\n[[connection]]\n")
        for k in ("name", "label", "url_env"):
            if k in c:
                out.append(f"{k} = {_value(c[k])}\n")
    return "".join(out)


def write_connections(data_dir: Path, items: list[dict[str, Any]]) -> None:
    """The whole list, written atomically, refused unless every entry is valid."""
    text = render_connections(items)
    got = parse_connections(text)
    if got.file_error or got.errors:
        reason = got.file_error or "; ".join(f"{k}: {v}" for k, v in got.errors.items())
        raise RegistryError(f"invalid Connection list: {reason}")
    _write_atomic(connections_path(data_dir), text)


def name_for(url_env: str, taken: set[str]) -> str:
    """A Connection name from its variable: `MY_DATA_DATABASE_URL` -> `my_data`, then `_2`, `_3`."""
    base = url_env.removesuffix("_DATABASE_URL").lower()
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    return name


def connection_choices(env: Mapping[str, str], data_dir: Path) -> list[dict[str, Any]]:
    """What the Database step offers (C5): every valid Connection by label, then each set
    `*_DATABASE_URL` variable no Connection names. `ready` says whether its variable is set.
    Never a value."""
    listed = parse_connections(connections_path(data_dir).read_text(encoding="utf-8")) \
        if connections_path(data_dir).is_file() else parse_connections("")
    if listed.file_error:
        raise RegistryError(f"{CONNECTIONS_FILE}: {listed.file_error}", 500)
    out = [{"name": c.name, "label": c.label, "url_env": c.url_env, "listed": True,
            "ready": bool(env.get(c.url_env, "").strip())} for c in listed.connections.values()]
    out.sort(key=lambda c: c["label"].lower())
    named = {c.url_env for c in listed.connections.values()}
    taken = set(listed.connections) | set(listed.errors)
    for var in declared_connections(env):
        if var not in named:
            name = name_for(var, taken)
            taken.add(name)
            out.append({"name": name, "label": name, "url_env": var, "listed": False, "ready": True})
    return out


def claim_connection(data_dir: Path, choice: Mapping[str, Any]) -> None:
    """Writes an unlisted choice (a bare variable) as a Connection (C5)."""
    items = read_connections(data_dir)
    if any(c.get("name") == choice["name"] for c in items):
        return
    write_connections(data_dir, [*items, {"name": choice["name"], "url_env": choice["url_env"]}])


def users_of(data_dir: Path, name: str) -> list[str]:
    return [str(e.get("name")) for e in read_entries(data_dir) if e.get("connection") == name]


def set_label(data_dir: Path, name: str, label: str) -> None:
    items = read_connections(data_dir)
    item = next((c for c in items if c.get("name") == name), None)
    if item is None:
        raise RegistryError("no such Connection", 404)
    candidate = {**item, "label": label.strip()}
    try:
        connection_entry(candidate)
    except ConfigError as e:
        raise RegistryError(str(e)) from None
    write_connections(data_dir, [candidate if c is item else c for c in items])


def delete_connection(data_dir: Path, name: str) -> None:
    """Refused while a Workspace names it (C9), with the Workspaces named."""
    items = read_connections(data_dir)
    if not any(c.get("name") == name for c in items):
        raise RegistryError("no such Connection", 404)
    users = users_of(data_dir, name)
    if users:
        raise RegistryError(f"Connection {name} is used by Workspace {', '.join(users)}", 409)
    write_connections(data_dir, [c for c in items if c.get("name") != name])


def migrate(data_dir: Path) -> list[str]:
    """C6: rewrites each `dsn_env` Workspace as `connection =`, finding or creating the
    Connection with that variable. Connections are written first; if the Workspace list then
    fails to write, the old Connection list is put back. Returns the migrated Workspaces."""
    entries = read_entries(data_dir)
    if not any(LEGACY_LOGIN_KEY in e for e in entries):
        return []
    path = connections_path(data_dir)
    before = path.read_text(encoding="utf-8") if path.is_file() else None
    items = read_connections(data_dir)
    by_var = {c.get("url_env"): c.get("name") for c in items}
    taken = {str(c.get("name")) for c in items}
    migrated: list[str] = []
    out: list[dict[str, Any]] = []
    for e in entries:
        var = e.get(LEGACY_LOGIN_KEY)
        if not isinstance(var, str) or "connection" in e or not DSN_VAR.fullmatch(var) or var == CLI_URL_ENV:
            out.append(e)  # nothing to do, or nothing it can become: the agent shows why
            continue
        if var not in by_var:
            name = name_for(var, taken)
            taken.add(name)
            by_var[var] = name
            items.append({"name": name, "url_env": var})
        rewritten: dict[str, Any] = {}
        for k, v in e.items():  # keep the entry's key order, with connection where dsn_env was
            rewritten["connection" if k == LEGACY_LOGIN_KEY else k] = by_var[var] if k == LEGACY_LOGIN_KEY else v
        out.append(rewritten)
        migrated.append(str(e.get("name")))
    if not migrated:
        return []
    write_connections(data_dir, items)
    try:
        write_entries(data_dir, out)
    except BaseException:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            _write_atomic(path, before)
        raise
    return migrated


CLI_DSN_VAR = "BOOTSTRAP_DATABASE_URL"  # the bootstrap CLI's login, never a Workspace's (D2)


def declared_connections(env: Mapping[str, str]) -> list[str]:
    """Names of the set `<NAME>_DATABASE_URL` variables a Workspace may use. Never a value."""
    return sorted(k for k, v in env.items() if DSN_VAR.fullmatch(k) and k != CLI_DSN_VAR and v.strip())


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
