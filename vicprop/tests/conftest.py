"""One disposable Postgres 18 per session, built with build_test_db. Docker, no network."""

from __future__ import annotations

import pytest

from vicprop.testing import build_test_db


@pytest.fixture(scope="session")
def dsn():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:18", username="postgres", password="postgres") as pg:
        url = pg.get_connection_url().replace("+psycopg2", "")
        build_test_db(url)
        yield url
