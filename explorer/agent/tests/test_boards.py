"""Boards, slice 1a (SPEC §18): loading and startup checks (B1), rendering (B3), the cache
(B4), Result IDs from the Conversation's counter (B21). Database tests are marked `db`."""

import asyncio
from pathlib import Path

import pytest

from explorer_agent.boards import (
    BOARD_ALIAS,
    EXPLORER,
    MAX_BOARD_QUERIES,
    BoardCache,
    BoardError,
    BoardRenderer,
    build_board,
    check_board,
    instance_results,
    load_board,
    load_boards,
)
from explorer_agent.macros import NO_ROWS
from explorer_agent.results import ResultStore
from explorer_agent.sql import SqlFail, SqlOk
from explorer_agent.testing.stub_server import STUB_BOARD, STUB_BOARD_ROWS, StubBoardRunner
from explorer_agent.ui import UiPipeline

ALLOWED = ("fixture", "mart")
GOOD = '''title = "Explorer"
question = "Where does the money go?"
layout = """root = Column([total, tiles])
total = Metric("totals", "value", "Total", 0, "currency")
tiles = Treemap("by_buyer", "By buyer", "buyer", "value")"""

[[queries]]
name = "totals"
sql = "SELECT count(*) AS n, sum(value) AS value FROM fixture.contracts"

[[queries]]
name = "by_buyer"
sql = """SELECT buyer, sum(value) AS value
FROM fixture.contracts GROUP BY buyer;"""
'''


def write(tmp_path: Path, text: str, name: str = EXPLORER) -> Path:
    (tmp_path / "boards").mkdir(exist_ok=True)
    path = tmp_path / "boards" / f"{name}.toml"
    path.write_text(text, encoding="utf-8")
    return path


# --- B1: loading (pure) --------------------------------------------------------------------------


def test_no_boards_directory_means_no_boards(tmp_path):
    assert load_boards(tmp_path, ALLOWED) == {}


def test_a_good_board_loads_with_normalised_sql(tmp_path):
    write(tmp_path, GOOD)
    boards = load_boards(tmp_path, ALLOWED)
    board = boards[EXPLORER]
    assert (board.title, board.question, board.scope) == ("Explorer", "Where does the money go?", "")
    assert [q.name for q in board.queries] == ["totals", "by_buyer"]
    assert not board.queries[1].sql.endswith(";")


def test_explorer_is_required_once_boards_exist(tmp_path):
    write(tmp_path, GOOD, name="supplier")
    with pytest.raises(BoardError, match="explorer.toml is required"):
        load_boards(tmp_path, ALLOWED)


def _replace(old: str, new: str) -> str:
    assert old in GOOD
    return GOOD.replace(old, new)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('extra = "x"\n' + GOOD, "unknown key extra"),
        (GOOD + '\nscope = "late"\n', "queries[1]: unknown key scope (top-level keys must come before the first [[queries]])"),
        ('[[controls]]\nname = "m"\n' + GOOD, "key controls is not supported yet"),
        ('key = "abn"\n' + GOOD, "key key is not supported yet"),
        (_replace('title = "Explorer"', 'title = "Top 10"'), "title must be plain text with no numbers"),
        (_replace('title = "Explorer"\n', ""), "missing key title"),
        (_replace('name = "totals"', 'name = "Totals"'), "queries[0]: name must match"),
        (_replace('name = "totals"', 'name = "r12"'), "looks like a Result ID"),
        (_replace('name = "by_buyer"', 'name = "totals"'), "name totals is used twice"),
        (_replace("FROM fixture.contracts GROUP", "FROM fixture.contracts WHERE buyer = :buyer GROUP"),
         "queries.by_buyer.sql: placeholder :buyer names no Control or key"),
        (_replace("FROM fixture.contracts GROUP", "FROM fixture.contracts WHERE buyer = $1 GROUP"),
         "queries.by_buyer.sql: write placeholders as :column"),
        (_replace("FROM fixture.contracts GROUP", "FROM secret.contracts GROUP"), "queries.by_buyer.sql:"),
        (_replace("root = Column", "main = Column"), "layout must define root"),
        (_replace("tiles = Treemap", "board_tiles = Treemap"), "layout name board_tiles is reserved"),
        (_replace("total = Metric", "totals = Metric"), "layout name totals is also a query name"),
        ('title = "Explorer"\nlayout = "root = Column([])"\n', "[[queries]] must hold at least one query"),
    ],
)
def test_bad_board_files_name_the_file_and_key(tmp_path, text, message):
    path = write(tmp_path, text)
    with pytest.raises(BoardError) as e:
        load_board(path, ALLOWED)
    assert str(path) in str(e.value)
    assert message in str(e.value)


