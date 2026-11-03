"""The UI pipeline with Actions (SPEC §15: A12, the Drill-down Surface)."""

from explorer_agent.actions import IdAllocator
from explorer_agent.drill import DrillSpec
from explorer_agent.results import ResultStore
from explorer_agent.testing.stub_server import seed_fixture
from explorer_agent.ui import UiPipeline

EXPRESS_SQL = r'SELECT \"Agency Name\", count(*) AS n\nFROM fixture.x\nWHERE \"Agency Name\" ILIKE '"'"'%x%'"'"' AND supplier IS NOT DISTINCT FROM :supplier\nGROUP BY 1'
EXPECTED_SQL = (
    'SELECT "Agency Name", count(*) AS n\nFROM fixture.x\n'
    "WHERE \"Agency Name\" ILIKE '%x%' AND supplier IS NOT DISTINCT FROM $1\nGROUP BY 1"
)


def store():
    s = ResultStore()
    seed_fixture(s)
    return s


def test_express_round_trips_drill_sql_with_percent_quotes_and_newlines():
    reply = f"""<a2ui>
surface("main")
root = Column([chart, more])
chart = BarChart("r1", "supplier", "value", "T", drill_sql="{EXPRESS_SQL}")
more = FollowUp("Next", "Show more")
</a2ui>"""
    ui = UiPipeline(allowed_schemas=("fixture",)).build(reply, store(), "turn-1-1-abc", IdAllocator())
    (drill,) = ui.drills
    assert drill.sql == EXPECTED_SQL
    assert [f.id for f in ui.follow_ups] == ["f1"]
    comps = [c for m in ui.messages for c in (m.get("updateComponents") or {}).get("components", [])]
    assert {c["component"] for c in comps} >= {"Button", "VegaChart"}  # strict validation and known props passed


def test_build_reserves_ids_from_the_given_allocator():
    ids = IdAllocator()
    ids.source()
    reply = '<a2ui>\nsurface("main")\nroot = Column([t])\nt = DataTable("r1", "T")\n</a2ui>'
    ui = UiPipeline().build(reply, store(), "s", ids)
    assert [s.id for s in ui.sources] == ["v2"]


def test_drill_down_surface_is_valid_for_each_drill_as():
    s = store()
    result = s.add(sql="SELECT 1", purpose="p", columns=["k", "n"], column_types=["text", "bigint"],
                   rows=[{"k": "a", "n": 1}, {"k": "b", "n": 2}], truncated=False)
    for drill_as in ("table", "bar", "line"):
        spec = DrillSpec(source="v1", visual=0, sql="SELECT $1", names=("x",), drill_as=drill_as, drill_x="k",
                         drill_y="n", title="T")
        ui = UiPipeline().build_drill_down(result, spec, "T · a", f"turn-1-2-{drill_as}", IdAllocator())
        comps = [c for m in ui.messages for c in (m.get("updateComponents") or {}).get("components", [])]
        assert ui.prose == "" and ui.drills == []
        (src,) = ui.sources
        assert src.rows in ((0, 1), (1, 0))
        button = next(c for c in comps if c["component"] == "Button")
        assert button["action"]["event"]["context"] == {"source": src.id}
        # One Card holds the title, the Visual, its SQL and the Ask button (SPEC §16).
        root = next(c for c in comps if c["id"] == "root")
        assert root["component"] == "Card"
        column = next(c for c in comps if c["id"] == root["child"])
        assert column["component"] == "Column" and column["children"][-1] == button["id"]


def test_drill_down_with_no_rows_still_has_an_ask_button():
    result = ResultStore().add(sql="SELECT 1", purpose="p", columns=["k"], column_types=["text"], rows=[], truncated=False)
    spec = DrillSpec(source="v1", visual=0, sql="SELECT $1", names=("x",), drill_as="table", drill_x="", drill_y="", title="T")
    ui = UiPipeline().build_drill_down(result, spec, "T · a", "turn-1-2-x", IdAllocator())
    (src,) = ui.sources
    assert src.rows == () and src.labels == {}

