"""A2A server wired to a canned answerer, for tests only (the client interop test).

Shaped like the real agent (ADR 0048): `GET /workspaces` lists one Workspace, `stub`, whose agent
is at `/w/stub/`. No database and no model: the store is seeded with the fixture result `r1`, and
every answer is `FIXED_REPLY`, with a Drill query on the chart and a Follow-up (SPEC §15).
Drill-downs run against `StubDrillRunner`'s canned rows, and the Explorer Board (SPEC §18) against
`StubBoardRunner`'s. Never imported by `server.py`. Prints `READY <the Workspace's URL>`.

    uv run python -m explorer_agent.testing.stub_server --port 10002
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from explorer_agent.boards import Board, BoardQuery, BoardRenderer, Control
from explorer_agent.drill import Prefetcher, rewrite
from explorer_agent.executor import BoardViews, Conversation, DrillDowns, StatusFn
from explorer_agent.results import ResultStore
from explorer_agent.server import build_app
from explorer_agent.sql import SqlOk
from explorer_agent.ui import UiPipeline, UiResult
from explorer_agent.workspaces import AVAILABLE, PREFIX

FIXTURE_PURPOSE = "top suppliers (fixture)"
FIXTURE_SQL = (
    "SELECT supplier, count(*) AS contracts, sum(value) AS value\n"
    "FROM fixture.contracts\nGROUP BY supplier\nORDER BY value DESC\nLIMIT 3"
)
STATUS_TEXT = f"Running SQL: {FIXTURE_PURPOSE}"

FIXED_REPLY = """**{r1[0].supplier}** received the most, {r1[0].value|currency_m} in total.
<a2ui>
surface("main")
root = Column([top, chart, table, more])
top = Metric("r1", "value", "Top supplier value", 0, "currency_m")
chart = BarChart("r1", "supplier", "value", "Value by supplier", _, false, "currency_m", drill_sql="SELECT buyer, count(*) AS contracts FROM fixture.contracts WHERE supplier IS NOT DISTINCT FROM :supplier GROUP BY buyer", drill_as="bar", drill_x="buyer", drill_y="contracts", drill_title="Contracts by buyer")
table = DataTable("r1", "Suppliers", "supplier,contracts,value", "value:currency")
more = FollowUp("Show by year", "Show the value by year")
</a2ui>"""


def seed_fixture(store: ResultStore) -> str:
    """Adds the fixture Result and returns its ID (r1 unless a Board took IDs first, SPEC §18)."""
    return store.add(
        sql=FIXTURE_SQL,
        purpose=FIXTURE_PURPOSE,
        columns=["supplier", "contracts", "value"],
        column_types=["text", "bigint", "numeric"],
        rows=[
            {"supplier": "Supplier A", "contracts": 37, "value": 3_441_812_603.0},
            {"supplier": "Supplier B", "contracts": 12, "value": 812_400_000.0},
            {"supplier": "Supplier C", "contracts": 5, "value": 96_250_000.5},
        ],
        truncated=False,
    ).id


FIXTURE_SCHEMAS = ("fixture",)
_PIPELINE: UiPipeline | None = None


def _pipeline() -> UiPipeline:
    global _PIPELINE
    if _PIPELINE is None:
        _PIPELINE = UiPipeline(allowed_schemas=FIXTURE_SCHEMAS, entity={"buyer": "buyer"}, key_types={"buyer": "text"})
    return _PIPELINE


async def stub_answerer(conv: Conversation, text: str, status: StatusFn) -> UiResult:
    # The fixture by its purpose: a Board and its Drill-downs take Result IDs first (SPEC §18).
    found = [i for i in conv.store.ids() if conv.store.get(i).purpose == FIXTURE_PURPOSE]  # type: ignore[union-attr]
    rid = found[0] if found else seed_fixture(conv.store)
    conv.actions.take_pairs()  # the tool loop would send them to the model
    await status(STATUS_TEXT)
    return _pipeline().build(FIXED_REPLY.replace("r1", rid), conv.results, conv.surface_id, conv.actions.ids)


class StubDrillRunner:
    """Canned Drill-down rows: two buyers per supplier."""

    async def run_bound(self, sql: str, params: list, *, timeout_ms: int | None = None) -> SqlOk:
        name = str(params[0])
        return SqlOk(
            columns=["buyer", "contracts"], column_types=["text", "bigint"],
            rows=[{"buyer": f"{name} buyer 1", "contracts": 4}, {"buyer": f"{name} buyer 2", "contracts": 2}],
            truncated=False, elapsed_ms=1,
        )


def stub_drills(prefetch_rows: int = 3) -> DrillDowns:
    runner = StubDrillRunner()
    return DrillDowns(runner, _pipeline(), Prefetcher(runner, rows=prefetch_rows, timeout_ms=3000, max_held=30))


# The Explorer's table drills into the same canned rows as the fixture answer (StubDrillRunner).
STUB_BOARD_DRILL = (
    "SELECT buyer, count(*) AS contracts FROM fixture.contracts WHERE supplier IS NOT DISTINCT FROM :supplier GROUP BY buyer"
)
# The Explorer's one Control (SPEC §18 slice 2): a buyer filter on the trend, with "All buyers".
STUB_OPTIONS_SQL = "SELECT DISTINCT buyer AS value, buyer AS label FROM fixture.contracts ORDER BY 1"
STUB_BUYER = Control(name="buyer", kind="choice", label="Buyer", type="text", default="all",
                     options_sql=STUB_OPTIONS_SQL, all_label="All buyers")
STUB_BY_YEAR = (
    "SELECT year, sum(value) AS value FROM fixture.contracts WHERE (:buyer IS NULL OR buyer = :buyer) "
    "GROUP BY year ORDER BY year"
)
STUB_BUYERS = tuple(f"Buyer {c}" for c in "ABCDE")
STUB_BOARD = Board(
    name="explorer",
    path=Path("<stub>/boards/explorer.toml"),
    title="Explorer",
    question="Where does the money go?",
    scope="All fixture contracts.",
    queries=(
        BoardQuery("totals", "SELECT count(*) AS contracts, sum(value) AS value FROM fixture.contracts"),
        BoardQuery("by_buyer", "SELECT buyer, sum(value) AS value FROM fixture.contracts GROUP BY buyer"),
        BoardQuery("by_year", STUB_BY_YEAR, rewrite(STUB_BY_YEAR, {"buyer": "text"}).sql, ("buyer",)),
        BoardQuery("top_suppliers", FIXTURE_SQL),
    ),
    layout=f"""root = Column([metrics, Row([tiles, years]), suppliers])
