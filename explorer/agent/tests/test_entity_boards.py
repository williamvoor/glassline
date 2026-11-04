"""Entity Boards, slice 3 (SPEC §18): keys in Board files (B1), the key in the cache key (B4),
the low-priority permit of Board prefetch (B21). Executor and Macro cases live in
`test_entity_executor.py` and `test_macros.py`."""

import asyncio
from pathlib import Path

import pytest

from explorer_agent.boards import (
    EXPLORER,
    BoardError,
    BoardRenderer,
    BoardSemaphore,
    check_board,
    load_board,
    load_boards,
    render_key,
    startup_values,
)
from explorer_agent.sql import SqlOk
from explorer_agent.ui import UiPipeline

ALLOWED = ("fixture",)
EXPLORER_TOML = '''title = "Explorer"
layout = """root = Column([t])
t = DataTable("by_buyer", "By buyer")"""

[[queries]]
name = "by_buyer"
sql = "SELECT buyer, sum(value) AS value FROM fixture.contracts GROUP BY buyer"
'''
BUYER_TOML = '''title = "Buyer"
scope = "All of the buyer's contracts."
key = "buyer"
key_type = "text"
key_columns = ["buyer", "agency"]
layout = """root = Column([t])
t = DataTable("suppliers", "Suppliers")"""

[[queries]]
name = "suppliers"
sql = "SELECT supplier, buyer, sum(value) AS value FROM fixture.contracts WHERE buyer = :buyer GROUP BY supplier, buyer"
'''


def write(tmp_path: Path, text: str, name: str) -> Path:
    (tmp_path / "boards").mkdir(exist_ok=True)
    path = tmp_path / "boards" / f"{name}.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _buyer(old: str = "", new: str = "") -> str:
    assert old in BUYER_TOML
    return BUYER_TOML.replace(old, new, 1) if old else BUYER_TOML


# --- B1: loading ---------------------------------------------------------------------------------


def test_a_keyed_board_loads_with_its_key_cast_like_a_control(tmp_path):
    board = load_board(write(tmp_path, BUYER_TOML, "buyer"), ALLOWED)
    assert (board.key, board.key_type, board.key_columns) == ("buyer", "text", ("buyer", "agency"))
    assert board.queries[0].names == ("buyer",)
    assert "$1::text" in board.queries[0].dollar


def test_an_int_key_binds_as_bigint(tmp_path):
    text = _buyer('key_type = "text"', 'key_type = "int"')
    board = load_board(write(tmp_path, text, "buyer"), ALLOWED)
    assert "$1::bigint" in board.queries[0].dollar


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('key_type = "text"\n', "", "key, key_type and key_columns go together"),
        ('key_columns = ["buyer", "agency"]\n', "", "key, key_type and key_columns go together"),
        ('key = "buyer"', 'key = "Buyer"', "key must match [a-z_][a-z0-9_]*"),
        ('key = "buyer"', 'key = "r1"', "key must match [a-z_][a-z0-9_]*"),
        ('key_type = "text"', 'key_type = "uuid"', "key_type must be one of: text, int, float, date, bool"),
        ('key_columns = ["buyer", "agency"]', "key_columns = []", "key_columns must be a non-empty list"),
        ('key_columns = ["buyer", "agency"]', 'key_columns = ["buyer", "buyer"]', "key_columns names buyer twice"),
        ('key_columns = ["buyer", "agency"]', 'key_columns = ["Buyer"]', "key_columns must be a non-empty list"),
        ("WHERE buyer = :buyer ", "", "no query uses :buyer"),
        ("WHERE buyer = :buyer", "WHERE buyer = :buyerx", "placeholder :buyerx names no Control or the key"),
    ],
)
def test_bad_keys_name_the_file_and_key(tmp_path, old, new, message):
    path = write(tmp_path, _buyer(old, new), "buyer")
    with pytest.raises(BoardError) as e:
        load_board(path, ALLOWED)
    assert str(path) in str(e.value)
    assert message in str(e.value)


