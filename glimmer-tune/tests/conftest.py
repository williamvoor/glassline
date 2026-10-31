from pathlib import Path

import pytest

from a2ui_eval.refs import RefIndex
from a2ui_eval.sdk import ExpressSDK

CATALOGS = Path(__file__).resolve().parents[1] / "catalogs"
CATALOG = CATALOGS / "basic_v0_9.json"  # ADR 0008
CATALOG_V1 = CATALOGS / "basic_v1.json"


@pytest.fixture(scope="session")
def sdk_v0_9() -> ExpressSDK:
    return ExpressSDK(CATALOG)


@pytest.fixture(scope="session")
def index_v0_9(sdk_v0_9: ExpressSDK) -> RefIndex:
    return RefIndex(sdk_v0_9.catalog_json)


@pytest.fixture(scope="session")
def sdk(sdk_v0_9: ExpressSDK) -> ExpressSDK:
    return sdk_v0_9


@pytest.fixture(scope="session")
def index(index_v0_9: RefIndex) -> RefIndex:
    return index_v0_9


@pytest.fixture(scope="session")
def sdk_v1() -> ExpressSDK:
    return ExpressSDK(CATALOG_V1)


@pytest.fixture(scope="session")
def index_v1(sdk_v1: ExpressSDK) -> RefIndex:
    return RefIndex(sdk_v1.catalog_json)


def comps(*items: dict) -> dict[str, dict]:
    return {c["id"]: c for c in items}
