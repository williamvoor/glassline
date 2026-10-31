from pathlib import Path

import pytest

from a2ui_eval.refs import RefIndex
from a2ui_eval.sdk import ExpressSDK

CATALOG = Path(__file__).resolve().parents[1] / "catalogs" / "basic_v1.json"


@pytest.fixture(scope="session")
def sdk() -> ExpressSDK:
    return ExpressSDK(CATALOG)


@pytest.fixture(scope="session")
def index(sdk: ExpressSDK) -> RefIndex:
    return RefIndex(sdk.catalog_json)


def comps(*items: dict) -> dict[str, dict]:
    return {c["id"]: c for c in items}