metrics = Row([total, contracts])
total = Metric("totals", "value", "Total value", 0, "currency_compact")
contracts = Metric("totals", "contracts", "Contracts", 0, "int")
tiles = Treemap("by_buyer", "Value by buyer", "buyer", "value", "currency_compact")
years = LineChart("by_year", "year", "value", "Value by year", format="currency_compact")
suppliers = DataTable("top_suppliers", "Top suppliers", "supplier,contracts,value", "value:currency_compact", bars="value", drill_sql="{STUB_BOARD_DRILL}", drill_title="Contracts by buyer")""",
    controls=(STUB_BUYER,),
)

STUB_BOARD_ROWS: dict[str, SqlOk] = {
    "totals": SqlOk(["contracts", "value"], ["bigint", "numeric"],
                    [{"contracts": 54, "value": 4_350_462_603.5}], False, 1),
    "by_buyer": SqlOk(["buyer", "value"], ["text", "numeric"], [
        {"buyer": f"Buyer {c}", "value": v} for c, v in zip("ABCDE", (2.1e9, 1.2e9, 6.0e8, 3.5e8, 1.0e8), strict=True)
    ], False, 1),
    "by_year": SqlOk(["year", "value"], ["text", "numeric"], [
        {"year": y, "value": v} for y, v in (("2022", 1.1e9), ("2023", 1.6e9), ("2024", 1.65e9))
    ], False, 1),
    "top_suppliers": SqlOk(["supplier", "contracts", "value"], ["text", "bigint", "numeric"], [
        {"supplier": "Supplier A", "contracts": 37, "value": 3_441_812_603.0},
        {"supplier": "Supplier B", "contracts": 12, "value": 812_400_000.0},
        {"supplier": "Supplier C", "contracts": 5, "value": 96_250_000.5},
    ], False, 1),
}


# The buyer Entity Board (SPEC §18 slice 3): opened by any row with a `buyer` column (the
# Explorer's treemap, the Drill-down rows). The fixture answer has no `buyer` column.
STUB_ENTITY_SUMMARY = "SELECT count(*) AS contracts, sum(value) AS value FROM fixture.contracts WHERE buyer = :buyer"
STUB_ENTITY_CONTRACTS = (
    "SELECT contract, supplier, start_date, end_date, value FROM fixture.contracts WHERE buyer = :buyer "
    "ORDER BY start_date"
)
STUB_ENTITY_SUPPLIERS = (
    "SELECT supplier, count(*) AS contracts, sum(value) AS value FROM fixture.contracts WHERE buyer = :buyer "
    "GROUP BY supplier ORDER BY value DESC"
)


def _keyed(name: str, sql: str) -> BoardQuery:
    return BoardQuery(name, sql, rewrite(sql, {"buyer": "text"}).sql, ("buyer",))


STUB_ENTITY = Board(
    name="buyer",
    path=Path("<stub>/boards/buyer.toml"),
    title="Buyer",
    question="What does this buyer buy, and from whom?",
    scope="All of the buyer's fixture contracts, not only the clicked answer's.",
    queries=(
        _keyed("summary", STUB_ENTITY_SUMMARY),
        _keyed("contracts", STUB_ENTITY_CONTRACTS),
        _keyed("suppliers", STUB_ENTITY_SUPPLIERS),
    ),
    layout="""root = Column([metrics, timeline, suppliers])
