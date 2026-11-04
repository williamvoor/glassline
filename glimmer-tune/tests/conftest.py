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


# ---------------------------------------------------------------- test tiers (SPEC "Test tiers")
# `slow`: a test that generates many rows or every gallery family. Iterate with
# `uv run pytest -n auto --dist loadgroup -m "not slow"`; run the whole suite before "done".
SLOW_FIXTURES: set[str] = set()  # the synthetic-generation fixtures went to archive/tests
SLOW_TESTS = {  # 2 s or more without a slow fixture (measured 2026-11-03)
    "test_every_dev_reference_is_accepted",
    "test_real_data_is_v0_9", "test_score_and_compare_cli_on_test", "test_score_output_carries_blind_ci_and_scorer_version",
    "test_score_output_carries_selection_ci", "test_synthetic_dev_passes",
}
# Files whose tests may run on any worker; every other file keeps one worker, so its
# module fixtures are built once.
SPREAD_FILES: set[str] = set()


def pytest_collection_modifyitems(config, items):
    for item in items:
        if SLOW_FIXTURES & set(getattr(item, "fixturenames", ())) or getattr(item, "originalname", item.name) in SLOW_TESTS:
            item.add_marker(pytest.mark.slow)
        if item.path.name not in SPREAD_FILES:
            item.add_marker(pytest.mark.xdist_group(item.path.name))
