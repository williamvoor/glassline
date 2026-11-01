import pytest

from explorer_agent.macros import macro_context
from explorer_agent.results import ResultStore

SUPPLIERS_SQL = "SELECT supplier, contracts, value FROM mart.contract_supplier LIMIT 3"


@pytest.fixture
def store() -> ResultStore:
    s = ResultStore()
    s.add(
        sql=SUPPLIERS_SQL,
        purpose="top suppliers",
        columns=["supplier", "contracts", "value"],
        column_types=["text", "bigint", "numeric"],
        rows=[
            {"supplier": "Beta", "contracts": 12, "value": 80_000_000.0},
            {"supplier": "Acme_[x]", "contracts": 1_234, "value": 3_441_812_603.0},
            {"supplier": "Gamma", "contracts": 3, "value": 5_000.5},
        ],
        truncated=False,
    )
    return s


@pytest.fixture
def ctx(store):
    with macro_context(store) as c:
        yield c


# ---- disposable Postgres 18 built by austender (ADR 0010) -------------------------------------

import importlib.util
from dataclasses import dataclass as _dataclass

from austender.testing import SEED_DIR, build_test_db

from explorer_agent.sql import open_pool

RO_PASSWORD = "explorer_ro"
LOADER_PASSWORD = "austender_loader"


def load_seed_expected():
    """austender's hand-computed seed totals (`tests/seed/expected.py`), imported by path."""
    spec = importlib.util.spec_from_file_location("austender_seed_expected", SEED_DIR / "expected.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@_dataclass(frozen=True)
class SeedDb:
    ro_dsn: str
    loader_dsn: str
    superuser_dsn: str


@pytest.fixture(scope="session")
def pg():
    try:
        import docker.errors
        from testcontainers.community.postgres import PostgresContainer

        container = PostgresContainer("postgres:18", driver=None)
        container.start()
    except docker.errors.DockerException as e:
        pytest.fail(f"database tests need Docker (Testcontainers Postgres 18), and it isn't available: {e}")
    try:
        superuser = container.get_connection_url()
        build_test_db(superuser, loader_password=LOADER_PASSWORD, explorer_ro_password=RO_PASSWORD)
        host, port = container.get_container_host_ip(), container.get_exposed_port(5432)
        base = f"{host}:{port}/{container.dbname}"
        yield SeedDb(
            ro_dsn=f"postgresql://explorer_ro:{RO_PASSWORD}@{base}",
            loader_dsn=f"postgresql://austender_loader:{LOADER_PASSWORD}@{base}",
            superuser_dsn=superuser,
        )
    finally:
        container.stop()


@pytest.fixture
async def ro_pool(pg):
    pool = open_pool(pg.ro_dsn)
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def loader_pool(pg):
    pool = open_pool(pg.loader_dsn)
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()
