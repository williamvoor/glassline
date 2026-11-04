"""HTTP handling for the Glimmer server. No Fireworks imports: the backend is injected.

`Backend` is any callable `(SurfaceRequest) -> dict` with keys `text`, `finish`, `tokens` and
`usage`. The real one is `fireworks.FireworksBackend`; tests pass a stub.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

log = logging.getLogger("glimmer_server")

MAX_BODY = 4 * 1024 * 1024
DEFAULT_MAX_TOKENS = 1536
MAX_TOKENS_CAP = 4096
ROLES = {"system", "user", "assistant"}
DETAIL_CHARS = 300
REASONING = "reasoning strength: low"
SOCKET_TIMEOUT_S = 30  # a client that declares a body and stalls doesn't hold a thread forever


@dataclass(frozen=True)
class SurfaceRequest:
    messages: list[dict[str, str]]
    max_tokens: int
    grammar: str | None
    cache_key: str


class BadRequest(Exception):
    pass


Backend = Callable[[SurfaceRequest], dict[str, Any]]


def parse_request(body: bytes) -> SurfaceRequest:
    try:
        d = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise BadRequest(f"body is not JSON: {e}") from None
    if not isinstance(d, dict):
        raise BadRequest("body must be a JSON object")
    msgs = d.get("messages")
    if not isinstance(msgs, list) or not msgs:
        raise BadRequest("messages must be a non-empty list")
    for i, m in enumerate(msgs):
        if not isinstance(m, dict) or m.get("role") not in ROLES or not isinstance(m.get("content"), str):
            raise BadRequest(f"messages[{i}] must be {{role: system|user|assistant, content: string}}")
    # Without a system message the muse_glimmer renderer inserts a default one with today's date
    # (breaking the cached prefix), and without a reasoning line it appends "high". Fail closed.
    if msgs[0]["role"] != "system":
        raise BadRequest("the first message must be the system message")
    if REASONING not in msgs[0]["content"].lower():
        raise BadRequest("the system message must say 'Reasoning strength: low.'")
    if msgs[-1]["role"] != "user":
        raise BadRequest("the last message must be from the user")
    mt = d.get("max_tokens", DEFAULT_MAX_TOKENS)
    if type(mt) is not int or not 1 <= mt <= MAX_TOKENS_CAP:  # `type is` refuses booleans
        raise BadRequest(f"max_tokens must be an integer from 1 to {MAX_TOKENS_CAP}")
    grammar = d.get("grammar")
    if grammar is not None and (not isinstance(grammar, str) or not grammar.strip()):
        raise BadRequest("grammar must be a non-empty string (GBNF)")
    key = d.get("cache_key")
    if not isinstance(key, str) or not 1 <= len(key) <= 128:
        raise BadRequest("cache_key must be a string of 1 to 128 characters")
    return SurfaceRequest([{"role": m["role"], "content": m["content"]} for m in msgs], mt, grammar, key)


def redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text[:DETAIL_CHARS]


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, backend: Backend, label: str, secrets: list[str]):
        super().__init__(("127.0.0.1", port), Handler)
        self.backend, self.label, self.secrets = backend, label, secrets
        self.lock = threading.Lock()
        self.served = 0
        self.errors = 0

    def count(self, ok: bool) -> None:
        with self.lock:
            self.served += ok
            self.errors += not ok


class Handler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"
    timeout = SOCKET_TIMEOUT_S

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - replaces the default access log
        pass

    def _send(self, status: int, payload: dict[str, Any], t0: float, tokens: int | None = None) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        log.info("%s %s %d %.2fs tokens=%s", self.command, self.path, status, time.monotonic() - t0, tokens)

    def _error(self, status: int, kind: str, detail: str, t0: float) -> None:
        # An error may leave an unread body on the socket; closing keeps the next request from
        # parsing it as a request line.
        self.close_connection = True
        self._send(status, {"error": kind, "detail": detail}, t0)

    def do_GET(self) -> None:
        t0 = time.monotonic()
        if self.path == "/health":
            s = self.server
            self._send(200, {"ok": True, "checkpoint": s.label, "served": s.served, "errors": s.errors}, t0)
        elif self.path == "/surface":
            self._error(405, "method_not_allowed", "use POST", t0)
        else:
            self._error(404, "not_found", "unknown path", t0)

    def do_POST(self) -> None:
        t0 = time.monotonic()
        if self.path != "/surface":
            if self.path == "/health":
                self._error(405, "method_not_allowed", "use GET", t0)
            else:
                self._error(404, "not_found", "unknown path", t0)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            return self._error(400, "bad_request", "bad Content-Length", t0)
        if length > MAX_BODY:
            self.close_connection = True  # the body is never read
            return self._error(413, "too_large", f"body over {MAX_BODY} bytes", t0)
        try:
            req = parse_request(self.rfile.read(length))
        except BadRequest as e:
            return self._error(400, "bad_request", str(e), t0)
        try:
            out = self.server.backend(req)
        except Exception as e:  # noqa: BLE001 - any backend failure is a 502 for the agent to fall back on
            self.server.count(False)
            return self._error(502, "backend", redact(f"{type(e).__name__}: {e}", self.server.secrets), t0)
        self.server.count(True)
        reply = {"text": out["text"], "finish": out["finish"], "tokens": out["tokens"],
                 "latency_s": round(time.monotonic() - t0, 3), "usage": out["usage"], "checkpoint": self.server.label}
        self._send(200, reply, t0, out["tokens"])
