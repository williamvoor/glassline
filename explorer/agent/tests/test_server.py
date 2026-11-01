import json
import re
import uuid

import httpx
import pytest

from explorer_agent.catalog import EXPLORER_CATALOG_ID
from explorer_agent.executor import GENERIC_ERROR, UI_ERROR_PREFIX, UNKNOWN_CONTEXT_PROSE, Conversations
from explorer_agent.server import build_app
from explorer_agent.testing.stub_server import STATUS_TEXT, stub_answerer
from explorer_agent.ui import UiPipeline, UiResult

URL = "http://agent.test"
ORIGIN = "http://127.0.0.1:5173"
EXT_URI = "https://a2ui.org/a2a-extension/a2ui/v0.9"
A2UI_MIME_TYPES = {"application/a2ui+json", "application/json+a2ui"}
CAPS = {"a2uiClientCapabilities": {"v0.9": {"supportedCatalogIds": [EXPLORER_CATALOG_ID]}}}


def client_for(answerer=stub_answerer) -> httpx.AsyncClient:
    app = build_app(answerer, url=URL, client_origin=ORIGIN)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL)


async def stream(client, text="top suppliers?", *, context_id=None, metadata=CAPS, header=True) -> list[dict]:
    message = {
        "kind": "message", "role": "user", "messageId": str(uuid.uuid4()),
        "parts": [{"kind": "text", "text": text}], "metadata": metadata,
    }
    if context_id:
        message["contextId"] = context_id
    body = {"jsonrpc": "2.0", "id": 1, "method": "message/stream", "params": {"message": message}}
    headers = {"X-A2A-Extensions": EXT_URI} if header else {}
    events = []
    async with client.stream("POST", "/", json=body, headers=headers) as r:
        assert r.status_code == 200
        async for line in r.aiter_lines():
            if line.startswith("data:"):
                payload = json.loads(line[5:])
                assert "error" not in payload, payload
                events.append(payload["result"])
    return events


def final_parts(events: list[dict]) -> list[dict]:
    final = events[-1]
    assert final["kind"] == "status-update" and final["final"] is True
    assert final["status"]["state"] == "completed"
    return final["status"]["message"]["parts"]


def a2ui_messages(parts: list[dict]) -> list[dict]:
    return [p["data"] for p in parts if p["kind"] == "data" and p.get("metadata", {}).get("mimeType") in A2UI_MIME_TYPES]


def surface_ids(messages: list[dict]) -> set[str]:
    return {b["surfaceId"] for m in messages for b in m.values() if isinstance(b, dict) and "surfaceId" in b}


def turn_surface(messages: list[dict], n: int) -> str:
    """The one surface ID in `messages`, checked to be Turn `n`'s (`turn-<n>-<tag>`)."""
    (sid,) = surface_ids(messages)
    assert re.fullmatch(rf"turn-{n}-[0-9a-f]{{6}}", sid), sid
    return sid


async def test_agent_card():
    async with client_for() as c:
        card = (await c.get("/.well-known/agent-card.json")).json()
    assert card["capabilities"]["streaming"] is True
    ext = card["capabilities"]["extensions"]
    assert [e["uri"] for e in ext] == [EXT_URI]
    assert ext[0]["params"]["supportedCatalogIds"] == [EXPLORER_CATALOG_ID]


async def test_stream_returns_status_then_prose_and_a2ui():
    async with client_for() as c:
        events = await stream(c)
    statuses = [
        p["text"] for e in events if e["kind"] == "status-update" and e["status"]["state"] == "working"
        and e["status"].get("message") for p in e["status"]["message"]["parts"]
    ]
    assert STATUS_TEXT in statuses
    parts = final_parts(events)
    assert parts[0]["kind"] == "text"
    assert "Supplier A" in parts[0]["text"] and "{r1" not in parts[0]["text"]
    assert all(p["kind"] == "data" for p in parts[1:])
    msgs = a2ui_messages(parts)
    assert len(msgs) == len(parts) - 1 > 0
    turn_surface(msgs, 1)


async def test_second_turn_gets_its_own_surface():
    async with client_for() as c:
        first = await stream(c)
        context_id = first[-1]["contextId"]
        second = await stream(c, "again", context_id=context_id)
    assert second[-1]["contextId"] == context_id
    one = turn_surface(a2ui_messages(final_parts(first)), 1)
    two = turn_surface(a2ui_messages(final_parts(second)), 2)
    assert one.split("-")[-1] == two.split("-")[-1]  # one tag per Conversation


async def test_surface_ids_do_not_repeat_after_a_restart():
    """The client keeps its contextId and surfaces across an agent restart. The restarted agent's
    turn-1 surface must not reuse an ID the client already holds, or the client rejects it."""
    async with client_for() as c:
        first = await stream(c)
    context_id = first[-1]["contextId"]
    async with client_for() as c:  # a fresh app: the restart
        after = await stream(c, "again", context_id=context_id)
    before_ids = surface_ids(a2ui_messages(final_parts(first)))
    after_ids = surface_ids(a2ui_messages(final_parts(after)))
    assert before_ids and after_ids and before_ids.isdisjoint(after_ids)


