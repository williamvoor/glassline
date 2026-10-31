from datetime import UTC, datetime

import httpx
import pytest

from austender.api import ApiError, AusTenderClient

START = datetime(2026, 9, 1, tzinfo=UTC)
END = datetime(2026, 10, 1, tzinfo=UTC)


class FakeTime:
    """A clock whose sleep advances it, so spacing and backoff are testable without waiting."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


def make_client(handler, ft=None, **kw):
    ft = ft or FakeTime()
    client = AusTenderClient(
        user_agent="test-agent/1.0",
        transport=httpx.MockTransport(handler),
        sleep=ft.sleep,
        monotonic=ft.monotonic,
        **kw,
    )
    return client, ft


def rel(i):
    return {"id": f"r{i}", "ocid": "o"}


def test_user_agent_required():
    for ua in (None, "", "   "):
        with pytest.raises(ValueError, match="INGEST_USER_AGENT"):
            AusTenderClient(user_agent=ua)


def test_user_agent_header_and_url():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"releases": []})

    client, _ = make_client(handler)
    assert list(client.iter_pages("contractPublished", START, END)) == []
    assert seen[0].headers["user-agent"] == "test-agent/1.0"
    assert str(seen[0].url) == (
        "https://api.tenders.gov.au/ocds/findByDates/contractPublished/2026-09-01T00:00:00Z/2026-10-01T00:00:00Z"
    )


def test_follows_next_links_until_absent():
    pages = {
        "/ocds/findByDates/contractPublished/2026-09-01T00:00:00Z/2026-10-01T00:00:00Z": {
            "releases": [rel(1), rel(2)],
            "links": {"next": "https://api.tenders.gov.au/ocds/page2?cursor=abc"},
        },
        "/ocds/page2": {"releases": [rel(3)]},
    }

    def handler(req):
        return httpx.Response(200, json=pages[req.url.path])

    client, _ = make_client(handler)
    got = list(client.iter_pages("contractPublished", START, END))
    assert [[r["id"] for r in p] for p in got] == [["r1", "r2"], ["r3"]]


def test_short_page_with_next_then_empty_page():
    """A short page can still carry links.next; the following page is empty. Both requests happen and
    iteration ends on the empty page, not on the short one."""
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path.endswith("2026-10-01T00:00:00Z"):
            return httpx.Response(200, json={"releases": [rel(1)], "links": {"next": "https://api.tenders.gov.au/x/p2"}})
        return httpx.Response(200, json={"releases": [], "links": {"next": "https://api.tenders.gov.au/x/p3"}})

    client, _ = make_client(handler)
    got = list(client.iter_pages("contractPublished", START, END))
    assert len(got) == 1
    assert len(calls) == 2  # stopped at the empty page even though it had a next link


def test_retry_with_doubling_backoff_then_success():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] == 1:
            raise httpx.ConnectError("boom")
        if n["i"] == 2:
            return httpx.Response(429)
        if n["i"] == 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"releases": []})

    client, ft = make_client(handler, min_interval_s=0)
    list(client.iter_pages("contractPublished", START, END))
    assert n["i"] == 4
    assert ft.sleeps == [5, 10, 20]


def test_gives_up_after_five_attempts():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(500)

    client, ft = make_client(handler, min_interval_s=0)
    with pytest.raises(ApiError, match="5 attempts"):
        list(client.iter_pages("contractPublished", START, END))
    assert n["i"] == 5
    assert ft.sleeps == [5, 10, 20, 40]


def test_truncated_body_is_retried():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(200, content=b'{"releases": [{"id":')  # cut off mid-body
        return httpx.Response(200, json={"releases": []})

    client, ft = make_client(handler, min_interval_s=0)
    list(client.iter_pages("contractPublished", START, END))
    assert n["i"] == 2
    assert ft.sleeps == [5]


def test_connection_dropped_mid_body_is_retried():
    n = {"i": 0}

    class Dropping(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"releases": ['
            raise httpx.ReadError("connection reset")

    def handler(req):
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(200, stream=Dropping())
        return httpx.Response(200, json={"releases": []})

    client, _ = make_client(handler, min_interval_s=0)
    list(client.iter_pages("contractPublished", START, END))
    assert n["i"] == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_no_retry_on_other_4xx(status):
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(status, json={"message": "nope"})

    client, ft = make_client(handler)
    with pytest.raises(ApiError):
        list(client.iter_pages("contractPublished", START, END))
    assert n["i"] == 1
    assert ft.sleeps == []


def test_find_by_id_not_found_is_none():
    def handler(req):
        return httpx.Response(400, json={"errorCode": 100, "message": "No Records found having CNID: CN1"})

    client, _ = make_client(handler)
    assert client.find_by_id("CN1") is None


def test_find_by_id_other_400_raises_and_ok_returns_body():
    client, _ = make_client(lambda req: httpx.Response(400, json={"errorCode": 7}))
    with pytest.raises(ApiError):
        client.find_by_id("CN1")
    client, _ = make_client(lambda req: httpx.Response(200, json={"releases": [rel(1)]}))
    assert client.find_by_id("CN1") == {"releases": [rel(1)]}


def test_requests_are_spaced_at_least_min_interval():
    times = []
    ft = FakeTime()

    def handler(req):
        times.append(ft.now)
        n = len(times)
        body = {"releases": [rel(n)]}
        if n < 3:
            body["links"] = {"next": f"https://api.tenders.gov.au/x/{n}"}
        return httpx.Response(200, json=body)

    client, _ = make_client(handler, ft, min_interval_s=1.0)
    list(client.iter_pages("contractPublished", START, END))
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert len(times) == 3
    assert all(g >= 1.0 for g in gaps)