def test_a_key_must_not_equal_a_control_name(tmp_path):
    text = _buyer("[[queries]]", '[[controls]]\nname = "buyer"\nkind = "toggle"\nlabel = "B"\n\n[[queries]]')
    with pytest.raises(BoardError, match="key buyer is also a Control name"):
        load_board(write(tmp_path, text, "buyer"), ALLOWED)


def test_the_explorer_cannot_have_a_key(tmp_path):
    write(tmp_path, BUYER_TOML, EXPLORER)
    with pytest.raises(BoardError, match="explorer.toml can't have a key"):
        load_boards(tmp_path, ALLOWED)


def test_key_columns_must_not_overlap_between_boards(tmp_path):
    write(tmp_path, EXPLORER_TOML, EXPLORER)
    write(tmp_path, BUYER_TOML, "buyer")
    write(tmp_path, BUYER_TOML.replace('title = "Buyer"', 'title = "Agency"').replace(
        '["buyer", "agency"]', '["agency_id", "agency"]'), "agency")
    with pytest.raises(BoardError, match=r"buyer.toml: key_columns: agency also opens .*agency.toml"):
        load_boards(tmp_path, ALLOWED)


def test_entity_boards_load_beside_the_explorer(tmp_path):
    write(tmp_path, EXPLORER_TOML, EXPLORER)
    write(tmp_path, BUYER_TOML, "buyer")
    boards = load_boards(tmp_path, ALLOWED)
    assert boards["buyer"].key == "buyer" and boards[EXPLORER].key == ""


# --- B1: startup check binds the key to NULL -----------------------------------------------------------


class BoundRunner:
    def __init__(self):
        self.calls: list[tuple[str, list]] = []

    async def run(self, sql):
        raise AssertionError(f"a keyed query must run bound: {sql}")

    async def run_bound(self, sql, params, *, timeout_ms=None):
        self.calls.append((sql, list(params)))
        return SqlOk(["supplier", "buyer", "value"], ["text", "text", "numeric"], [], False, 1)


async def test_the_startup_check_runs_limit_0_with_the_key_null(tmp_path):
    board = load_board(write(tmp_path, BUYER_TOML, "buyer"), ALLOWED)
    runner = BoundRunner()
    await check_board(board, runner, UiPipeline(allowed_schemas=ALLOWED))
    assert runner.calls and all(params == [None] for _sql, params in runner.calls)
    assert all(sql.rstrip().endswith("LIMIT 0") for sql, _ in runner.calls)
    assert startup_values(board) == {"buyer": None}


async def test_a_board_drill_sql_may_name_the_key_as_a_row_column(tmp_path):
    """The key isn't bound in a Board's drill_sql: `:buyer` there is the row's column."""
    text = _buyer('t = DataTable("suppliers", "Suppliers")',
                  't = DataTable("suppliers", "Suppliers", drill_sql="SELECT 1 AS n FROM fixture.contracts '
                  'WHERE supplier IS NOT DISTINCT FROM :supplier AND buyer IS NOT DISTINCT FROM :buyer")')
    board = load_board(write(tmp_path, text, "buyer"), ALLOWED)
    runner = BoundRunner()
    await check_board(board, runner, UiPipeline(allowed_schemas=ALLOWED))
    drill = [params for sql, params in runner.calls if "_explorer_board_drill_check" in sql]
    assert drill == [[None, None]]


# --- B4: the key is part of the cache key ------------------------------------------------------------


class CountingRunner:
    def __init__(self):
        self.params: list[list] = []

    async def run(self, sql):
        raise AssertionError(sql)

    async def run_bound(self, sql, params, *, timeout_ms=None):
        self.params.append(list(params))
        await asyncio.sleep(0)
        rows = [{"supplier": "S", "buyer": params[0], "value": 1.0}]
        return SqlOk(["supplier", "buyer", "value"], ["text", "text", "numeric"], rows, False, 1)


