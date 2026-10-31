"""Backfill / fetch-month into raw.release, with per-month checkpoints in ops."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

from austender.api import AusTenderClient

_MONTH = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")


def canonical_sha256(payload: dict) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def month_window(month: str) -> tuple[datetime, datetime]:
    """UTC calendar month [first day, first day of next month)."""
    m = _MONTH.fullmatch(month)
    if not m:
        raise ValueError(f"month must look like YYYY-MM, got {month!r}")
    y, mo = int(m.group(1)), int(m.group(2))
    start = datetime(y, mo, 1, tzinfo=UTC)
    end = datetime(y + (mo == 12), mo % 12 + 1, 1, tzinfo=UTC)
    return start, end


def months_between(first: str, last: str) -> list[str]:
    start, _ = month_window(first)
    end, _ = month_window(last)
    out = []
    y, mo = start.year, start.month
    while (y, mo) <= (end.year, end.month):
        out.append(f"{y:04d}-{mo:02d}")
        y, mo = y + (mo == 12), mo % 12 + 1
    return out


@dataclass
class StoreResult:
    new: int = 0
    duplicate: int = 0
    rejected: int = 0


def start_run(conn: psycopg.Connection, kind: str, month: str | None = None) -> int:
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO ops.ingest_run (kind, month) VALUES (%s, %s) RETURNING id", (kind, month)
        ).fetchone()
    return row[0]


def store_releases(conn: psycopg.Connection, releases: Iterable[dict], run_id: int | None) -> StoreResult:
    """Insert releases into raw.release (append-only), in one transaction.

    A release_id already stored with the same hash is a duplicate and is skipped. With a different hash it
    is recorded in ops.rejected_release and the stored row stays unchanged.
    """
    result = StoreResult()
    first_seen: dict[str, tuple[str, dict]] = {}  # release_id -> (sha, payload), first occurrence in this batch
    rejects: list[tuple[str, dict, str, str]] = []  # (release_id, payload, existing_sha, new_sha)
    for rel in releases:
        rid = rel.get("id")
        if not rid:
            continue
        sha = canonical_sha256(rel)
        if rid in first_seen:
            if first_seen[rid][0] == sha:
                result.duplicate += 1
            else:
                rejects.append((rid, rel, first_seen[rid][0], sha))
            continue
        first_seen[rid] = (sha, rel)

    with conn.transaction():
        ids = list(first_seen)
        inserted = set()
        if ids:
            rows = conn.execute(
                "INSERT INTO raw.release (release_id, ocid, payload, payload_sha256, ingest_run_id) "
                "SELECT t.release_id, t.ocid, t.payload::jsonb, t.sha, %s "
                "FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[]) AS t(release_id, ocid, payload, sha) "
                "ON CONFLICT (release_id) DO NOTHING RETURNING release_id",
                (
                    run_id,
                    ids,
                    [first_seen[i][1].get("ocid") or "" for i in ids],
                    [json.dumps(first_seen[i][1], ensure_ascii=False) for i in ids],
                    [first_seen[i][0] for i in ids],
                ),
            ).fetchall()
            inserted = {r[0] for r in rows}
        result.new = len(inserted)
        existing_ids = [i for i in ids if i not in inserted]
        if existing_ids:
            stored = dict(
                conn.execute(
                    "SELECT release_id, payload_sha256 FROM raw.release WHERE release_id = ANY(%s)", (existing_ids,)
                ).fetchall()
            )
            for rid in existing_ids:
                sha, payload = first_seen[rid]
                if stored[rid] == sha:
                    result.duplicate += 1
                else:
                    rejects.append((rid, payload, stored[rid], sha))
        for rid, payload, existing_sha, new_sha in rejects:
            if conn.execute(
                "INSERT INTO ops.rejected_release (release_id, ocid, existing_sha256, new_sha256, payload, ingest_run_id) "
                "VALUES (%s, %s, %s, %s, %s::jsonb, %s) ON CONFLICT (release_id, new_sha256) DO NOTHING",
                (rid, payload.get("ocid"), existing_sha, new_sha, json.dumps(payload, ensure_ascii=False), run_id),
            ).rowcount:
                result.rejected += 1
    return result


@dataclass
class MonthStats:
    month: str
    pages: int = 0  # pages with releases
    requests: int = 0  # HTTP requests including the final empty page and retries
    releases_fetched: int = 0
    distinct_release_ids: int = 0
    distinct_contracts: int = 0
    new: int = 0
    duplicate: int = 0
    rejected: int = 0
    elapsed_s: float = 0.0


def fetch_month(
    conn: psycopg.Connection,
    client: AusTenderClient,
    month: str,
    *,
    log: Callable[[str], None] = lambda _m: None,
    kind: str = "fetch-month",
) -> MonthStats:
    """Fetch one UTC calendar month by contractPublished into raw. Each page is committed as it arrives,
    so an interrupted month is simply fetched again (existing releases are skipped). The checkpoint is
    written only after every page is stored."""
    start, end = month_window(month)
    stats = MonthStats(month)
    run_id = start_run(conn, kind, month)
    t0 = time.monotonic()
    req0 = client.requests_made
    release_ids: set[str] = set()
    notice_ids: set[str] = set()
    try:
        for releases in client.iter_pages("contractPublished", start, end):
            stats.pages += 1
            stats.releases_fetched += len(releases)
            for r in releases:
                release_ids.add(r.get("id"))
                contracts = r.get("contracts") or [{}]
                notice_ids.add(contracts[0].get("id"))
            res = store_releases(conn, releases, run_id)
            stats.new += res.new
            stats.duplicate += res.duplicate
            stats.rejected += res.rejected
            if stats.pages % 10 == 0:
                log(f"{month}: {stats.pages} pages, {stats.releases_fetched} releases")
        stats.distinct_release_ids = len(release_ids)
        stats.distinct_contracts = len(notice_ids)
        stats.requests = client.requests_made - req0
        stats.elapsed_s = time.monotonic() - t0
        with conn.transaction():
            conn.execute(
                "UPDATE ops.ingest_run SET status='complete', finished_at=now(), pages=%s, releases_fetched=%s, "
                "releases_new=%s, releases_dup=%s, releases_rejected=%s WHERE id=%s",
                (stats.pages, stats.releases_fetched, stats.new, stats.duplicate, stats.rejected, run_id),
            )
            conn.execute(
                "INSERT INTO ops.checkpoint (month, ingest_run_id, pages, releases_fetched) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (month) DO UPDATE SET ingest_run_id = EXCLUDED.ingest_run_id, "
                "pages = EXCLUDED.pages, releases_fetched = EXCLUDED.releases_fetched, completed_at = now()",
                (month, run_id, stats.pages, stats.releases_fetched),
            )
    except BaseException as e:
        with conn.transaction():
            conn.execute(
                "UPDATE ops.ingest_run SET status='failed', finished_at=now(), error=%s WHERE id=%s",
                (f"{type(e).__name__}: {e}"[:500], run_id),
            )
        raise
    return stats


def completed_months(conn: psycopg.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT month FROM ops.checkpoint")}


def backfill(
    conn: psycopg.Connection,
    client: AusTenderClient,
    first: str,
    last: str,
    *,
    resume: bool = False,
    log: Callable[[str], None] = print,
) -> list[MonthStats]:
    """One UTC month at a time, oldest first. With resume, months that have a checkpoint are skipped."""
    done = completed_months(conn) if resume else set()
    out = []
    for month in months_between(first, last):
        if month in done:
            log(f"{month}: already complete, skipping")
            continue
        s = fetch_month(conn, client, month, log=log, kind="backfill")
        log(
            f"{month}: {s.releases_fetched} releases ({s.new} new, {s.duplicate} dup, {s.rejected} rejected) "
            f"in {s.pages} pages, {s.elapsed_s:.0f}s"
        )
        out.append(s)
    return out