def test_at_most_max_board_queries(tmp_path):
    queries = "".join(f'[[queries]]\nname = "q{i}"\nsql = "SELECT 1 AS x"\n\n' for i in range(MAX_BOARD_QUERIES + 1))
    path = write(tmp_path, 'title = "Explorer"\nlayout = "root = Column([])"\n' + queries)
    with pytest.raises(BoardError, match=f"at most {MAX_BOARD_QUERIES} per Board"):
        load_board(path, ALLOWED)


def test_dataset_loading_turns_board_errors_into_config_errors(tmp_path):
    from explorer_agent.config import ConfigError, load_dataset

    for name in ("notes.md", "glossary.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / "dataset.toml").write_text(
        'name = "t"\ndescription = "d"\nallowed_schemas = ["fixture"]\nnotes = "notes.md"\nglossary = "glossary.md"\n'
        '[currency]\ncode = "AUD"\nsymbol = "$"\n', encoding="utf-8")
    assert load_dataset(tmp_path).boards == {}
    write(tmp_path, GOOD.replace("Explorer", "Explorer 2"))
    with pytest.raises(ConfigError, match="no numbers"):
        load_dataset(tmp_path)


# --- B1: startup check (fake runner) ----------------------------------------------------------------


class CheckRunner:
    def __init__(self, outputs: dict[str, tuple[list[str], list[str]]], fail: str | None = None):
        self.outputs = outputs
        self.fail = fail
        self.sql: list[str] = []

    async def run(self, sql):
        self.sql.append(sql)
        for name, (columns, types) in self.outputs.items():
            if name in sql:
                if name == self.fail:
                    return SqlFail(sqlstate="42703", message='column "valu" does not exist')
                return SqlOk(columns=columns, column_types=types, rows=[], truncated=False, elapsed_ms=1)
        raise AssertionError(sql)


@pytest.fixture(scope="module")
def pipeline() -> UiPipeline:
    return UiPipeline(allowed_schemas=ALLOWED)


def _outputs(value_type: str = "numeric"):
    return {"count(*)": (["n", "value"], ["bigint", "numeric"]), "GROUP BY buyer": (["buyer", "value"], ["text", value_type])}


async def test_startup_check_runs_limit_0_and_checks_columns(tmp_path, pipeline):
    board = load_board(write(tmp_path, GOOD), ALLOWED)
    runner = CheckRunner(_outputs())
    outputs = await check_board(board, runner, pipeline)
    assert outputs == {"totals": (["n", "value"], ["bigint", "numeric"]), "by_buyer": (["buyer", "value"], ["text", "numeric"])}
    assert all(s.rstrip().endswith("LIMIT 0") for s in runner.sql)


async def test_startup_check_fails_on_a_query_error_and_on_a_column_of_the_wrong_kind(tmp_path, pipeline):
    board = load_board(write(tmp_path, GOOD), ALLOWED)
    with pytest.raises(BoardError, match=r"queries.by_buyer.sql fails \(SQLSTATE 42703\)"):
        await check_board(board, CheckRunner(_outputs(), fail="GROUP BY buyer"), pipeline)
    with pytest.raises(BoardError, match="value column 'value' of query 'by_buyer' is text, not a number"):
        await check_board(board, CheckRunner(_outputs("text")), pipeline)


async def test_startup_check_fails_on_a_layout_that_does_not_parse(tmp_path, pipeline):
    board = load_board(write(tmp_path, _replace("tiles])", "tiles")), ALLOWED)
    with pytest.raises(BoardError, match="layout"):
        await check_board(board, CheckRunner(_outputs()), pipeline)


