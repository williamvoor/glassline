"""A daily cap on model runs across all users (MAX_TURNS_PER_DAY), for a public deploy.

One model run is a typed question or an Ask: up to MAX_TOOL_ITERATIONS model calls plus Repairs.
The day is UTC. The count lives in memory, so a restart starts the day over.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime

log = logging.getLogger(__name__)

# MAX_TURNS_PER_DAY spent: no model run, no Level (Drill-downs and Boards still work).
TURN_LIMIT_PROSE = "Today's question limit has been reached. Please try again tomorrow (the day resets at 00:00 UTC)."


def utc_today() -> date:
    return datetime.now(UTC).date()


class TurnBudget:
    def __init__(self, limit: int, *, contact: str | None = None, today: Callable[[], date] = utc_today):
        if limit <= 0:
            raise ValueError("limit must be positive")
        self.limit = limit
        contact = (contact or "").strip()
        # What a refused model run shows: TURN_LIMIT_CONTACT (deployment config) says whom to ask.
        self.message = f"{TURN_LIMIT_PROSE} {contact}" if contact else TURN_LIMIT_PROSE
        self._today = today
        self._day = today()
        self._used = 0

    def take(self) -> bool:
        """One model run from today's budget: False (and nothing taken) once it is spent."""
        day = self._today()
        if day != self._day:
            self._day, self._used = day, 0
        if self._used >= self.limit:
            log.info("model run refused: daily limit %d reached (MAX_TURNS_PER_DAY)", self.limit)
            return False
        self._used += 1
        if self._used == self.limit:
            log.warning("daily model-run limit reached: %d (MAX_TURNS_PER_DAY)", self.limit)
        return True
