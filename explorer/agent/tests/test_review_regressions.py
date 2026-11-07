"""Maintainability review regressions, without a model or database."""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pglast import parse_sql

from explorer_agent import __main__ as main
from explorer_agent.boards import BoardError, BoardRenderer, check_board, parse_board
from explorer_agent.sql import SqlOk, SqlRunner
from explorer_agent.ui import UiPipeline

ALLOWED = ("fixture",)
BOARD = '''title = "Explorer"
layout = """root = Column([total])
total = Metric("totals", "n", "Count", format="int")"""
[[queries]]
name = "totals"
sql = "SELECT 1::bigint AS n"
'''


class Runner:
    check_privileges = AsyncMock()

    async def run(self, sql, **kwargs):
        return SqlOk(["n"], ["bigint"], [] if "LIMIT 0" in sql else [{"n": 1}], False, 1)

    async def run_bound(self, sql, values, **kwargs):
        return await self.run(sql)


@pytest.mark.parametrize("macro", [
    'Metric("totals", "n", "Count", format="typo")',
    'Metric("totals", "n", "Count", row=-1)',
    'DataTable("totals", "Count", format="n:typo")',
    'DataTable("totals", "Count", format="n")',
    'BarChart("totals", "n", "n", "Count", format="typo")',
    'BarChart("totals", "n", "n", "Count", format="text")',
    'LineChart("totals", "n", "n", "Count", format="date")',
    'Cite("Count {totals[0].n|typo}")',
])
async def test_startup_rejects_static_macro_errors(macro):
    text = BOARD.replace('Metric("totals", "n", "Count", format="int")', macro)
    board = parse_board(text, Path("boards/explorer.toml"), ALLOWED)
    with pytest.raises(BoardError, match="format|row must"):
        await check_board(board, Runner(), UiPipeline(allowed_schemas=ALLOWED))


async def test_cli_renders_resolved_number_defaults(monkeypatch, capsys):
    text = BOARD.replace('[[queries]]', '''[[controls]]
name = "minimum"
kind = "number"
label = "Minimum"
min = 0
max = 10
default = 5
[[queries]]''').replace("SELECT 1::bigint AS n", "SELECT 1::bigint AS n WHERE :minimum >= 0")
    board = parse_board(text, Path("boards/explorer.toml"), ALLOWED)
    settings = SimpleNamespace(dataset=SimpleNamespace(boards={board.name: board}, allowed_schemas=ALLOWED),
                               sql_timeout_ms=1000, sql_row_cap=20)
    pool = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(main, "_open_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(main, "SqlRunner", lambda *a: Runner())
    monkeypatch.setattr(main.UiPipeline, "from_settings", lambda _: UiPipeline(allowed_schemas=ALLOWED))
    monkeypatch.setattr(main, "_renderer", lambda s, r: BoardRenderer(s.dataset.boards, r, concurrency=2,
                        row_cap=20, ttl_s=30, max_cache=10))
    assert await main._boards_check(settings, target_s=60) == 0
    assert capsys.readouterr().out.startswith("ok explorer:")
    pool.close.assert_awaited_once()


async def test_cli_reports_a_failed_options_query_and_checks_the_other_boards(monkeypatch, capsys):
    from explorer_agent.boards import OptionsError
    first = parse_board(BOARD, Path("boards/first.toml"), ALLOWED)
    second = parse_board(BOARD, Path("boards/second.toml"), ALLOWED)
    settings = SimpleNamespace(dataset=SimpleNamespace(boards={first.name: first, second.name: second},
                                                       allowed_schemas=ALLOWED),
                               sql_timeout_ms=1000, sql_row_cap=20)
    pool = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(main, "_open_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(main, "SqlRunner", lambda *a: Runner())
    monkeypatch.setattr(main.UiPipeline, "from_settings", lambda _: UiPipeline(allowed_schemas=ALLOWED))

    class Renderer(BoardRenderer):
        async def defaults(self, board, fresh=False):
            if board.name == first.name:
                raise OptionsError("options.agency")
            return await super().defaults(board, fresh=fresh)

    monkeypatch.setattr(main, "_renderer", lambda s, r: Renderer(s.dataset.boards, r, concurrency=2,
                        row_cap=20, ttl_s=30, max_cache=10))
    assert await main._boards_check(settings, target_s=60) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(f"FAIL {first.name}:") and out[0].endswith("failed queries: options.agency")
    assert out[1].startswith(f"ok {second.name}:")
    pool.close.assert_awaited_once()


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("comment", ["-- explanation", "/* explanation */"])
async def test_empty_result_semicolon_before_comment(monkeypatch, bound, comment):
    calls = []
    class Cursor:
        description = None

        async def stream(self, sql, params, **kwargs):
            parse_sql(sql)
            if False:
                yield ()

        async def execute(self, sql, params):
            parse_sql(sql)  # catches a semicolon left inside the fallback's subquery
            calls.append((sql, params))
            self.description = [SimpleNamespace(name="n")]

    cursor = Cursor()
    conn = SimpleNamespace(execute=AsyncMock(), cursor=lambda: cursor)
    @asynccontextmanager
    async def connection():
        yield conn
    monkeypatch.setattr("explorer_agent.sql.psycopg.AsyncRawCursor", lambda _: cursor)
    monkeypatch.setattr("explorer_agent.sql._type_name", lambda *a: "bigint")
    runner = SqlRunner(SimpleNamespace(connection=connection), ALLOWED, 1000, 20)
    query = f"SELECT {'$1::bigint' if bound else '1'} AS n WHERE false; {comment}"
    out = await runner.run_bound(query, [7]) if bound else await runner.run(query)
    assert isinstance(out, SqlOk) and out.rows == [] and out.columns == ["n"]
    assert calls[0][1] == ([7] if bound else None)