# --- B1: Board drill_sql at startup (slice 1b) ------------------------------------------------------


DRILL = "SELECT buyer, count(*) AS n FROM fixture.contracts WHERE buyer IS NOT DISTINCT FROM :buyer GROUP BY buyer"


def _with_drill(drill: str, extra: str = "") -> str:
    return GOOD.replace('"buyer", "value")"""', f'"buyer", "value", drill_sql="{drill}"{extra})"""')


class DrillCheckRunner(CheckRunner):
    def __init__(self, outputs, drill: SqlOk | SqlFail | None = None):
        super().__init__(outputs)
        self.drill = drill or SqlOk(columns=["buyer", "n"], column_types=["text", "bigint"], rows=[], truncated=False, elapsed_ms=1)
        self.bound: list[tuple[str, list]] = []

    async def run_bound(self, sql, params, *, timeout_ms=None):
        self.bound.append((sql, params))
        return self.drill


async def test_board_drill_sql_runs_as_limit_0_with_null_placeholders(tmp_path, pipeline):
    board = load_board(write(tmp_path, _with_drill(DRILL)), ALLOWED)
    runner = DrillCheckRunner(_outputs())
    await check_board(board, runner, pipeline)
    [(sql, params)] = runner.bound
    assert sql.rstrip().endswith("LIMIT 0") and "$1" in sql and params == [None]


@pytest.mark.parametrize(("drill", "extra", "message"), [
    (DRILL.replace(":buyer", ":supplier"), "", r"tiles: drill_sql placeholder :supplier is not a column of query 'by_buyer'"),
    ("SELECT * FROM other.t WHERE buyer IS NOT DISTINCT FROM :buyer", "", r"tiles: drill_sql: .*other"),
    ("SELECT * FROM fixture.contracts", "", "tiles: drill_sql needs at least one :column placeholder"),
    (DRILL, ', drill_as="bar"', 'tiles: drill_as "bar" needs drill_x and drill_y'),
])
async def test_board_drill_sql_static_errors_stop_the_check(tmp_path, pipeline, drill, extra, message):
    board = load_board(write(tmp_path, _with_drill(drill, extra)), ALLOWED)
    runner = DrillCheckRunner(_outputs())
    with pytest.raises(BoardError, match=message):
        await check_board(board, runner, pipeline)
    assert runner.bound == []  # nothing ran


async def test_board_drill_sql_failing_at_limit_0_or_in_its_output_stops_the_check(tmp_path, pipeline):
    board = load_board(write(tmp_path, _with_drill(DRILL)), ALLOWED)
    untyped = SqlFail(sqlstate="42P18", message="could not determine data type of parameter $1")
    with pytest.raises(BoardError, match=r"tiles: drill_sql fails with its placeholders NULL \(SQLSTATE 42P18\).*add a cast"):
        await check_board(board, DrillCheckRunner(_outputs(), untyped), pipeline)
    good = load_board(write(tmp_path, _with_drill(DRILL, ', drill_as="bar", drill_x="buyer", drill_y="n"')), ALLOWED)
    await check_board(good, DrillCheckRunner(_outputs()), pipeline)  # a bar Drill-down with the right kinds passes
    bar = load_board(write(tmp_path, _with_drill(DRILL, ', drill_as="bar", drill_x="n", drill_y="buyer"')), ALLOWED)
    with pytest.raises(BoardError, match="tiles: drill_y 'buyer' is text, not a number"):
        await check_board(bar, DrillCheckRunner(_outputs()), pipeline)


# --- B4: the cache -------------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_cache_ttl_cap_and_pinned_entries():
    clock = Clock()
    cache = BoardCache(ttl_s=10, max_entries=2, clock=clock)
    r = {"q": object()}
    cache.put(("a",), r, {})
    cache.put(("b",), r, {})
    cache.put(("explorer",), r, {}, pin=True)
    cache.put(("c",), r, {})  # the cap drops the oldest, never the pinned entry
    assert cache.get(("a",)) == {} and cache.get(("b",)) and cache.get(("c",))
    clock.t = 10
    assert cache.get(("c",)) == {}  # expired
    # A pinned entry is served until warm() replaces it, even if a refresh fails (SPEC §18).
    assert cache.get(("explorer",))


