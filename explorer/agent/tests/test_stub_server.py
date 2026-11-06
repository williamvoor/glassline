"""The stub server is shaped like the real agent (ADR 0048, O5): a Workspace listing and one
Workspace, `stub`, under `/w/stub/`. No model, no database."""

import httpx

from explorer_agent.testing.stub_server import STUB_WORKSPACE, stub_app

URL = "http://127.0.0.1:10002"


async def test_stub_lists_one_workspace_and_serves_its_card():
    app = stub_app(URL, "http://127.0.0.1:5173")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL) as c:
        listed = (await c.get("/workspaces")).json()
        assert listed == [{"name": STUB_WORKSPACE, "description": listed[0]["description"], "status": "available",
                           "url": f"{URL}/w/{STUB_WORKSPACE}"}]
        card = (await c.get(f"/w/{STUB_WORKSPACE}/.well-known/agent-card.json")).json()
        assert card["url"] == f"{URL}/w/{STUB_WORKSPACE}"
        assert (await c.get("/.well-known/agent-card.json")).status_code == 404  # no agent at the root


async def test_stub_allows_the_client_origin():
    app = stub_app(URL, "http://127.0.0.1:5173")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL) as c:
        r = await c.options("/workspaces", headers={"Origin": "http://127.0.0.1:5173",
                                                    "Access-Control-Request-Method": "GET"})
        assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