@pytest.mark.parametrize(
    ("header", "metadata"),
    [
        (False, CAPS),
        (True, {"a2uiClientCapabilities": {"v0.9": {"supportedCatalogIds": ["urn:other"]}}}),
        (True, None),
        (True, {"a2uiClientCapabilities": "garbage"}),
    ],
    ids=["no-header", "other-catalog", "no-metadata", "malformed"],
)
async def test_without_a2ui_support_prose_only(header, metadata):
    async with client_for() as c:
        parts = final_parts(await stream(c, header=header, metadata=metadata))
    assert [p["kind"] for p in parts] == ["text"]
    assert "Supplier A" in parts[0]["text"]


async def test_ui_error_completes_with_explanation():
    async def bad(conv, text, status):
        reply = '<a2ui>\nroot = Column([c])\nc = BarChart("r9", "x", "y", "T")\n</a2ui>'
        return UiPipeline().build(reply, conv.store, conv.surface_id)

    async with client_for(bad) as c:
        parts = final_parts(await stream(c))
    assert [p["kind"] for p in parts] == ["text"]
    assert parts[0]["text"].startswith(UI_ERROR_PREFIX)
    assert "r9" in parts[0]["text"]


async def test_answerer_exception_does_not_kill_server():
    calls = []

    async def flaky(conv, text, status):
        calls.append(text)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return UiResult(prose="fine", messages=[], bare_numbers=[])

    async with client_for(flaky) as c:
        first = final_parts(await stream(c))
        second = final_parts(await stream(c))
    assert first == [{"kind": "text", "text": GENERIC_ERROR}]
    assert second[0]["text"] == "fine"


async def test_cors_preflight():
    async with client_for() as c:
        r = await c.options("/", headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-a2a-extensions",
        })
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert "x-a2a-extensions" in r.headers["access-control-allow-headers"].lower()


# E1
async def test_unknown_context_note():
    seen = []

    async def answer(conv, text, status):
        seen.append(conv.unknown_context)
        return UiResult(prose="answer", messages=[], bare_numbers=[])

    async with client_for(answer) as c:
        fresh = final_parts(await stream(c))  # no contextId: a new conversation
        ctx = (await stream(c))[-1]["contextId"]
        known = final_parts(await stream(c, context_id=ctx))
        unknown = final_parts(await stream(c, context_id="from-before-a-restart"))
        again = final_parts(await stream(c, context_id="from-before-a-restart"))
    assert fresh[0]["text"] == "answer" and known[0]["text"] == "answer"
    assert unknown[0]["text"] == f"{UNKNOWN_CONTEXT_PROSE}\n\nanswer"
    assert again[0]["text"] == "answer"  # only the first Turn after the gap gets the note
    assert seen == [False, False, False, True, False]


# E2
async def test_conversation_lru():
    async def answer(conv, text, status):
        return UiResult(prose="answer", messages=[], bare_numbers=[])

    app = build_app(answer, url=URL, client_origin=ORIGIN, conversations=Conversations(max_conversations=2))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL) as c:
        a = (await stream(c))[-1]["contextId"]
        b = (await stream(c))[-1]["contextId"]
        assert final_parts(await stream(c, context_id=a))[0]["text"] == "answer"  # a is now most recent
        await stream(c)  # a third conversation evicts b, the least recently used
        assert final_parts(await stream(c, context_id=a))[0]["text"] == "answer"
        assert final_parts(await stream(c, context_id=b))[0]["text"].startswith(UNKNOWN_CONTEXT_PROSE)


# E4
async def test_same_context_is_serialised():
    import asyncio

    log = []

    async def answer(conv, text, status):
        log.append(("start", text))
        await asyncio.sleep(0.1)
        log.append(("end", text))
        return UiResult(prose=text, messages=[], bare_numbers=[])

    async with client_for(answer) as c:
        ctx = (await stream(c, "first"))[-1]["contextId"]
        log.clear()
        await asyncio.gather(stream(c, "a", context_id=ctx), stream(c, "b", context_id=ctx))
    assert [kind for kind, _ in log] == ["start", "end", "start", "end"]


# E3: the real loop behind A2A, with the stub model and the seed database
@pytest.mark.db
async def test_stream_with_llm_loop(ro_pool):
    from explorer_agent.llm import LlmLoop, LoopConfig
    from explorer_agent.schema import SchemaCatalog
    from explorer_agent.sql import SqlRunner
    from explorer_agent.testing.stub_model import ScriptedCompletion, text, tool_calls

    sql = "SELECT supplier_name, total_value FROM mart.supplier_fy WHERE financial_year = '2024-25' ORDER BY 2 DESC LIMIT 3"
    reply = """**{r1[0].supplier_name}** leads.
<a2ui>
surface("main")
root = Column([chart])
chart = BarChart("r1", "supplier_name", "total_value", "Top suppliers", _, false, "currency_m")
</a2ui>"""
    model = ScriptedCompletion([tool_calls(("run_sql", {"sql": sql, "purpose": "top suppliers"})), text(reply)])
    loop = LlmLoop(LoopConfig(), "SYSTEM", UiPipeline(), SqlRunner(ro_pool, ("mart",), 5000, 100),
                   SchemaCatalog(ro_pool, ["mart"]), completion=model)
    async with client_for(loop) as c:
        events = await stream(c)
    statuses = [e["status"]["message"]["parts"][0]["text"] for e in events
                if e.get("kind") == "status-update" and e["status"]["state"] == "working" and e["status"].get("message")]
    assert "Running SQL: top suppliers" in statuses
    parts = final_parts(events)
    assert parts[0]["kind"] == "text" and parts[0]["text"].startswith("**")
    turn_surface(a2ui_messages(parts), 1)