def test_re_running_one_query_keeps_the_others_age():
    """Regression: a partial re-run used to restamp the whole entry, serving old rows past the TTL."""
    clock = Clock()
    cache = BoardCache(ttl_s=10, max_entries=5, clock=clock)
    a, c = object(), object()
    cache.put(("k",), {"a": a}, {})
    clock.t = 9
    cache.put(("k",), {"c": c}, cache.get(("k",)))  # only c ran now; a was kept from the cache
    clock.t = 10
    assert cache.get(("k",)) == {"c": c}  # a is 10 s old: expired, c is fresh


class BoardRunner:
    """Board queries with gates, call counts and failures on demand."""

    def __init__(self, rows=None):
        self.rows = rows or STUB_BOARD_ROWS
        self.sql: list[str] = []
        self.active = 0
        self.peak = 0
        self.gate: asyncio.Event | None = None
        self.fail: set[str] = set()

    async def run(self, sql):
        self.sql.append(sql)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.gate is not None:
                await self.gate.wait()
            else:
                await asyncio.sleep(0)
            for q in STUB_BOARD.queries:
                if q.sql in sql:
                    if q.name in self.fail:
                        return SqlFail(sqlstate="57014", message="canceling statement due to statement timeout")
                    return self.rows[q.name]
            raise AssertionError(sql)
        finally:
            self.active -= 1


def renderer(runner, **kw) -> BoardRenderer:
    args = {"concurrency": 2, "row_cap": 500, "ttl_s": 300, "max_cache": 20} | kw
    return BoardRenderer({"explorer": STUB_BOARD}, runner, **args)


async def test_same_key_runs_no_query_the_second_time():
    runner = BoardRunner()
    r = renderer(runner)
    first = await r.outcomes(STUB_BOARD)
    second = await r.outcomes(STUB_BOARD)
    assert len(runner.sql) == len(STUB_BOARD.queries)
    assert first == second


async def test_failed_queries_are_not_cached_and_rerun_alone():
    runner = BoardRunner()
    runner.fail = {"by_year"}
    r = renderer(runner)
    out = await r.outcomes(STUB_BOARD)
    assert isinstance(out["by_year"], SqlFail)
    runner.fail = set()
    runner.sql.clear()
    out = await r.outcomes(STUB_BOARD)
    assert len(runner.sql) == 1 and "GROUP BY year" in runner.sql[0]
    assert not isinstance(out["by_year"], SqlFail)


async def test_two_concurrent_cold_renders_run_once():
    runner = BoardRunner()
    runner.gate = asyncio.Event()
    r = renderer(runner)
    a = asyncio.create_task(r.outcomes(STUB_BOARD))
    b = asyncio.create_task(r.outcomes(STUB_BOARD))
    await asyncio.sleep(0.01)
    runner.gate.set()
    assert (await a) == (await b)
    assert len(runner.sql) == len(STUB_BOARD.queries)


async def test_agent_wide_semaphore_bounds_queries_across_conversations():
    runner = BoardRunner()
    runner.gate = asyncio.Event()
    r = renderer(runner, concurrency=1)
    other = type(STUB_BOARD)(**{**STUB_BOARD.__dict__, "name": "other"})
    r.boards["other"] = other
    tasks = [asyncio.create_task(r.outcomes(STUB_BOARD)), asyncio.create_task(r.outcomes(other))]
    await asyncio.sleep(0.01)
    assert runner.peak == 1
    runner.gate.set()
    await asyncio.gather(*tasks)
    assert runner.peak == 1


async def test_row_cap_wraps_the_query_and_reports_truncation():
    rows = dict(STUB_BOARD_ROWS)
    rows["by_buyer"] = SqlOk(["buyer", "value"], ["text", "numeric"],
                             [{"buyer": f"b{i}", "value": 1.0} for i in range(4)], False, 1)
    runner = BoardRunner(rows)
    out = await renderer(runner, row_cap=3).outcomes(STUB_BOARD)
    wrapped = next(s for s in runner.sql if "GROUP BY buyer" in s)
    assert f") AS {BOARD_ALIAS} LIMIT 4" in wrapped
    assert out["by_buyer"].truncated and out["by_buyer"].row_count == 3
    assert not out["totals"].truncated


