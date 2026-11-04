import datetime as dt
import json
import uuid
from decimal import Decimal

from explorer_agent.results import ResultStore


def _add(store: ResultStore, **row):
    return store.add(
        sql="SELECT 1", purpose="p", columns=list(row), column_types=["text"] * len(row),
        rows=[row], truncated=False,
    )


def test_ids_are_sequential_and_never_reused_after_eviction():
    store = ResultStore(max_results=2)
    ids = [_add(store, a=1).id for _ in range(3)]
    assert ids == ["r1", "r2", "r3"]
    assert store.ids() == ["r2", "r3"]
    assert store.get("r1") is None
    assert _add(store, a=1).id == "r4"


def test_values_are_stored_json_safe():
    u = uuid.UUID(int=1)
    r = _add(store := ResultStore(), d=Decimal("12.34"), day=dt.date(2024, 7, 1),
             ts=dt.datetime(2024, 7, 1, 9, 30), u=u, s="x")
    assert store.get(r.id).rows == [
        {"d": 12.34, "day": "2024-07-01", "ts": "2024-07-01T09:30:00", "u": str(u), "s": "x"}
    ]


def test_column_kinds():
    store = ResultStore()
    r = store.add(sql="", purpose="", columns=["a", "b", "c"],
                  column_types=["numeric", "date", "text"], rows=[], truncated=False)
    assert r.is_numeric("a") and not r.is_numeric("c")
    assert r.is_temporal("b") and not r.is_temporal("a")


# Regression: array and JSON columns kept nested Decimals, so json.dumps of the tool result raised
# and the Turn failed (e.g. SELECT ARRAY[1.2::numeric], or array_agg over a numeric column).
def test_nested_values_are_stored_json_safe():
    r = _add(store := ResultStore(), amounts=[Decimal("1.2"), None], doc={"v": Decimal("2"), "d": [dt.date(2024, 7, 1)]})
    row = store.get(r.id).rows[0]
    assert row == {"amounts": [1.2, None], "doc": {"v": 2.0, "d": ["2024-07-01"]}}
    json.dumps(row)


def test_result_view_looks_up_the_store_then_each_extra_source_in_order():
    """SPEC §18 slice 2: `r<id>` lookups check the store, then the live Board instances, then the
    Results pinned for a model run."""
    from explorer_agent.results import ResultView, StoredResult

    store = ResultStore()
    stored = _add(store, a="x")

    def board(rid):
        return StoredResult(id=rid, sql="", purpose="", columns=[], column_types=[], rows=[], truncated=False)

    live, pinned = [board("r7")], [board("r7"), board("r9")]
    view = ResultView(store, lambda: live, lambda: pinned)
    assert view.get(stored.id) is stored
    assert view.get("r7") is live[0]  # the live instance first
    assert view.get("r9") is pinned[1]
    assert view.get("r8") is None
    assert view.ids() == [stored.id, "r7", "r9"]
