"""A daily cap on how many chat questions reach the paid model.

The per-IP rate limits stop one visitor from hammering the assistant; this stops
the total from running up the Gemini bill (or exhausting the free-tier quota)
no matter how many visitors there are.

It is an IN-MEMORY counter, deliberately with no database table:

* it resets when the process restarts (a deploy, a crash, or Render's free tier
  spinning the service down and back up) -- so the real daily ceiling can be
  higher than ASK_DAILY_LIMIT after a restart;
* it is per process, so with more than one instance each has its own count;
* the "day" is the UTC calendar day.

That is acceptable for a safety valve on a small single-instance deployment. A
hard spending limit should also be set on the Google API key itself.
"""

import threading
from datetime import date, datetime, timezone
from typing import Callable, Optional

BUSY_MESSAGE = "The assistant is busy right now. Please try again later."


class DailyCounter:
    """Thread-safe count of accepted requests per UTC day."""

    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self._clock = clock
        self._lock = threading.Lock()
        self._day: Optional[date] = None
        self._count = 0

    def try_acquire(self, limit: int) -> bool:
        """Count one request and return True, or return False if ``limit`` is used up."""
        with self._lock:
            today = self._clock().date()
            if today != self._day:
                self._day, self._count = today, 0
            if self._count >= limit:
                return False
            self._count += 1
            return True

    @property
    def used_today(self) -> int:
        with self._lock:
            return self._count if self._day == self._clock().date() else 0

    def reset(self) -> None:
        with self._lock:
            self._day, self._count = None, 0


# The one counter the API route uses.
ask_counter = DailyCounter()