async def test_warm_up_pins_the_explorer_and_refreshes_it_when_its_entry_expires():
    runner = BoardRunner()
    r = renderer(runner, ttl_s=0.05)
    await r.warm()
    assert r.cache.pinned and not r.cache.entries
    n = len(runner.sql)
    await asyncio.sleep(0.12)  # the refresh ran in the background
    r.stop()
    assert len(runner.sql) > n


# --- B3 and B21: building a Board -----------------------------------------------------------------


def _components(ui) -> list[dict]:
    return [c for m in ui.messages if "updateComponents" in m for c in m["updateComponents"]["components"]]


async def _built(pipeline, *, fail=(), rows=None, store=None, surface="turn-0-1-abc"):
    runner = BoardRunner(rows)
    runner.fail = set(fail)
    out = await renderer(runner).outcomes(STUB_BOARD)
    store = store or ResultStore()
    results, failed = instance_results(out, store.reserve_id)
    return build_board(STUB_BOARD, results, failed, pipeline, surface), results, store


async def test_board_heading_comes_first_then_the_layout_and_every_visual_shows_its_sql(pipeline):
    ui, _, _ = await _built(pipeline)
    comps = {c["id"]: c for c in _components(ui)}
    assert comps["root"]["children"] == ["board_heading", "board_layout"]
    assert [comps[i]["text"] for i in comps["board_heading"]["children"]] == [
        "Explorer", "Where does the money go?", "All fixture contracts.",  # captions aren't markdown
    ]
    assert comps["board_layout"]["component"] == "Column"
    sql = [c for c in comps.values() if c["component"] == "SqlDetails"]
    assert len(sql) == 5  # two Metrics, the treemap, the bar chart and the table
    # Clickable since slice 1b: the treemap, the line chart and the table, each with its source.
    actions = [c["action"] for c in comps.values() if "action" in c]
    assert [a["source"] for a in actions] == [s.id for s in ui.sources] and len(actions) == 3
    assert [d.source for d in ui.drills] == [actions[2]["source"]]  # the table's Drill query
    assert next(c for c in comps.values() if c["component"] == "DataGrid")["columns"][2]["bars"][0] == 1.0


async def test_board_heading_is_shown_as_written(pipeline):
    """h1–h5 and caption Text render as plain text in the client, so escaping showed backslashes."""
    board = type(STUB_BOARD)(**{**STUB_BOARD.__dict__, "title": "Spend *by* [agency]"})
    runner = BoardRunner()
    out = await renderer(runner).outcomes(STUB_BOARD)
    results, failed = instance_results(out, ResultStore().reserve_id)
    ui = build_board(board, results, failed, pipeline, "s")
    title = next(c for c in _components(ui) if c["id"] == "board_title")
    assert title["text"] == "Spend *by* [agency]"


async def test_panel_ids_are_stable_across_renders_whatever_the_rows(pipeline):
    one, _, _ = await _built(pipeline)
    fewer = dict(STUB_BOARD_ROWS)
    fewer["top_suppliers"] = SqlOk(fewer["top_suppliers"].columns, fewer["top_suppliers"].column_types,
                                   fewer["top_suppliers"].rows[:1], False, 1)
    two, _, _ = await _built(pipeline, rows=fewer)
    assert [c["id"] for c in _components(one)] == [c["id"] for c in _components(two)]


async def test_a_failed_query_gives_an_error_card_and_the_rest_renders(pipeline):
    ui, results, _ = await _built(pipeline, fail={"by_year"})
    texts = [c.get("text") for c in _components(ui) if c["component"] == "Text"]
    assert "This panel couldn't load: 57014" in texts
    assert "Value by buyer" in texts and "Top suppliers" in texts
    assert "by_year" not in results