async def test_the_key_value_is_part_of_the_cache_key(tmp_path):
    board = load_board(write(tmp_path, BUYER_TOML, "buyer"), ALLOWED)
    assert render_key(board, {}, "A") != render_key(board, {}, "B")
    runner = CountingRunner()
    renderer = BoardRenderer({"buyer": board}, runner, concurrency=2, row_cap=500, ttl_s=300, max_cache=20)
    a = await renderer.outcomes(board, {}, key="A")
    b = await renderer.outcomes(board, {}, key="B")
    again = await renderer.outcomes(board, {}, key="A")
    assert runner.params == [["A"], ["B"]]
    assert a["suppliers"].rows[0]["buyer"] == "A" and b["suppliers"].rows[0]["buyer"] == "B"
    assert again["suppliers"] is a["suppliers"]


# --- B21: a prefetch permit yields to user renders -------------------------------------------------------


async def test_a_low_priority_permit_waits_while_a_user_render_waits():
    sem = BoardSemaphore(1)
    order: list[str] = []
    await sem.acquire()  # a user render holds the only permit

    async def user():
        await sem.acquire()
        order.append("user")
        await sem.release()

    async def prefetch():
        await sem.acquire(low=True)
        order.append("prefetch")
        await sem.release()

    p = asyncio.create_task(prefetch())  # queued first
    await asyncio.sleep(0)
    u = asyncio.create_task(user())
    await asyncio.sleep(0)
    await sem.release()
    await asyncio.gather(p, u)
    assert order == ["user", "prefetch"]


async def test_a_low_priority_permit_is_granted_when_nobody_waits():
    sem = BoardSemaphore(2)
    await sem.acquire()
    await asyncio.wait_for(sem.acquire(low=True), 1)
    assert sem.used == 2


async def test_a_board_prefetch_runs_its_queries_one_at_a_time_with_the_prefetch_timeout(tmp_path):
    board = load_board(write(tmp_path, BUYER_TOML.replace(
        '[[queries]]', '[[queries]]\nname = "n"\nsql = "SELECT count(*) AS n FROM fixture.contracts WHERE buyer = :buyer"\n\n[[queries]]', 1),
        "buyer"), ALLOWED)
    running = 0
    peak = 0
    timeouts: list = []

    class Runner:
        async def run(self, sql):
            raise AssertionError(sql)

        async def run_bound(self, sql, params, *, timeout_ms=None):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            timeouts.append(timeout_ms)
            await asyncio.sleep(0.01)
            running -= 1
            return SqlOk(["supplier", "buyer", "value", "n"], ["text", "text", "numeric", "bigint"], [], False, 1)

    renderer = BoardRenderer({"buyer": board}, Runner(), concurrency=2, row_cap=500, ttl_s=300, max_cache=20)
    await renderer.prefetch(board, "A", timeout_ms=1234)
    assert peak == 1 and timeouts == [1234, 1234]
    assert set(renderer.cache.get(render_key(board, {}, "A"))) == {"n", "suppliers"}


async def test_a_low_waiter_wakes_when_a_user_waiter_takes_a_permit_and_one_is_left():
    """Refuter F3: two permits freed at once, a user waiter takes one, the low waiter the other."""
    sem = BoardSemaphore(2)
    await sem.acquire()
    await sem.acquire()
    low = asyncio.create_task(sem.acquire(low=True))
    user = asyncio.create_task(sem.acquire())
    await asyncio.sleep(0)
    await sem.release()
    await sem.release()
    await asyncio.wait_for(asyncio.gather(low, user), 1)
    assert sem.used == 2


async def test_a_cancelled_user_waiter_lets_the_low_waiter_through():
    sem = BoardSemaphore(1)
    await sem.acquire()
    low = asyncio.create_task(sem.acquire(low=True))
    user = asyncio.create_task(sem.acquire())
    await asyncio.sleep(0)
    await sem.release()
    user.cancel()
    await asyncio.gather(user, return_exceptions=True)
    await asyncio.wait_for(low, 1)
    assert sem.used == 1
