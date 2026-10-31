"""AusTender OCDS client: pagination, retries, rate limit. Synchronous, one request at a time."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import httpx

BASE_URL = "https://api.tenders.gov.au/ocds"


class ApiError(Exception):
    """A request failed for good (retries exhausted, or a non-retryable status)."""


class _Retryable(Exception):
    pass


def iso_z(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


class AusTenderClient:
    def __init__(
        self,
        *,
        user_agent: str | None,
        base_url: str = BASE_URL,
        min_interval_s: float = 1.0,
        backoff_base_s: float = 5.0,
        max_attempts: int = 5,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        timeout_s: float = 60.0,
    ) -> None:
        if not user_agent or not user_agent.strip():
            raise ValueError(
                "INGEST_USER_AGENT is not set. Use a descriptive value with a contact address, "
                "e.g. 'genUI-austender/0.1 (research; you@example.org)'."
            )
        self._base = base_url.rstrip("/")
        self._min_interval = min_interval_s
        self._backoff_base = backoff_base_s
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_done: float | None = None
        self.requests_made = 0
        self._http = httpx.Client(
            transport=transport,
            headers={"User-Agent": user_agent.strip(), "Accept": "application/json"},
            timeout=timeout_s,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> AusTenderClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _throttle(self) -> None:
        if self._last_done is None:
            return
        wait = self._min_interval - (self._monotonic() - self._last_done)
        if wait > 0:
            self._sleep(wait)

    def _get(self, url: str) -> tuple[int, Any]:
        """GET with retries. Returns (status, parsed JSON or None) for 2xx and non-retryable 4xx.

        The body is read and parsed inside the retried unit, so a connection dropped mid-body is retried.
        """
        for attempt in range(1, self._max_attempts + 1):
            self._throttle()
            try:
                self.requests_made += 1
                try:
                    resp = self._http.get(url)
                finally:
                    self._last_done = self._monotonic()
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise _Retryable(f"HTTP {resp.status_code}")
                try:
                    data = resp.json()
                except json.JSONDecodeError as e:
                    if resp.status_code >= 400:
                        return resp.status_code, None
                    raise _Retryable("invalid JSON body") from e
                return resp.status_code, data
            except (httpx.RequestError, _Retryable) as e:
                if attempt == self._max_attempts:
                    raise ApiError(f"giving up after {attempt} attempts on {url}: {e}") from e
                self._sleep(self._backoff_base * 2 ** (attempt - 1))
        raise AssertionError("unreachable")

    def iter_pages(self, date_type: str, start: datetime, end: datetime) -> Iterator[list[dict]]:
        """Yield the `releases` list of each page. Stops only when `links.next` is absent or a page is
        empty: a short page can still have a next link followed by an empty page."""
        url: str | None = f"{self._base}/findByDates/{date_type}/{iso_z(start)}/{iso_z(end)}"
        while url:
            status, data = self._get(url)
            if status != 200 or not isinstance(data, dict):
                raise ApiError(f"unexpected HTTP {status} from findByDates")
            releases = data.get("releases") or []
            if not releases:
                return
            yield releases
            url = (data.get("links") or {}).get("next") or None

    def find_by_id(self, notice_id: str) -> dict | None:
        """One notice's release chain. A not-found is HTTP 400 with errorCode 100: returned as None."""
        status, data = self._get(f"{self._base}/findById/{notice_id}")
        if status == 200 and isinstance(data, dict):
            return data
        if status == 400 and isinstance(data, dict) and data.get("errorCode") == 100:
            return None
        raise ApiError(f"unexpected HTTP {status} from findById/{notice_id}")