async def test_zero_rows_give_no_rows_cards(pipeline):
    empty = {name: SqlOk(o.columns, o.column_types, [], False, 1) for name, o in STUB_BOARD_ROWS.items()}
    ui, _, _ = await _built(pipeline, rows=empty)
    texts = [c.get("text") for c in _components(ui) if c["component"] == "Text"]
    assert texts.count(NO_ROWS) == 4  # two Metrics, the treemap and the bar chart; the table shows no rows


async def test_board_result_ids_come_from_the_conversations_counter(pipeline):
    store = ResultStore()
    store.add(sql="SELECT 1", purpose="p", columns=["x"], column_types=["int"], rows=[], truncated=False)
    _, results, store = await _built(pipeline, store=store)
    ids = sorted(int(r.id[1:]) for r in results.values())
    assert ids == [2, 3, 4, 5]
    after = store.add(sql="SELECT 1", purpose="p", columns=["x"], column_types=["int"], rows=[], truncated=False)
    assert after.id == "r6"
    _, again, _ = await _built(pipeline, store=store)
    first_ids, again_ids = {r.id for r in results.values()}, {r.id for r in again.values()}
    assert first_ids.isdisjoint(again_ids)
    assert again_ids.isdisjoint(store.ids())


async def test_cached_results_are_shared_by_reference_under_fresh_ids():
    out = await renderer(BoardRunner()).outcomes(STUB_BOARD)
    a, _ = instance_results(out, ResultStore().reserve_id)
    s = ResultStore()
    s.reserve_id()
    b, _ = instance_results(out, s.reserve_id)
    assert a["totals"].id != b["totals"].id
    assert a["totals"].rows is b["totals"].rows


# --- B1 and B3 against the seed database (db) ----------------------------------------------------------


# A Board over the seed database's tables, for the database tests only: Dataset Boards come from
# the bootstrap tool (SPEC §18), so none is committed under datasets/.
SEED_BOARD = '''title = "Explorer"
layout = """root = Column([total, Row([tiles, trend]), suppliers])
total = Metric("totals", "total_value", "Total", 0, "currency_compact")
tiles = Treemap("by_agency", "By agency", "agency_name", "total_value", "currency_compact")
trend = LineChart("by_fy", "financial_year", "total_value", "By year", format="currency_compact")
suppliers = DataTable("top", "Top", "supplier_name,total_value", "total_value:currency_compact", bars="total_value")"""

[[queries]]
name = "totals"
sql = "SELECT sum(total_value) AS total_value FROM mart.agency"

[[queries]]
name = "by_agency"
sql = "SELECT agency_name, total_value FROM mart.agency ORDER BY total_value DESC"

[[queries]]
name = "by_fy"
sql = "SELECT financial_year, sum(total_value) AS total_value FROM mart.agency_fy GROUP BY 1 ORDER BY 1"

[[queries]]
name = "top"
sql = "SELECT supplier_name, total_value FROM mart.supplier ORDER BY total_value DESC LIMIT 10"
'''


@pytest.fixture
def seed_runner(ro_pool):
    from explorer_agent.sql import SqlRunner

    return SqlRunner(ro_pool, ("mart",), 5000, 5000)


@pytest.mark.db
async def test_a_board_over_the_seed_database_passes_the_startup_check_and_renders(seed_runner, tmp_path):
    write(tmp_path, SEED_BOARD)
    boards = load_boards(tmp_path, ("mart",))
    board = boards[EXPLORER]
    pipeline = UiPipeline(allowed_schemas=("mart",))
    await check_board(board, seed_runner, pipeline)
    out = await BoardRenderer(boards, seed_runner, concurrency=2, row_cap=500, ttl_s=300, max_cache=20).outcomes(board)
    assert not [n for n, o in out.items() if isinstance(o, SqlFail)]
    results, failed = instance_results(out, ResultStore().reserve_id)
    ui = build_board(board, results, failed, pipeline, "turn-0-1-x")
    assert any(c["component"] == "VegaChart" for c in _components(ui))


@pytest.mark.db
async def test_a_query_failing_at_limit_0_stops_the_check(seed_runner, tmp_path):
    text = SEED_BOARD.replace("SELECT agency_name,", "SELECT agency_nme,")
    board = load_board(write(tmp_path, text), ("mart",))
    with pytest.raises(BoardError, match=r"queries.by_agency.sql fails \(SQLSTATE 42703\)"):
        await check_board(board, seed_runner, UiPipeline(allowed_schemas=("mart",)))


