"""Workspaces (specs/workspaces/SPEC.md phase 1, ADR 0039): one agent process, one A2A sub-app
per Workspace under `/w/<name>/`, picked by a dictionary lookup.

The registry reads the Workspace list, builds each Workspace in order (a failure leaves it
unavailable with a reason that never holds a DSN), serves `GET /workspaces` and
`POST /workspaces/reload`, and routes `/w/<name>/…` to its sub-app. The agent only reads the list.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from explorer_agent.config import (
    CONNECTIONS_FILE,
    WORKSPACES_FILE,
    ConfigError,
    ConnectionList,
    WorkspaceEntry,
    WorkspaceListing,
    WorkspaceUnavailable,
    connection_url_env,
    load_connections,
    load_workspace_list,
    workspace_settings,
)
from explorer_agent.config import Settings

log = logging.getLogger(__name__)

AVAILABLE = "available"
UNAVAILABLE = "unavailable"
PREFIX = "/w/"


@dataclass
class Built:
    """What building one Workspace gives: its sub-app, how to close it, and its card's text."""

    app: ASGIApp
    close: Callable[[], Awaitable[None]]
    description: str
    running: Callable[[], bool]  # a model run is in progress in one of its Conversations
    started: Callable[[], None] = lambda: None  # after it is mounted (Board warm-up)
    pool: Any = None  # its own connection pool (W4)


# (entry, settings, url, saved path) -> Built; raises WorkspaceUnavailable with the reason
Builder = Callable[[WorkspaceEntry, Settings, str, Path], Awaitable[Built]]


@dataclass
class Workspace:
    name: str
    status: str
    reason: str | None = None
    entry: WorkspaceEntry | None = None
    fingerprint: str = ""
    built: Built | None = None
    description: str = ""

    def listing(self, base_url: str) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "description": self.description, "status": self.status,
                               "url": f"{base_url}{PREFIX}{self.name}" if self.entry is not None else None}
        if self.reason is not None:
            out["reason"] = self.reason
        return out

    @property
    def running(self) -> bool:
        return self.built is not None and self.built.running()


def login_key(entry: WorkspaceEntry, connections: ConnectionList) -> str:
    """What the entry's Connection resolves to (a variable name or the reason it can't), so a
    Connection change rebuilds the Workspaces that use it. Never a URL."""
    try:
        return connection_url_env(entry, connections)
    except WorkspaceUnavailable as e:
        return f"unavailable: {e}"


def dataset_fingerprint(entry: WorkspaceEntry, login: str = "") -> str:
    """The entry as written, its resolved login variable, and every Dataset file's path and
    bytes (B5)."""
    h = hashlib.sha256(json.dumps(entry.raw, sort_keys=True, default=str).encode())
    h.update(b"\0login\0" + login.encode())
    root = entry.dataset_dir
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts):
                h.update(str(p.relative_to(root)).encode() + b"\0")
                h.update(p.read_bytes())
    return h.hexdigest()


