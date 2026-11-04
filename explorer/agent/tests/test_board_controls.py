"""Board Controls (SPEC §18 slice 2): loading, startup checks, values, the Controls Card and
changed sets (pure), and the typed "All" idiom against the seed database (B2)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from explorer_agent.boards import (
    ALL,
    CONTROLS_NOTE,
    DEFAULTS_TEXT,
    EXPLORER,
    BoardError,
    BoardRenderer,
    ControlError,
    Options,
    bound_values,
    build_board,
    changed_panels,
    check_board,
    controls_components,
    data_model,
    default_state,
    instance_results,
    layout_components,
    load_board,
    log_text,
    options_of,
    panel_components,
    panel_dependencies,
    parse_board,
    shown_values,
    validate_values,
)
from explorer_agent.drill import display_sql
from explorer_agent.results import ResultStore, StoredResult
from explorer_agent.sql import SqlFail, SqlOk
from explorer_agent.testing.stub_server import STUB_BOARD, STUB_BUYERS, StubBoardRunner
from explorer_agent.ui import UiPipeline

ALLOWED = ("fixture", "mart")
PATH = Path("<test>/boards/explorer.toml")

# One Control of each kind, each used by the one query.
KINDS = '''title = "Explorer"
layout = """root = Column([table])
table = DataTable("rows", "Rows", "n")"""

[[controls]]
name = "method"
kind = "choice"
label = "Method"
type = "text"
options_sql = "SELECT DISTINCT procurement_method AS value, procurement_method AS label FROM mart.contract ORDER BY 1"
all_label = "All methods"

[[controls]]
name = "amends"
kind = "multi"
label = "Amendments"
type = "int"
options_sql = "SELECT DISTINCT amendment_count AS value, amendment_count::text AS label FROM mart.contract ORDER BY 1"

[[controls]]
name = "hide_cancelled"
kind = "toggle"
label = "Hide cancelled"

[[controls]]
name = "min_suppliers"
kind = "number"
label = "Suppliers at least"
min = 0
max = 99999999999
default = 0

[[controls]]
name = "since"
kind = "date"
label = "Published since"
optional = true

[[queries]]
name = "rows"
sql = """SELECT count(*) AS n FROM mart.contract
WHERE (:method IS NULL OR procurement_method = :method)
  AND (:amends IS NULL OR amendment_count = ANY(:amends))
  AND (NOT :hide_cancelled OR NOT is_cancelled)
  AND supplier_count >= :min_suppliers
  AND (:since IS NULL OR publish_date >= :since)"""
'''


def board(text: str = KINDS):
    return parse_board(text, PATH, ALLOWED)


def replace(old: str, new: str, text: str = KINDS) -> str:
    assert old in text, old
    return text.replace(old, new, 1)


# --- loading (B1, B6) -------------------------------------------------------------------------


def test_each_kind_loads_with_its_type_cast_and_nullability():
    b = board()
    by = {c.name: c for c in b.controls}
    assert [c.name for c in b.controls] == ["method", "amends", "hide_cancelled", "min_suppliers", "since"]
    assert by["method"].cast == "text" and by["method"].nullable and by["method"].default == ALL
    assert by["amends"].cast == "bigint[]" and by["amends"].nullable and by["amends"].default == ()
    assert by["hide_cancelled"].cast == "boolean" and not by["hide_cancelled"].nullable and by["hide_cancelled"].default is False
    assert by["min_suppliers"].cast == "bigint" and not by["min_suppliers"].nullable  # int binds as bigint
    assert by["since"].cast == "date" and by["since"].nullable and by["since"].default == ""
    q = b.queries[0]
    assert q.names == ("method", "amends", "hide_cancelled", "min_suppliers", "since")
    assert "$1::text IS NULL" in q.dollar and "ANY($2::bigint[])" in q.dollar and "NOT $3::boolean" in q.dollar
    assert ":method" in q.sql  # the written form is kept


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('kind = "toggle"', 'kind = "slider"', "controls.hide_cancelled.kind must be one of"),
        ('label = "Method"\n', "", "controls.method.label must be non-empty text"),
        ('type = "text"\n', "", "controls.method.type must be one of"),
        ('type = "text"', 'type = "money"', "controls.method.type must be one of"),
        ('all_label = "All methods"\n', 'default = "all"\n', 'controls.method.default "all" needs all_label'),
        ("min = 0\n", "", "controls.min_suppliers: min and max must both be an integer"),
        ("max = 99999999999\ndefault = 0", "max = 5\ndefault = 6", "controls.min_suppliers.default must be between min and max"),
        ("default = 0", "default = 0.5", "number Controls are integer-only"),
        ("optional = true\n", "", 'controls.since.default must be an ISO date'),
        ('name = "since"', 'name = "method"', "controls.method: name method is used twice"),
        ('name = "since"', 'name = "r3"', "controls[4]: name must match"),
        ('label = "Hide cancelled"', 'label = "Hide cancelled"\nstep = 2', "controls.hide_cancelled: unknown key step for a toggle"),
        ("FROM mart.contract ORDER BY 1\"\nall_label", "FROM secret.contract ORDER BY 1\"\nall_label", "controls.method.options_sql:"),
        ("FROM mart.contract ORDER BY 1\"\nall_label", "FROM mart.contract WHERE x = :y\"\nall_label",
         "controls.method.options_sql: takes no placeholders"),
        ("min = 0\nmax = 99999999999", 'min = 0\nmax = 9\nrange_sql = "SELECT 0 AS min, 9 AS max"',
         "give min and max, or range_sql, not both"),
        ("AND (:since IS NULL", "AND (:until IS NULL", "placeholder :until names no Control"),
    ],
)
def test_bad_controls_name_the_file_and_key(old, new, message):
    with pytest.raises(BoardError) as e:
        board(replace(old, new))
    assert str(PATH) in str(e.value)
    assert message in str(e.value)


def test_a_choice_without_all_label_defaults_to_its_first_option_and_rejects_all():
    b = board(replace('all_label = "All methods"\n', 'default = "open"\n'))
    c = b.control("method")
    assert c.default == "open" and not c.nullable


# --- startup check (B1, B2) ------------------------------------------------------------------


class CheckRunner:
    """`LIMIT 0` answers by SQL: the query's columns, and the side queries' as configured."""

    def __init__(self, columns=("n",), types=("bigint",), options=("value", "label"), fail_at_null=False):
        self.columns, self.types, self.options = list(columns), list(types), list(options)
        self.bound: list[list] = []
        self.fail_at_null = fail_at_null

    async def run(self, sql):
        if "AS value" in sql:
            return SqlOk(self.options, ["text"] * len(self.options), [], False, 1)
        if "AS min" in sql:
            return SqlOk(["min", "max"], ["integer", "integer"], [], False, 1)
        return SqlOk(self.columns, self.types, [], False, 1)

    async def run_bound(self, sql, params, *, timeout_ms=None):
        self.bound.append(list(params))
        if self.fail_at_null and params[0] is None:
            return SqlFail(sqlstate="42883", message="operator does not exist")
        return SqlOk(self.columns, self.types, [], False, 1)


@pytest.fixture(scope="module")
def pipeline() -> UiPipeline:
    return UiPipeline(allowed_schemas=ALLOWED)


async def test_startup_runs_each_query_at_the_defaults_and_with_nullable_controls_null(pipeline):
    runner = CheckRunner()
    await check_board(board(), runner, pipeline)
    assert runner.bound == [
        [None, None, False, 0, None],  # defaults: All, none chosen, off, 0, no date
        [None, None, False, 0, None],  # nullable ones at NULL: the same here, as they all default to NULL
    ]
    b = board(replace('all_label = "All methods"', 'all_label = "All methods"\ndefault = "open"'))
    runner = CheckRunner()
    await check_board(b, runner, pipeline)
    assert runner.bound[0][0] == "open" and runner.bound[1][0] is None


async def test_a_query_failing_at_null_stops_the_check(pipeline):
    b = board(replace('all_label = "All methods"', 'all_label = "All methods"\ndefault = "open"'))
    with pytest.raises(BoardError, match=r"queries.rows.sql fails with its nullable Controls NULL \(SQLSTATE 42883\)"):
        await check_board(b, CheckRunner(fail_at_null=True), pipeline)


@pytest.mark.parametrize(
    ("runner", "message"),
    [
        (CheckRunner(columns=("n", "method"), types=("bigint", "text")), "controls.method: name is also an output column of query 'rows'"),
        (CheckRunner(options=("value", "name")), "controls.method.options_sql must return value and label columns; missing label"),
    ],
)
async def test_control_collisions_and_options_columns_stop_the_check(pipeline, runner, message):
    with pytest.raises(BoardError) as e:
        await check_board(board(), runner, pipeline)
    assert message in str(e.value)


async def test_an_unused_control_stops_the_check(pipeline):
    text = replace("  AND (:since IS NULL OR publish_date >= :since)", "")
    with pytest.raises(BoardError, match="controls.since: no query or drill_sql uses :since"):
        await check_board(board(text), CheckRunner(), pipeline)


async def test_a_control_used_only_by_a_drill_sql_counts_as_used(pipeline):
    text = replace("  AND (:since IS NULL OR publish_date >= :since)", "")
    text = replace('table = DataTable("rows", "Rows", "n")',
                   'table = DataTable("rows", "Rows", "n", drill_sql="SELECT 1 AS one FROM mart.contract WHERE supplier_count = :n AND (:since IS NULL OR publish_date >= :since)")',
                   text)
    runner = CheckRunner()
    await check_board(board(text), runner, pipeline)
    assert runner.bound[-1] == [None, None]  # the drill: row column NULL, the Control at its default (NULL)


async def test_range_sql_must_return_min_and_max_of_the_right_kind(pipeline):
    text = replace("min = 0\nmax = 99999999999", 'range_sql = "SELECT 0 AS min, 9 AS max"')
    await check_board(board(text), CheckRunner(), pipeline)
    text = replace("optional = true", 'optional = true\nrange_sql = "SELECT 0 AS min, 9 AS max"')
    with pytest.raises(BoardError, match="controls.since.range_sql: min is integer, not a date"):
        await check_board(board(text), CheckRunner(), pipeline)


# --- values (B6, B21) -------------------------------------------------------------------------


def opts(board_, **over) -> dict[str, Options]:
    base = {
        "method": Options(("open", "limited", "select"), ("Open", "Limited", "Select")),
        "amends": Options((0, 1, 2), ("0", "1", "2")),
        "hide_cancelled": Options(),
        "min_suppliers": Options(lo=0, hi=99999999999),
        "since": Options(),
    }
    return {c.name: over.get(c.name, base[c.name]) for c in board_.controls}


def test_defaults_bind_typed_nulls_and_fall_back_when_missing(caplog):
    b = board()
    o = opts(b)
    state = default_state(b, o)
    assert state == {"method": None, "amends": [], "hide_cancelled": False, "min_suppliers": 0, "since": None}
    assert bound_values(b, o, state) == {"method": None, "amends": None, "hide_cancelled": False, "min_suppliers": 0, "since": None}
    # A default that isn't an option: All with all_label, the first option without; logged by name only.
    b2 = board(replace('all_label = "All methods"', 'all_label = "All methods"\ndefault = "gone"'))
    assert default_state(b2, opts(b2))["method"] is None
    b3 = board(replace('all_label = "All methods"\n', 'default = "gone"\n'))
    assert default_state(b3, opts(b3))["method"] == 0
    assert "control=method" in caplog.text and "gone" not in caplog.text
    b4 = board(replace('label = "Amendments"', 'label = "Amendments"\ndefault = [1, 7]'))
    assert default_state(b4, opts(b4))["amends"] == [1]
    # A number default outside a range_sql range is clamped.
    b5 = board(replace("min = 0\nmax = 99999999999\ndefault = 0", 'range_sql = "SELECT 1 AS min, 3 AS max"\ndefault = 9'))
    assert default_state(b5, opts(b5, min_suppliers=Options(lo=1, hi=3)))["min_suppliers"] == 3


def test_bound_values_take_option_values_and_lists():
    b = board()
    o = opts(b)
    state = {"method": 1, "amends": [0, 2], "hide_cancelled": True, "min_suppliers": 99999999999, "since": "2024-07-01"}
    assert bound_values(b, o, state) == {"method": "limited", "amends": [0, 2], "hide_cancelled": True,
                                         "min_suppliers": 99999999999, "since": "2024-07-01"}


GOOD_VALUES = {"method": None, "amends": [], "hide_cancelled": False, "min_suppliers": 0, "since": None}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"extra": 1}, "Unknown control."),
        ({"method": 3}, "Method"),  # index out of range
        ({"method": True}, "Method"),  # a bool isn't an index
        ({"method": "1"}, "Method"),
        ({"amends": [0, 0]}, "Amendments"),
        ({"amends": 1}, "Amendments"),
        ({"hide_cancelled": 1}, "Hide cancelled"),
        ({"min_suppliers": 1.5}, "Suppliers at least"),
        ({"min_suppliers": -1}, "Suppliers at least"),
        ({"since": "2024-7-1"}, "Published since"),
        ({"since": "2026-W01-1"}, "Published since"),  # an ISO week date: fromisoformat takes it
        ({"since": "20240701"}, "Published since"),
        ({"since": ""}, "Published since"),
    ],
)
def test_every_validation_case_is_rejected(change, message):
    b = board()
    with pytest.raises(ControlError, match=message):
        validate_values(b, opts(b), {**GOOD_VALUES, **change})


def test_missing_controls_all_without_all_label_and_a_cleared_required_date_are_rejected():
    b = board()
    with pytest.raises(ControlError, match="missing"):
        validate_values(b, opts(b), {k: v for k, v in GOOD_VALUES.items() if k != "since"})
    b2 = board(replace('all_label = "All methods"\n', 'default = "open"\n').replace("optional = true\n", 'default = "2024-01-01"\n'))
    with pytest.raises(ControlError, match="Method"):
        validate_values(b2, opts(b2), {**GOOD_VALUES, "since": "2024-01-01"})
    with pytest.raises(ControlError, match="Published since"):
        validate_values(b2, opts(b2), {**GOOD_VALUES, "method": 0})


def test_valid_values_are_accepted_and_multi_is_sorted():
    b = board()
    out = validate_values(b, opts(b), {**GOOD_VALUES, "method": 2, "amends": [2, 0], "since": "2024-02-29"})
    assert out["method"] == 2 and out["amends"] == [0, 2]


def test_data_model_holds_index_strings_all_and_empty_dates():
    b = board()
    assert data_model(b, GOOD_VALUES) == {"method": [ALL], "amends": [], "hide_cancelled": False, "min_suppliers": 0, "since": ""}
    assert data_model(b, {**GOOD_VALUES, "method": 2, "amends": [0, 1], "since": "2024-01-02"})["method"] == ["2"]


def test_log_text_lists_controls_off_their_defaults_in_order_and_resets():
    b = board()
    o = opts(b)
    defaults = default_state(b, o)
    assert log_text(b, o, defaults, defaults) == DEFAULTS_TEXT
    state = {**defaults, "since": "2024-01-02", "method": 1, "amends": [1, 2], "hide_cancelled": True}
    assert log_text(b, o, state, defaults) == (
        "Method set to Limited · Amendments set to 1, 2 · Hide cancelled set to on · Published since set to 2024-01-02"
    )
    assert shown_values(b, o, defaults) == {"Method": "All methods", "Amendments": "none", "Hide cancelled": "off",
                                            "Suppliers at least": "0", "Published since": "any"}


# --- the Controls Card (B6, B21) ----------------------------------------------------------------


def _side(name, rows, truncated=False):
    return StoredResult(id="", sql="", purpose="", columns=["value", "label"], column_types=["text", "text"],
                        rows=rows, truncated=truncated)


def test_options_drop_null_values_and_cap_at_max_control_options():
    b = board()
    rows = [{"value": None, "label": "(none)"}] + [{"value": f"m{i}", "label": f"M{i}"} for i in range(5)]
    out = options_of(b, {"options.method": _side("method", rows)}, 3)
    assert out["method"].values == ("m0", "m1", "m2") and out["method"].cut
    assert not options_of(b, {"options.method": _side("method", rows[:3])}, 3)["method"].cut


def test_controls_card_builds_each_kind_bound_to_controls():
    b = board()
    o = opts(b, method=Options(("open", "limited"), ("Open", "Limited"), cut=True),
             amends=Options(tuple(range(11)), tuple(str(i) for i in range(11))))
    comps = {c["id"]: c for c in controls_components(b, o)}
    assert comps["board_controls"] == {"id": "board_controls", "component": "Card", "child": "board_controls_items"}
    method = comps["board_control_method"]
    assert method["component"] == "ChoicePicker" and method["variant"] == "mutuallyExclusive"
    assert method["options"] == [{"label": "All methods", "value": ALL}, {"label": "Open", "value": "0"},
                                 {"label": "Limited", "value": "1"}]
    assert method["value"] == {"path": "/controls/method"} and "filterable" not in method
    assert comps["board_control_method_note"]["text"] == CONTROLS_NOTE.format(n=2)
    amends = comps["board_control_amends"]
    assert amends["variant"] == "multipleSelection" and amends["filterable"] is True  # over 10 options
    assert comps["board_control_hide_cancelled"]["component"] == "CheckBox"
    slider = comps["board_control_min_suppliers"]
    assert slider["component"] == "Slider" and (slider["min"], slider["max"]) == (0, 99999999999)
    date = comps["board_control_since"]
    assert date["component"] == "DateTimeInput" and date["enableDate"] is True
    assert comps["board_controls_items"]["children"] == [
        "board_control_method", "board_control_method_note", "board_control_amends", "board_control_hide_cancelled",
        "board_control_min_suppliers", "board_control_since",
    ]


async def test_the_stub_board_puts_its_controls_card_between_heading_and_layout_with_its_data_model(pipeline):
    r = BoardRenderer({EXPLORER: STUB_BOARD}, StubBoardRunner(), concurrency=2, row_cap=500, ttl_s=300, max_cache=20)
    options, state, bound = await r.defaults(STUB_BOARD)
    assert options["buyer"].values == STUB_BUYERS and state == {"buyer": None} and bound == {"buyer": None}
    out = await r.outcomes(STUB_BOARD, bound)
    results, failed = instance_results(out, ResultStore().reserve_id)
    ui = build_board(STUB_BOARD, results, failed, pipeline, "turn-0-1-x", None, options, state)
    comps = {c["id"]: c for m in ui.messages for c in (m.get("updateComponents") or {}).get("components", [])}
    assert comps["root"]["children"] == ["board_heading", "board_controls", "board_layout"]
    model = [m["updateDataModel"] for m in ui.messages if "updateDataModel" in m]
    assert model == [{"surfaceId": "turn-0-1-x", "path": "/controls", "value": {"buyer": [ALL]}}]
    assert list(ui.messages[-1]) == ["version", "updateDataModel"]  # after the components


# --- changed sets (B7) ---------------------------------------------------------------------------


def test_a_change_re_runs_only_the_queries_using_it_and_re_sends_their_panels(pipeline):
    deps = panel_dependencies(STUB_BOARD, layout_components(STUB_BOARD, pipeline))
    assert deps["years"] == (frozenset({"by_year"}), frozenset())
    assert changed_panels(STUB_BOARD, deps, {"buyer"}) == ({"by_year"}, {"years"})
    assert changed_panels(STUB_BOARD, deps, set()) == (set(), set())


def test_a_drill_sql_using_a_control_re_sends_its_panel_without_re_running_its_query(pipeline):
    layout = STUB_BOARD.layout.replace(
        "WHERE supplier IS NOT DISTINCT FROM :supplier",
        "WHERE supplier IS NOT DISTINCT FROM :supplier AND (:buyer IS NULL OR buyer = :buyer)",
    )
    import dataclasses

    b = dataclasses.replace(STUB_BOARD, layout=layout)
    deps = panel_dependencies(b, layout_components(b, pipeline))
    assert deps["suppliers"] == (frozenset({"top_suppliers"}), frozenset({"buyer"}))
    assert changed_panels(b, deps, {"buyer"}) == ({"by_year"}, {"years", "suppliers"})


def test_panel_components_are_a_panels_root_and_its_expansion():
    messages = [{"updateComponents": {"components": [
        {"id": "root"}, {"id": "board_layout"}, {"id": "years"}, {"id": "years__Text_0"}, {"id": "yearsx"},
        {"id": "tiles__Card_1"}, {"id": "root__Text_2"},
    ]}}]
    assert [c["id"] for c in panel_components(messages, {"years"})] == ["years", "years__Text_0"]
    assert [c["id"] for c in panel_components(messages, {"root"})] == ["board_layout", "root__Text_2"]


# --- Board Drill queries with Control placeholders (B10, pure part) -----------------------------


async def test_a_board_drill_binds_the_controls_values_and_skips_their_null_drop_check(pipeline):
    import dataclasses

    drill = "SELECT buyer, count(*) AS contracts FROM fixture.contracts WHERE supplier IS NOT DISTINCT FROM :supplier AND buyer = :buyer GROUP BY buyer"
    b = dataclasses.replace(STUB_BOARD, layout=STUB_BOARD.layout.replace(
        "WHERE supplier IS NOT DISTINCT FROM :supplier GROUP BY buyer", "WHERE " + drill.split("WHERE ", 1)[1]))
    r = BoardRenderer({EXPLORER: b}, StubBoardRunner(), concurrency=2, row_cap=500, ttl_s=300, max_cache=20)
    options, _state, _bound = await r.defaults(b)
    for state, value in (({"buyer": None}, None), ({"buyer": 1}, "Buyer B")):
        out = await r.outcomes(b, bound_values(b, options, state))
        results, failed = instance_results(out, ResultStore().reserve_id)
        ui = build_board(b, results, failed, pipeline, "turn-0-1-x", None, options, state)
        (spec,) = ui.drills  # `= :buyer` with buyer NULL is "All", not a dropped drill
        assert spec.names == ("supplier", "buyer") and spec.fixed == (("buyer", value),)
        assert "$2::text" in spec.sql
        assert spec.values({"supplier": "Supplier A"}) == ["Supplier A", value]


# --- B2: the "All" idiom against the seed database ----------------------------------------------


@pytest.fixture
def seed_runner(ro_pool):
    from explorer_agent.sql import SqlRunner

    return SqlRunner(ro_pool, ("mart",), 5000, 5000)


async def _count(runner, where: str = "TRUE") -> int:
    out = await runner.run(f"SELECT count(*) AS n FROM mart.contract WHERE {where}")
    assert not isinstance(out, SqlFail), out
    return out.rows[0]["n"]


@pytest.mark.db
async def test_every_kind_runs_at_a_value_and_at_null_against_real_column_types(seed_runner, tmp_path):
    pipeline = UiPipeline(allowed_schemas=("mart",))
    path = tmp_path / "boards" / "explorer.toml"
    path.parent.mkdir()
    path.write_text(KINDS.replace("fixture.", "mart."), encoding="utf-8")
    b = load_board(path, ("mart",))
    await check_board(b, seed_runner, pipeline)  # LIMIT 0 at the defaults and at NULL
    r = BoardRenderer({EXPLORER: b}, seed_runner, concurrency=2, row_cap=500, ttl_s=300, max_cache=20)
    options, state, bound = await r.defaults(b)
    method = options["method"].values.index(next(v for v in options["method"].values if v is not None))
    amends = [0, 1][: len(options["amends"].values)]
    cases = [
        (state, "TRUE"),  # every Control at NULL / its default
        ({**state, "method": method}, f"procurement_method = '{options['method'].values[method]}'"),
        ({**state, "amends": amends},
         f"amendment_count = ANY(ARRAY[{','.join(str(options['amends'].values[i]) for i in amends)}]::bigint[])"),
        ({**state, "hide_cancelled": True}, "NOT is_cancelled"),
        ({**state, "min_suppliers": 2}, "supplier_count >= 2"),
        ({**state, "min_suppliers": 99999999999}, "supplier_count >= 99999999999"),  # int binds as bigint
        ({**state, "since": "2024-01-01"}, "publish_date >= DATE '2024-01-01'"),
    ]
    assert isinstance(options["amends"].values[0], int)  # an int column's options bind as bigint[]
    for values, where in cases:
        out = await r.outcomes(b, bound_values(b, options, values))
        result = out["rows"]
        assert not isinstance(result, SqlFail), (values, result)
        assert result.rows[0]["n"] == await _count(seed_runner, where), (values, where)
        # The display copy keeps the casts and reruns to the same rows.
        assert "::" in result.sql and ":method" not in result.sql
        again = await seed_runner.run(result.sql)
        assert not isinstance(again, SqlFail), result.sql
        assert again.rows == result.rows


@pytest.mark.db
async def test_the_display_copy_of_a_date_and_a_list_reruns(seed_runner):
    sql = "SELECT count(*) AS n FROM mart.contract WHERE ($1::date IS NULL OR publish_date >= $1::date) AND ($2::bigint[] IS NULL OR amendment_count = ANY($2::bigint[]))"
    values = [dt.date(2024, 1, 1), [0, 1]]
    bound = await seed_runner.run_bound(sql, values)
    shown = await seed_runner.run(display_sql(sql, values))
    assert not isinstance(bound, SqlFail) and not isinstance(shown, SqlFail)
    assert bound.rows == shown.rows
