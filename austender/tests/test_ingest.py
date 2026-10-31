from datetime import UTC, datetime

import httpx
import psycopg
import pytest

from austender import cli, db, ingest
from austender.api import AusTenderClient, ApiError


def test_month_window_and_months_between():
    assert ingest.month_window("2026-09") == (datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC))
    assert ingest.month_window("2026-12")[1] == datetime(2027, 1, 1, tzinfo=UTC)
    assert ingest.months_between("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    with pytest.raises(ValueError):
        ingest.month_window("2026-13")
    with pytest.raises(ValueError):
        ingest.month_window("2026-9")


def test_canonical_hash_ignores_key_order():
    assert ingest.canonical_sha256({"a": 1, "b": [1, 2]}) == ingest.canonical_sha256({"b": [1, 2], "a": 1})
    assert ingest.canonical_sha256({"a": 1}) != ingest.canonical_sha256({"a": 2})


def rel(i, value="1.00", ocid="o1"):
    return {
        "ocid": ocid,
        "id": f"{ocid}-{i}",
        "date": "2026-09-02T00:00:00Z",
        "tag": ["contract"],
        "contracts": [{"id": f"CN{i}", "value": {"amount": value}}],
    }


@pytest.fixture
def fresh(new_db):
    dsn = new_db()
    with psycopg.connect(dsn, autocommit=True) as c:
        db.ensure_roles(c, loader_password="x", explorer_ro_password="y")
        db.migrate(c)
        yield c


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_store_dedupes_and_rejects_changed_payloads(fresh):
    run = ingest.start_run(fresh, "test")
    r = ingest.store_releases(fresh, [rel(1), rel(2), rel(1)], run)  # in-batch duplicate
    assert (r.new, r.duplicate, r.rejected) == (2, 1, 0)

    r = ingest.store_releases(fresh, [rel(1), rel(3)], run)  # across batches
    assert (r.new, r.duplicate, r.rejected) == (1, 1, 0)

    r = ingest.store_releases(fresh, [rel(2, value="9.99")], run)  # same id, different payload
    assert (r.new, r.duplicate, r.rejected) == (0, 0, 1)
    assert count(fresh, "raw.release") == 3
    assert count(fresh, "ops.rejected_release") == 1
    (amount,) = fresh.execute("SELECT payload #>> '{contracts,0,value,amount}' FROM raw.release WHERE release_id='o1-2'").fetchone()
    assert amount == "1.00"  # stored row unchanged

    # seeing the same rejected payload again does not add another rejection
    r = ingest.store_releases(fresh, [rel(2, value="9.99")], run)
    assert r.rejected == 0 and count(fresh, "ops.rejected_release") == 1

    # a different payload twice inside one batch: second is rejected against the first
    r = ingest.store_releases(fresh, [rel(4), rel(4, value="2.00")], run)
    assert (r.new, r.rejected) == (1, 1)


def month_handler(pages):
    """pages: list of release lists. Page i links to page i+1, the last page has no next link."""
    calls = []

    def handler(req):
        calls.append(str(req.url))
        idx = int(req.url.params.get("page", 0))
        body = {"releases": pages[idx]}
        if idx + 1 < len(pages):
            body["links"] = {"next": f"https://api.tenders.gov.au/ocds/findByDates/next?page={idx + 1}"}
        return httpx.Response(200, json=body)

    return handler, calls


def make_client(handler):
    return AusTenderClient(
        user_agent="test/1.0", transport=httpx.MockTransport(handler), min_interval_s=0, sleep=lambda s: None
    )


def test_fetch_month_stores_pages_and_writes_checkpoint(fresh):
    handler, calls = month_handler([[rel(1), rel(2)], [rel(3), rel(1)], []])
    with make_client(handler) as client:
        s = ingest.fetch_month(fresh, client, "2026-09")
    assert (s.pages, s.releases_fetched, s.distinct_release_ids, s.distinct_contracts) == (2, 4, 3, 3)
    assert (s.new, s.duplicate, s.rejected) == (3, 1, 0)
    assert s.requests == 3
    assert "2026-09-01T00:00:00Z/2026-10-01T00:00:00Z" in calls[0]
    assert count(fresh, "raw.release") == 3
    assert fresh.execute("SELECT month, pages, releases_fetched FROM ops.checkpoint").fetchall() == [("2026-09", 2, 4)]
    assert fresh.execute("SELECT status FROM ops.ingest_run ORDER BY id DESC LIMIT 1").fetchone()[0] == "complete"


def test_failed_month_has_no_checkpoint_but_keeps_stored_pages(fresh):
    def handler(req):
        if req.url.params.get("page") == "1":
            return httpx.Response(403, json={})
        return httpx.Response(200, json={"releases": [rel(1)], "links": {"next": "https://api.tenders.gov.au/x?page=1"}})

    with make_client(handler) as client, pytest.raises(ApiError):
        ingest.fetch_month(fresh, client, "2026-09")
    assert count(fresh, "ops.checkpoint") == 0
    assert count(fresh, "raw.release") == 1
    assert fresh.execute("SELECT status FROM ops.ingest_run ORDER BY id DESC LIMIT 1").fetchone()[0] == "failed"


def test_backfill_resume_skips_completed_months(fresh):
    handler, calls = month_handler([[rel(1)]])
    with make_client(handler) as client:
        ingest.fetch_month(fresh, client, "2026-07")
        calls.clear()
        out = ingest.backfill(fresh, client, "2026-07", "2026-08", resume=True, log=lambda m: None)
    assert [s.month for s in out] == ["2026-08"]
    assert len(calls) == 1 and "2026-08-01T00:00:00Z/2026-09-01T00:00:00Z" in calls[0]


def test_cli_requires_user_agent(monkeypatch, capsys):
    monkeypatch.setenv("INGEST_USER_AGENT", "")
    monkeypatch.setattr(cli, "load_env_file", lambda: None)
    assert cli.main(["fetch-month", "2026-09"]) == 2
    assert "INGEST_USER_AGENT" in capsys.readouterr().err
