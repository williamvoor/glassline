"""Saved Conversations (Workspaces SPEC phase 1, ADR 0040): one SQLite file per Workspace.

A saved Conversation keeps its SQL, never its rows. Each Result keeps its SQL, columns, row count
and a hash of its rows. Each Level keeps the input it was built from (the model's reply, a
Drill-down's view arguments, a Board's name, key and Control values). The model history keeps
every message with its row previews replaced by `{"rows_omitted": true}`.

All database work runs on one writer thread per Workspace, off the event loop. The file is
owner-only (0600) and lives in the data directory, outside the repo.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from explorer_agent.results import StoredResult

log = logging.getLogger(__name__)

T = TypeVar("T")

ROWS_OMITTED = {"rows_omitted": True}
# Postgres quotes data in its messages ("invalid input syntax ... "<value>""), so a saved error
# keeps its SQLSTATE only (refuter F1).
ERROR_OMITTED = "the error message is not saved"
TITLE_MAX = 80
STATE_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    turns INTEGER NOT NULL,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS results (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    result_id TEXT NOT NULL,
    sql TEXT NOT NULL,
    purpose TEXT NOT NULL,
    columns TEXT NOT NULL,
    column_types TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    truncated INTEGER NOT NULL,
    row_hash TEXT NOT NULL,
    PRIMARY KEY (conversation_id, result_id)
);
"""


_DECIMAL_TYPES = frozenset({"numeric", "decimal"})


def row_hash(rows: list[dict[str, Any]], columns: list[str], column_types: list[str]) -> str:
    """SHA-256 of the canonical JSON of a Result's rows (they are JSON-safe already). A whole
    `numeric` value is hashed as the float it was stored as before 2026-11-05 (1150000.0), so a
    Result saved then still matches its re-run."""
    decimal = {c for c, t in zip(columns, column_types, strict=True) if t in _DECIMAL_TYPES}
    if decimal:
        rows = [{k: float(v) if k in decimal and type(v) is int else v for k, v in r.items()} for r in rows]
    text = json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


_RESULT_ID = re.compile(r"\br\d+\b")


def result_ids_in(text: str) -> list[str]:
    """The `r<n>` tokens in a reply, in first-seen order (Workspaces B7)."""
    return list(dict.fromkeys(_RESULT_ID.findall(text or "")))


def title_of(question: str) -> str:
    text = " ".join(question.split())
    return text if len(text) <= TITLE_MAX else text[: TITLE_MAX - 1].rstrip() + "…"


def _omit_in(body: Any) -> Any:
    if not isinstance(body, dict):
        return body
    out = dict(body)
    if isinstance(out.get("error"), dict):
        out["error"] = {"sqlstate": out["error"].get("sqlstate"), "message": ERROR_OMITTED}
    if "rows_preview" in out:
        out["rows_preview"] = dict(ROWS_OMITTED)
    if isinstance(out.get("results"), list):  # the Board pair: one entry per Result
        out["results"] = [
            {**r, "rows_preview": dict(ROWS_OMITTED)} if isinstance(r, dict) and "rows_preview" in r else r
            for r in out["results"]
        ]
    return out


def omit_rows(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The model history with every tool result's row preview and each Board-pair row list
    replaced by `{"rows_omitted": true}`, and each tool error cut to its SQLSTATE. Columns, row
    counts and SQL are kept."""
    out = []
    for m in history:
        if m.get("role") == "tool" and isinstance(m.get("content"), str):
            try:
                body = json.loads(m["content"])
            except json.JSONDecodeError:
                out.append(dict(m))
                continue
            out.append({**m, "content": json.dumps(_omit_in(body), ensure_ascii=False)})
        else:
            out.append(copy.deepcopy(m))
    return out


@dataclass(frozen=True)
class SavedResult:
    id: str
    sql: str
    purpose: str
    columns: list[str]
    column_types: list[str]
    row_count: int
    truncated: bool
    row_hash: str


@dataclass
class Snapshot:
    """One Conversation as the executor sees it at the end of a Level. Built on the event loop
    from references; `history` is copied and stripped of rows on the writer thread."""

    id: str
    title: str
    created_at: str
    updated_at: str
    turns: int
    state: dict[str, Any]
    history: list[dict[str, Any]]
    results: list[StoredResult] = field(default_factory=list)  # new ones; rows hashed, never written


@dataclass
class Loaded:
    id: str
    title: str
    created_at: str
    updated_at: str
    state: dict[str, Any]
    results: dict[str, SavedResult]


class SavedStore:
    """The Workspace's `conversations.sqlite`. Every call runs on its own writer thread."""

    def __init__(self, path: Path):
        self.path = path
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"saved-{path.parent.name}")
        self._conn: sqlite3.Connection | None = None

    # thread side ----------------------------------------------------------------------------

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not self.path.exists():
                os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
            os.chmod(self.path, 0o600)
            conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(SCHEMA)
            self._conn = conn
        return self._conn

    def save_sync(self, snap: Snapshot) -> None:
        db = self._db()
        state = {**snap.state, "history": omit_rows(snap.history)}
        rows = [
            (snap.id, r.id, r.sql, r.purpose, json.dumps(r.columns), json.dumps(r.column_types), r.row_count,
             int(r.truncated), row_hash(r.rows, r.columns, r.column_types))
            for r in snap.results
        ]
        db.execute("BEGIN")
        try:
            db.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at, turns, state) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at, "
                "turns=excluded.turns, state=excluded.state",
                (snap.id, snap.title, snap.created_at, snap.updated_at, snap.turns, json.dumps(state, ensure_ascii=False)),
            )
            # A Result ID is never reused, so the first hash is the one a re-run compares to (B8).
            db.executemany("INSERT OR IGNORE INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def load_sync(self, conversation_id: str) -> Loaded | None:
        db = self._db()
        row = db.execute(
            "SELECT id, title, created_at, updated_at, state FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if row is None:
            return None
        results = {
            r[0]: SavedResult(r[0], r[1], r[2], json.loads(r[3]), json.loads(r[4]), r[5], bool(r[6]), r[7])
            for r in db.execute(
                "SELECT result_id, sql, purpose, columns, column_types, row_count, truncated, row_hash "
                "FROM results WHERE conversation_id = ?", (conversation_id,),
            )
        }
        return Loaded(row[0], row[1], row[2], row[3], json.loads(row[4]), results)

    def list_sync(self) -> list[dict[str, Any]]:
        return [
            {"id": r[0], "title": r[1], "updated_at": r[2], "turns": r[3]}
            for r in self._db().execute(
                "SELECT id, title, updated_at, turns FROM conversations ORDER BY updated_at DESC, rowid DESC"
            )
        ]

    def delete_sync(self, conversation_id: str) -> bool:
        cur = self._db().execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return cur.rowcount > 0

    def delete_all_sync(self) -> None:
        self._db().execute("DELETE FROM conversations")

    def close_sync(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # event-loop side ------------------------------------------------------------------------

    async def call(self, fn: Callable[..., T], *args: Any) -> T:
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    async def save(self, snap: Snapshot) -> None:
        await self.call(self.save_sync, snap)

    async def load(self, conversation_id: str) -> Loaded | None:
        return await self.call(self.load_sync, conversation_id)

    async def list(self) -> list[dict[str, Any]]:
        return await self.call(self.list_sync)

    async def delete(self, conversation_id: str) -> bool:
        return await self.call(self.delete_sync, conversation_id)

    async def delete_all(self) -> None:
        await self.call(self.delete_all_sync)

    async def close(self) -> None:
        await self.call(self.close_sync)
        self._executor.shutdown(wait=True)
