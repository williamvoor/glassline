"""A2A server wired to a canned answerer, for the client interop test and manual checks.

No database and no model: the store is seeded with the fixture result `r1`, and every
answer is `FIXED_REPLY`. Never imported by `server.py`.

    uv run python -m explorer_agent.testing.stub_server --port 10002
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import uvicorn

from explorer_agent.executor import Conversation, StatusFn
from explorer_agent.results import ResultStore
from explorer_agent.server import build_app
from explorer_agent.ui import UiPipeline, UiResult

FIXTURE_PURPOSE = "top suppliers (fixture)"
FIXTURE_SQL = (
    "SELECT supplier, count(*) AS contracts, sum(value) AS value\n"
    "FROM fixture.contracts\nGROUP BY supplier\nORDER BY value DESC\nLIMIT 3"
)
STATUS_TEXT = f"Running SQL: {FIXTURE_PURPOSE}"

FIXED_REPLY = """**{r1[0].supplier}** received the most, {r1[0].value|currency_m} in total.
<a2ui>
surface("main")
root = Column([top, chart, table])
top = Metric("r1", "value", "Top supplier value", 0, "currency_m")
chart = BarChart("r1", "supplier", "value", "Value by supplier", _, false, "currency_m")
table = DataTable("r1", "Suppliers", "supplier,contracts,value", "value:currency")
</a2ui>"""


def seed_fixture(store: ResultStore) -> None:
    store.add(
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
    )


_PIPELINE: UiPipeline | None = None


def _pipeline() -> UiPipeline:
    global _PIPELINE
    if _PIPELINE is None:
        _PIPELINE = UiPipeline()
    return _PIPELINE


async def stub_answerer(conv: Conversation, text: str, status: StatusFn) -> UiResult:
    if not conv.store.ids():
        seed_fixture(conv.store)
    await status(STATUS_TEXT)
    return _pipeline().build(FIXED_REPLY, conv.store, conv.surface_id)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--client-origin", default="http://127.0.0.1:5173")
    args = parser.parse_args(argv)

    url = f"http://{args.host}:{args.port}"
    app = build_app(
        stub_answerer, url=url, client_origin=args.client_origin,
        description="Stub agent: every answer is the same fixture", examples=("Which suppliers received the most?",),
    )
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning"))

    async def serve() -> None:
        task = asyncio.create_task(server.serve())
        while not server.started:
            if task.done():
                await task  # surfaces the startup error
                return
            await asyncio.sleep(0.05)
        print(f"READY {url}", flush=True)
        await task

    asyncio.run(serve())
    sys.exit(0 if server.started else 1)


if __name__ == "__main__":
    main()
