"""Connections, role setup and the plain-SQL migration runner."""

from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg
from psycopg import sql

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
LOADER = "austender_loader"
EXPLORER_RO = "explorer_ro"
_LOCK_KEY = 7_164_251_001  # advisory lock so two migrate runs cannot interleave


def connect(**kwargs: object) -> psycopg.Connection:
    return psycopg.connect(**kwargs)  # type: ignore[arg-type]


def ensure_roles(
    conn: psycopg.Connection,
    *,
    loader_password: str | None,
    explorer_ro_password: str | None,
) -> None:
    """Create the two roles if missing and set their attributes. Needs a role that can create roles
    (the container's bootstrap user, or a superuser in tests). Idempotent.

    austender_loader owns everything and does all writes. explorer_ro gets SELECT on mart only
    (granted by migration 0001), no temp tables or schema public, and is read-only by default.
    """
    with conn.transaction():
        loader_exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (LOADER,)).fetchone()
        if not loader_exists:
            create = sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(LOADER))
            if loader_password:
                create += sql.SQL(" PASSWORD {}").format(sql.Literal(loader_password))
            conn.execute(create)
        # An existing loader (the container's bootstrap role) keeps its password.
        ro_exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (EXPLORER_RO,)).fetchone()
        if not ro_exists:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(EXPLORER_RO)))
        if explorer_ro_password:
            conn.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(EXPLORER_RO), sql.Literal(explorer_ro_password))
            )
        conn.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(sql.Identifier(EXPLORER_RO)))
        db = conn.execute("SELECT current_database()").fetchone()[0]
        conn.execute(sql.SQL("GRANT CREATE, TEMPORARY ON DATABASE {} TO {}").format(sql.Identifier(db), sql.Identifier(LOADER)))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(db), sql.Identifier(EXPLORER_RO)))
        # explorer_ro gets nothing beyond mart, so take away what PUBLIC gets by default (temp tables, schema
        # public). The loader keeps TEMPORARY through its own grant above.
        conn.execute(sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db)))
        conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply pending migrations in order, all in one transaction, as austender_loader.
    Returns the names applied. Roles must already exist (see ensure_roles)."""
    applied_now: list[str] = []
    with conn.transaction():
        conn.execute("SET LOCAL ROLE austender_loader")
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        conn.execute("CREATE SCHEMA IF NOT EXISTS ops")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ops.schema_migration ("
            " version text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {r[0]: r[1] for r in conn.execute("SELECT version, sha256 FROM ops.schema_migration")}
        for path in migration_files():
            text = path.read_text()
            digest = hashlib.sha256(text.encode()).hexdigest()
            if path.stem in done:
                if done[path.stem] != digest:
                    raise RuntimeError(f"migration {path.name} was changed after it was applied")
                continue
            conn.execute(text)  # type: ignore[arg-type]
            conn.execute("INSERT INTO ops.schema_migration (version, sha256) VALUES (%s, %s)", (path.stem, digest))
            applied_now.append(path.stem)
    return applied_now