@pytest.mark.db
async def test_a_board_drill_sql_is_checked_against_the_seed_database(seed_runner, tmp_path):
    """Postgres infers a NULL placeholder's type from the column it is compared with, and can't
    when nothing gives it one (42P18), which stops the check (SPEC §18 slice 1b)."""
    good = "SELECT supplier_name FROM mart.supplier WHERE supplier_name IS NOT DISTINCT FROM :supplier_name"
    text = SEED_BOARD.replace('bars="total_value")', f'bars="total_value", drill_sql="{good}")')
    board = load_board(write(tmp_path, text), ("mart",))
    await check_board(board, seed_runner, UiPipeline(allowed_schemas=("mart",)))
    untyped = "SELECT supplier_name FROM mart.supplier WHERE :supplier_name IS NULL"
    board = load_board(write(tmp_path, text.replace(good, untyped)), ("mart",))
    with pytest.raises(BoardError, match=r"suppliers: drill_sql fails with its placeholders NULL \(SQLSTATE 42P18\)"):
        await check_board(board, seed_runner, UiPipeline(allowed_schemas=("mart",)))


async def test_a_cancelled_caller_does_not_cancel_the_shared_render():
    """Regression: the leader's cancellation was handed to every joined waiter."""
    runner = BoardRunner()
    runner.gate = asyncio.Event()
    r = renderer(runner)
    leader = asyncio.create_task(r.outcomes(STUB_BOARD))
    await asyncio.sleep(0.01)
    joined = asyncio.create_task(r.outcomes(STUB_BOARD))
    await asyncio.sleep(0.01)
    leader.cancel()
    await asyncio.sleep(0.01)
    runner.gate.set()
    out = await joined
    assert leader.cancelled() and not isinstance(out["totals"], SqlFail)
    assert len(runner.sql) == len(STUB_BOARD.queries)
    assert r.cache.get(("explorer", None, ()))  # the run still filled the cache


async def test_a_panel_that_cannot_be_drawn_becomes_a_card_and_the_rest_renders(pipeline):
    rows = dict(STUB_BOARD_ROWS)
    rows["top_suppliers"] = SqlOk(["supplier", "contracts", "value"], ["text", "bigint", "numeric"],
                                  [{"supplier": "A", "contracts": 1, "value": -5.0}], False, 1)
    ui, _, _ = await _built(pipeline, rows=rows)
    texts = [c.get("text") for c in _components(ui) if c["component"] == "Text"]
    assert "This panel couldn't be drawn from its rows." in texts  # negative bars
    assert "Value by buyer" in texts


async def test_chart_panels_report_a_truncated_query(pipeline):
    rows = dict(STUB_BOARD_ROWS)
    rows["by_year"] = SqlOk(["year", "value"], ["text", "numeric"],
                            [{"year": str(2000 + i), "value": 1.0} for i in range(4)], False, 1)
    runner = BoardRunner(rows)
    out = await renderer(runner, row_cap=3).outcomes(STUB_BOARD)
    results, failed = instance_results(out, ResultStore().reserve_id)
    ui = build_board(STUB_BOARD, results, failed, pipeline, "s")
    captions = [c["text"] for c in _components(ui) if c["component"] == "Text" and c.get("variant") == "caption"]
    assert "query truncated at 3 rows" in captions


async def test_board_placeholders_are_not_logged_as_typed_numbers(tmp_path, pipeline, caplog):
    board = load_board(write(tmp_path, _replace('total = Metric("totals", "value", "Total", 0, "currency")',
                                                'total = Text("Total {totals[0].value|currency}")')), ALLOWED)
    s = ResultStore()
    from explorer_agent.results import StoredResult
    results = {"totals": StoredResult(id=s.reserve_id(), sql="x", purpose="p", columns=["n", "value"],
                                      column_types=["bigint", "numeric"], rows=[{"n": 1, "value": 5.0}], truncated=False),
               "by_buyer": StoredResult(id=s.reserve_id(), sql="x", purpose="p", columns=["buyer", "value"],
                                        column_types=["text", "numeric"], rows=[{"buyer": "a", "value": 1.0}], truncated=False)}
    ui = build_board(board, results, {}, pipeline, "s")
    assert ui.bare_numbers == []


