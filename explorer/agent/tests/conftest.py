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
