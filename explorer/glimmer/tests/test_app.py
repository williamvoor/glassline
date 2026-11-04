import logging
import threading
import time

import pytest

from conftest import CKPT, KEY, StubBackend, body, call
from glimmer_server import app as A


def test_surface_returns_the_backend_reply(serve):
    b = StubBackend()
    url = serve(b)
    status, out = call(url, "/surface", body(max_tokens=900, grammar="root ::= \"x\""))
    assert status == 200
    assert set(out) == {"text", "finish", "tokens", "latency_s", "usage", "checkpoint"}
    assert out["text"].startswith("<a2ui>") and out["finish"] == "stop" and out["tokens"] == 7
    assert out["checkpoint"] == "s225-2939ff3b" and out["usage"]["cached_prompt_tokens"] == 80
    (req,) = b.calls
    assert req.messages == body()["messages"] and req.max_tokens == 900
    assert req.grammar == "root ::= \"x\"" and req.cache_key == "explorer-glimmer"


def test_surface_defaults(serve):
    b = StubBackend()
    assert call(serve(b), "/surface", body())[0] == 200
    assert b.calls[0].max_tokens == 1536 and b.calls[0].grammar is None


BAD = [
    ("raw", b"{not json", "not JSON"),
    ("raw", b"[1]", "JSON object"),
    ({"messages": []}, None, "non-empty"),
    ({"messages": [{"role": "tool", "content": "x"}]}, None, "messages[0]"),
    ({"messages": [{"role": "user", "content": ["x"]}]}, None, "messages[0]"),
    ({"messages": [{"role": "system", "content": "Reasoning strength: low."}, {"role": "assistant", "content": "y"}]}, None, "last message"),
    ({"messages": [{"role": "user", "content": "x"}]}, None, "system message"),
    ({"messages": [{"role": "system", "content": "rules"}, {"role": "user", "content": "x"}]}, None, "Reasoning strength: low"),
    ({"messages": [{"role": "system", "content": "Reasoning strength: high."}, {"role": "user", "content": "x"}]}, None, "Reasoning strength: low"),
    ({"grammar": ""}, None, "grammar"),
    ({"grammar": "  \n"}, None, "grammar"),
    ({"max_tokens": 0}, None, "max_tokens"),
    ({"max_tokens": 5000}, None, "max_tokens"),
    ({"max_tokens": "100"}, None, "max_tokens"),
    ({"max_tokens": True}, None, "max_tokens"),
    ({"grammar": 3}, None, "grammar"),
    ({"cache_key": None}, None, "cache_key"),
    ({"cache_key": ""}, None, "cache_key"),
    ({"cache_key": "k" * 129}, None, "cache_key"),
]


@pytest.mark.parametrize("over, raw, match", BAD)
def test_surface_rejects_bad_requests(serve, over, raw, match):
    b = StubBackend()
    url = serve(b)
    status, out = call(url, "/surface", raw=raw) if over == "raw" else call(url, "/surface", body(**over))
    assert status == 400 and out["error"] == "bad_request" and match in out["detail"]
    assert b.calls == []


def test_surface_refuses_a_huge_body(serve):
    import http.client

    b = StubBackend()
    url = serve(b)
    host, port = url.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    conn.putrequest("POST", "/surface")
    conn.putheader("Content-Length", str(A.MAX_BODY + 1))
    conn.endheaders()  # the body is never sent: the server must answer from the header alone
    r = conn.getresponse()
    assert r.status == 413 and b"too_large" in r.read()
    assert b.calls == []


def test_backend_failure_is_a_redacted_502(serve):
    b = StubBackend(error=RuntimeError(f"401 for key {KEY} on {CKPT} " + "x" * 1000))
    url = serve(b)
    status, out = call(url, "/surface", body())
    assert status == 502 and out["error"] == "backend"
    assert KEY not in out["detail"] and CKPT not in out["detail"] and "***" in out["detail"]
    assert len(out["detail"]) <= A.DETAIL_CHARS
    assert call(url, "/health")[1]["errors"] == 1


def test_health_and_routing(serve):
    url = serve(StubBackend())
    call(url, "/surface", body())
    status, h = call(url, "/health")
    assert status == 200 and h == {"ok": True, "checkpoint": "s225-2939ff3b", "served": 1, "errors": 0}
    assert CKPT not in str(h)
    assert call(url, "/nope")[0] == 404
    assert call(url, "/nope", body())[0] == 404
    assert call(url, "/surface")[0] == 405
    assert call(url, "/health", body())[0] == 405


def test_log_has_no_prompt_text_or_secrets(serve, caplog):
    caplog.set_level(logging.INFO, logger="glimmer_server")
    url = serve(StubBackend(reply={"text": "SECRET-REPLY", "finish": "stop", "tokens": 3, "usage": {}}))
    call(url, "/surface", body(messages=[{"role": "system", "content": "Reasoning strength: low."}, {"role": "user", "content": "SECRET-PROMPT"}], grammar="SECRET-GBNF"))
    call(serve(StubBackend(error=RuntimeError(f"{KEY} {CKPT}"))), "/surface", body())
    text = caplog.text
    assert "POST /surface 200" in text and "tokens=3" in text
    for s in ("SECRET-PROMPT", "SECRET-REPLY", "SECRET-GBNF", KEY, CKPT):
        assert s not in text


def test_requests_run_concurrently(serve):
    url = serve(StubBackend(delay=0.5))
    results = []
    ts = [threading.Thread(target=lambda: results.append(call(url, "/surface", body())[0])) for _ in range(4)]
    t0 = time.monotonic()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert results == [200] * 4 and time.monotonic() - t0 < 1.5


def test_early_errors_keep_the_connection_usable_or_close_it(serve):
    """Regression: a POST answered without reading its body left the bytes on a keep-alive
    connection, and the next request on it got the standard library's HTML 400."""
    import http.client
    import json

    url = serve(StubBackend())
    host, port = url.removeprefix("http://").split(":")
    for path, payload in (("/nope", b'{"a": 1}'), ("/health", b'{"a": 1}'), ("/surface", b"{bad json")):
        conn = http.client.HTTPConnection(host, int(port), timeout=10)
        conn.request("POST", path, body=payload, headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        assert r.status in (400, 404, 405) and json.loads(r.read())["error"]
        try:
            conn.request("GET", "/health")
            r2 = conn.getresponse()
            assert r2.status == 200 and json.loads(r2.read())["ok"]
        except (ConnectionError, http.client.RemoteDisconnected, http.client.BadStatusLine):
            pass  # closing the connection is also correct
        conn.close()
