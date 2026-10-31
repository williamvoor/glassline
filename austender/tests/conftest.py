import itertools
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from testcontainers.community.postgres import PostgresContainer

sys.path.insert(0, str(Path(__file__).parent / "seed"))

from austender.testing import build_test_db  # noqa: E402

_counter = itertools.count(1)


@pytest.fixture(scope="session")
def pg_admin_dsn():
    with PostgresContainer("postgres:18", driver=None) as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def new_db(pg_admin_dsn):
    """Factory: a fresh, empty database in the shared container; returns its superuser DSN."""

    def make() -> str:
        name = f"t{next(_counter)}"
        with psycopg.connect(pg_admin_dsn, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
        return make_conninfo(pg_admin_dsn, dbname=name)

    return make


@pytest.fixture(scope="session")
def seeded_dsn(new_db):
    """A database built by build_test_db. Tests must treat it as read-only (rebuild is idempotent)."""
    dsn = new_db()
    build_test_db(dsn)
    return dsn


@pytest.fixture
def conn(seeded_dsn):
    with psycopg.connect(seeded_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
def ro_conn(seeded_dsn):
    dsn = make_conninfo(seeded_dsn, user="explorer_ro", password="explorer_ro")
    with psycopg.connect(dsn, autocommit=True) as c:
        yield c
