"""A disposable Postgres 18 (Testcontainers) with an owner role and a read-only role. Docker, no network."""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
import pytest

OWNER_PW = "owner-pw-7f3k"
RO_PW = "ro-pw-9q2m"


@dataclass(frozen=True)
class Pg:
    superuser: str
    owner: str  # owns the schemas; loads
    ro: str  # explorer_ro


@pytest.fixture(scope="session")
def pg():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:18", username="postgres", password="postgres", dbname="data") as c:
        su = c.get_connection_url().replace("+psycopg2", "")
        with psycopg.connect(su, autocommit=True) as conn:
            conn.execute(f"CREATE ROLE loader LOGIN PASSWORD '{OWNER_PW}'")
            conn.execute(f"CREATE ROLE explorer_ro LOGIN PASSWORD '{RO_PW}'")
            conn.execute("GRANT CREATE ON DATABASE data TO loader")
            conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        base = psycopg.conninfo.conninfo_to_dict(su)
        mk = lambda user, pw: psycopg.conninfo.make_conninfo(**{**base, "user": user, "password": pw})  # noqa: E731
        yield Pg(su, mk("loader", OWNER_PW), mk("explorer_ro", RO_PW))


@pytest.fixture
def clean(pg):
    """Drops what a test created, so tests don't see each other's schemas."""
    yield pg
    with psycopg.connect(pg.superuser, autocommit=True) as conn:
        for (s,) in conn.execute("SELECT nspname FROM pg_namespace WHERE nspowner = 'loader'::regrole").fetchall():
            conn.execute(f'DROP SCHEMA "{s}" CASCADE')
        conn.execute("REVOKE ALL ON DATABASE data FROM explorer_ro")
        conn.execute("ALTER DEFAULT PRIVILEGES FOR ROLE loader REVOKE ALL ON TABLES FROM explorer_ro")