metrics = Row([total, count])
total = Metric("summary", "value", "Total value", 0, "currency_compact")
count = Metric("summary", "contracts", "Contracts", 0, "int")
timeline = Timeline("contracts", "Contracts over time", "contract", "start_date", "end_date", value="value", format="currency_compact", horizon_months=6)
suppliers = DataTable("suppliers", "Suppliers", "supplier,contracts,value", "value:currency_compact", bars="value")""",
    key="buyer",
    key_type="text",
    key_columns=("buyer",),
)


def stub_entity_rows(buyer: str) -> dict[str, SqlOk]:
    """Canned rows for one buyer: a fifth of the money per letter from A, as on the Explorer."""
    share = (len(STUB_BUYERS) - STUB_BUYERS.index(buyer)) / 15 if buyer in STUB_BUYERS else 0.05
    contracts = [
        {"contract": f"{buyer} · CN{n}", "supplier": f"Supplier {s}", "start_date": start, "end_date": end,
         "value": round(v * share, 2)}
        for n, (s, start, end, v) in enumerate([
            ("A", "2023-07-01", "2026-06-30", 1.2e9), ("B", "2024-01-15", "2025-12-31", 4.0e8),
            ("A", "2025-03-01", "2027-02-28", 9.0e8), ("C", "2025-09-01", "2026-03-31", 6.0e7),
        ], start=1)
    ]
    total = sum(c["value"] for c in contracts)
    by_supplier: dict[str, list[float]] = {}
    for c in contracts:
        by_supplier.setdefault(c["supplier"], []).append(c["value"])
    return {
        "summary": SqlOk(["contracts", "value"], ["bigint", "numeric"], [{"contracts": len(contracts), "value": total}], False, 1),
        "contracts": SqlOk(["contract", "supplier", "start_date", "end_date", "value"], ["text", "text", "date", "date", "numeric"],
                           contracts, False, 1),
        "suppliers": SqlOk(["supplier", "contracts", "value"], ["text", "bigint", "numeric"], sorted(
            ({"supplier": k, "contracts": len(v), "value": sum(v)} for k, v in by_supplier.items()),
            key=lambda r: -r["value"]), False, 1),
    }


class StubBoardRunner:
    """Canned rows for the stub Explorer Board's queries, found by their SQL. With a buyer
    chosen, the trend is that buyer's share (a fifth per letter from A), so a change shows."""

    async def run(self, sql: str) -> SqlOk:
        if STUB_OPTIONS_SQL in sql:
            return SqlOk(["value", "label"], ["text", "text"], [{"value": b, "label": b} for b in STUB_BUYERS], False, 1)
        for q in STUB_BOARD.queries:
            if q.runs in sql:
                return STUB_BOARD_ROWS[q.name]
        raise ValueError("not a stub Board query")

    async def run_bound(self, sql: str, params: list, *, timeout_ms: int | None = None) -> SqlOk:
        for q in STUB_ENTITY.queries:
            if q.runs in sql:
                return stub_entity_rows(str(params[0]))[q.name]
        rows = await self.run(sql)
        buyer = params[0] if params else None
        if buyer is None or buyer not in STUB_BUYERS:
            return rows
        share = (len(STUB_BUYERS) - STUB_BUYERS.index(buyer)) / 15
        return SqlOk(rows.columns, rows.column_types, [{**r, "value": r["value"] * share} for r in rows.rows], False, 1)