class Registry:
    """The Workspaces and the ASGI router in front of their sub-apps."""

    def __init__(self, data_dir: Path, repo_root: Path, explorer_root: Path, env: Mapping[str, str],
                 builder: Builder, base_url: str):
        self.data_dir = data_dir
        self.repo_root = repo_root
        self.explorer_root = explorer_root
        self.env = env
        self.builder = builder
        self.base_url = base_url
        self.workspaces: dict[str, Workspace] = {}  # list order
        self.connections = ConnectionList()  # read with the list, on start and each reload
        self._lock = asyncio.Lock()

    @property
    def list_path(self) -> Path:
        return self.data_dir / WORKSPACES_FILE

    def saved_path(self, name: str) -> Path:
        return self.data_dir / "workspaces" / name / "conversations.sqlite"

    def listing(self) -> list[dict[str, Any]]:
        return [w.listing(self.base_url) for w in self.workspaces.values()]

    async def _build(self, item: WorkspaceListing) -> Workspace:
        if item.entry is None:
            return Workspace(item.name, UNAVAILABLE, item.error)
        entry = item.entry
        fingerprint = dataset_fingerprint(entry, login_key(entry, self.connections))
        try:
            settings = workspace_settings(self.env, entry, self.explorer_root, self.connections)
            built = await self.builder(entry, settings, f"{self.base_url}{PREFIX}{entry.name}", self.saved_path(entry.name))
        except WorkspaceUnavailable as e:
            log.warning("workspace unavailable: %s: %s", entry.name, e)
            return Workspace(entry.name, UNAVAILABLE, str(e), entry, fingerprint)
        except Exception as e:  # never the message: it could quote a DSN
            log.error("workspace failed to start: %s: %s", entry.name, type(e).__name__)
            return Workspace(entry.name, UNAVAILABLE, "startup failed", entry, fingerprint)
        log.info("workspace mounted: %s", entry.name)
        return Workspace(entry.name, AVAILABLE, None, entry, fingerprint, built, built.description)

    def _read(self) -> list[WorkspaceListing]:
        self.connections = load_connections(self.data_dir / CONNECTIONS_FILE)
        return load_workspace_list(self.list_path, self.repo_root)

    async def start(self) -> None:
        try:
            items = self._read()
        except ConfigError as e:
            log.error("workspace list not loaded: %s", e)
            items = []
        for item in items:
            if item.name in self.workspaces:  # a duplicate: listed, never routed to
                self.workspaces[f"{item.name}#{len(self.workspaces)}"] = Workspace(
                    item.name, UNAVAILABLE, item.error or "invalid entry: duplicate name")
                continue
            ws = await self._build(item)
            self.workspaces[ws.name] = ws
            if ws.built is not None:
                ws.built.started()

    async def reload(self) -> tuple[int, Any]:
        """Re-reads the list: mounts new Workspaces, retries unavailable ones, rebuilds changed
        ones, unmounts removed ones. 409 while a removed or changed one is running (B5)."""
        async with self._lock:
            try:
                items = self._read()
            except ConfigError as e:
                return 400, {"error": str(e)}
            old = self.workspaces
            plan: list[tuple[WorkspaceListing, Workspace | None]] = []
            for item in items:
                current = old.get(item.name)
                keep = (current is not None and current.status == AVAILABLE and item.entry is not None
                        and current.entry is not None and current.fingerprint == dataset_fingerprint(item.entry, login_key(item.entry, self.connections))
                        and current.entry.raw == item.entry.raw)
                plan.append((item, current if keep else None))
            kept = {id(ws) for _, ws in plan if ws is not None}
            replaced = [ws for ws in old.values() if id(ws) not in kept]
            busy = sorted(ws.name for ws in replaced if ws.running)
            if busy:
                return 409, {"error": f"a model run is in progress in {', '.join(busy)}", "workspaces": self.listing()}
            new: dict[str, Workspace] = {}
            started = []
            for item, ws in plan:
                if item.name in new:
                    new[f"{item.name}#{len(new)}"] = Workspace(item.name, UNAVAILABLE,
                                                               item.error or "invalid entry: duplicate name")
                    continue
                if ws is None:
                    ws = await self._build(item)
                    started.append(ws)
                new[ws.name] = ws
            self.workspaces = new
            for ws in replaced:
                await self._close(ws)
            for ws in started:
                if ws.built is not None:
                    ws.built.started()
            return 200, self.listing()

    @staticmethod
    async def _close(ws: Workspace) -> None:
        if ws.built is not None:
            try:
                await ws.built.close()
            except Exception as e:
                log.error("workspace close failed: %s: %s", ws.name, type(e).__name__)

    async def close(self) -> None:
        for ws in self.workspaces.values():
            await self._close(ws)

    # ASGI ---------------------------------------------------------------------------------------

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await _lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        path: str = scope["path"]
        method: str = scope["method"]
        if path == "/workspaces" and method == "GET":
            await JSONResponse(self.listing())(scope, receive, send)
            return
        if path == "/workspaces/reload" and method == "POST":
            status, body = await self.reload()
            await JSONResponse(body, status_code=status)(scope, receive, send)
            return
        if path.startswith(PREFIX):
            name, _, rest = path[len(PREFIX):].partition("/")
            ws = self.workspaces.get(name)
            if ws is None:
                await JSONResponse({"error": "no such workspace"}, status_code=404)(scope, receive, send)
                return
            if ws.built is None:
                await JSONResponse({"error": ws.reason}, status_code=503)(scope, receive, send)
                return
            prefix = f"{PREFIX}{name}"
            child = dict(scope)
            child["root_path"] = scope.get("root_path", "") + prefix
            child["path"] = "/" + rest
            child["raw_path"] = child["path"].encode()
            await ws.built.app(child, receive, send)
            return
        await JSONResponse({"error": "not found"}, status_code=404)(scope, receive, send)


async def _lifespan(receive: Receive, send: Send) -> None:
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


@dataclass
class Guard:
    """Refuses what a page on another site could send (W7): any request with a foreign `Host`
    (DNS rebinding: the GET routes hold saved questions and rows), or a POST that isn't JSON
    (that would need a CORS preflight, which only the client's origin passes). The bootstrap
    server's rule."""

    app: ASGIApp
    hosts: set[bytes] = field(default_factory=set)

    @classmethod
    def for_port(cls, app: ASGIApp, host: str, port: int) -> Guard:
        names = {host, "127.0.0.1", "localhost"}
        return cls(app, {f"{n}:{port}".encode() for n in names})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            ok = headers.get(b"host", b"") in self.hosts
            if ok and scope["method"] in ("POST", "PUT", "PATCH"):
                ctype = headers.get(b"content-type", b"").split(b";")[0].strip().lower()
                ok = ctype == b"application/json"
            if not ok:
                await PlainTextResponse("forbidden", status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def workspace_app(registry: Registry, client_origin: str, host: str, port: int) -> ASGIApp:
    """The router behind the Host guard and one CORS middleware (B1)."""
    cors = CORSMiddleware(Guard.for_port(registry, host, port), allow_origins=[client_origin], allow_methods=["*"],
                          allow_headers=["*"])
    return cors