async def test_follow_up_in_a_board_layout_is_a_startup_error(tmp_path, pipeline):
    board = load_board(write(tmp_path, _replace("root = Column([total, tiles])", "root = Column([total, tiles, more])\nmore = FollowUp(\"More\", \"Tell me more\")")), ALLOWED)
    with pytest.raises(BoardError, match="FollowUp is for answers, not Boards"):
        await check_board(board, CheckRunner(_outputs()), pipeline)


# --- the Explorer template (SPEC §18, slice 1a) -----------------------------------------------------

import re  # noqa: E402
import tomllib  # noqa: E402

TEMPLATE = Path(__file__).resolve().parents[2] / "bootstrap" / "templates" / "explorer.toml"


async def test_the_explorer_template_is_dataset_agnostic_and_loads_once_filled(tmp_path, pipeline):
    text = TEMPLATE.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert set(data) == {"title", "question", "scope", "layout", "queries"}  # top-level keys before [[queries]]
    assert [set(q) for q in data["queries"]] == [{"name", "sql"}] * 4
    assert all(q["sql"].startswith("<") for q in data["queries"])  # no SQL in the template, only slots
    # Fill every slot with something valid and check the shape loads and passes the startup check.
    fill = {
        "<totals_query>": "totals", "<share_query>": "by_buyer", "<trend_query>": "by_year", "<top_query>": "top_rows",
        "<category column>": "buyer", "<value column>": "value", "<period column>": "year", "<format>": "int",
        "<columns>": "buyer,value", "<column:format pairs>": "value:int", "<title>": "T",
        "<2 to 4 Metric calls on the totals query>": 'Metric("totals", "value", "Total")',
    }
    for slot, value in fill.items():
        text = text.replace(slot, value)
    text = re.sub(r'(?m)^(question|scope) = ".*"$', r'\1 = "x"', text)
    sql = {"totals": "SELECT sum(value) AS value FROM fixture.contracts",
           "by_buyer": "SELECT buyer, sum(value) AS value FROM fixture.contracts GROUP BY buyer",
           "by_year": "SELECT year, sum(value) AS value FROM fixture.contracts GROUP BY year",
           "top_rows": "SELECT buyer, value FROM fixture.contracts LIMIT 10"}
    for name, q in sql.items():
        text = re.sub(rf'(name = "{name}"\nsql = )"[^"]*"', rf'\1"{q}"', text)
    unfilled = [line for line in text.splitlines() if "<" in line and not line.lstrip().startswith("#")]
    assert unfilled == [], unfilled
    board = load_board(write(tmp_path, text), ALLOWED)
    runner = CheckRunner({"GROUP BY buyer": (["buyer", "value"], ["text", "numeric"]),
                          "GROUP BY year": (["year", "value"], ["text", "numeric"]),
                          "LIMIT 10": (["buyer", "value"], ["text", "numeric"]),
                          "contracts\n": (["value"], ["numeric"])})
    await check_board(board, runner, pipeline)


def test_boards_need_the_express_format(tmp_path):
    from explorer_agent.config import ConfigError, load_settings

    for name in ("notes.md", "glossary.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / "dataset.toml").write_text(
        'name = "t"\ndescription = "d"\nallowed_schemas = ["fixture"]\nnotes = "notes.md"\nglossary = "glossary.md"\n'
        '[currency]\ncode = "AUD"\nsymbol = "$"\n', encoding="utf-8")
    write(tmp_path, GOOD)
    env = {"DATABASE_URL": "postgresql://u:p@localhost:5432/db", "DATASET_DIR": str(tmp_path)}
    assert load_settings(env, tmp_path).dataset.boards
    with pytest.raises(ConfigError, match="FORMAT must be express"):
        load_settings({**env, "FORMAT": "direct_json"}, tmp_path)