def stub_boards() -> BoardViews:
    boards = {"explorer": STUB_BOARD, "buyer": STUB_ENTITY}
    renderer = BoardRenderer(boards, StubBoardRunner(), concurrency=2, row_cap=500, ttl_s=300, max_cache=20)
    return BoardViews(renderer, _pipeline())


STUB_WORKSPACE = "stub"
STUB_DESCRIPTION = "Stub agent: every answer is the same fixture"


class _StubRouter:
    """The Workspace router's shape (workspaces.Registry) with one fixed Workspace."""

    def __init__(self, agent: ASGIApp, base_url: str):
        self.agent = agent
        self.listing = [{"name": STUB_WORKSPACE, "description": STUB_DESCRIPTION, "status": AVAILABLE,
                         "url": f"{base_url}{PREFIX}{STUB_WORKSPACE}"}]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while (await receive())["type"] != "lifespan.shutdown":
                await send({"type": "lifespan.startup.complete"})
            await send({"type": "lifespan.shutdown.complete"})
            return
        path, prefix = scope["path"], f"{PREFIX}{STUB_WORKSPACE}"
        if path == "/workspaces" and scope.get("method") == "GET":
            await JSONResponse(self.listing)(scope, receive, send)
        elif path == prefix or path.startswith(prefix + "/"):
            child = dict(scope)
            child["root_path"] = scope.get("root_path", "") + prefix
            child["path"] = path[len(prefix):] or "/"
            child["raw_path"] = child["path"].encode()
            await self.agent(child, receive, send)
        else:
            await JSONResponse({"error": "not found"}, status_code=404)(scope, receive, send)


def stub_app(url: str, client_origin: str, *, boards: bool = True) -> ASGIApp:
    agent = build_app(
        stub_answerer, url=f"{url}{PREFIX}{STUB_WORKSPACE}", client_origin=client_origin, cors=False,
        description=STUB_DESCRIPTION, examples=("Which suppliers received the most?",),
        drills=stub_drills(), model="stub", boards=stub_boards() if boards else None,
    )
    return CORSMiddleware(_StubRouter(agent, url), allow_origins=[client_origin], allow_methods=["*"],
                          allow_headers=["*"])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--client-origin", default="http://127.0.0.1:5173")
    parser.add_argument("--no-boards", action="store_true", help="answer open_board with explorerNoBoards")
    args = parser.parse_args(argv)

    url = f"http://{args.host}:{args.port}"
    app = stub_app(url, args.client_origin, boards=not args.no_boards)
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning"))

    async def serve() -> None:
        task = asyncio.create_task(server.serve())
        while not server.started:
            if task.done():
                await task  # surfaces the startup error
                return
            await asyncio.sleep(0.05)
        print(f"READY {url}{PREFIX}{STUB_WORKSPACE}", flush=True)
        await task

    asyncio.run(serve())
    sys.exit(0 if server.started else 1)


if __name__ == "__main__":
    main()
