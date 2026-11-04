"""Rate-limit pacing and retry for the embedding API.

Gemini's free tier allows about 100 embedding requests per minute and answers
a burst with HTTP 429 RESOURCE_EXHAUSTED, usually with the delay it wants
("Please retry in 21s" / a RetryInfo detail). The old code retried three times
within a few seconds and gave up, so a 75-chunk build died on a quota that
would have cleared in 21 seconds.

This module has no third-party imports so it can be tested anywhere; the clock
and sleep are injectable so tests run instantly.
"""

import re
import time
from typing import Callable, Optional, TypeVar

T = TypeVar("T")

# Stay clearly under the 100/minute free-tier limit.
DEFAULT_MAX_PER_MINUTE = 90

# 429s are expected and worth waiting out; other errors get a short retry.
MAX_RATE_LIMIT_ATTEMPTS = 8
MAX_OTHER_ATTEMPTS = 3
_SLACK_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 60.0


class RateLimiter:
    """Space calls at least ``60 / max_per_minute`` seconds apart.

    Even spacing guarantees no 60-second window holds more than
    ``max_per_minute`` calls.
    """

    def __init__(
        self,
        max_per_minute: float = DEFAULT_MAX_PER_MINUTE,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if max_per_minute <= 0:
            raise ValueError("max_per_minute must be positive")
        self.interval = 60.0 / max_per_minute
        self._clock = clock
        self._sleep = sleep
        self._next_allowed: Optional[float] = None

    def wait(self) -> None:
        now = self._clock()
        if self._next_allowed is not None and now < self._next_allowed:
            self._sleep(self._next_allowed - now)
            now = self._next_allowed
        self._next_allowed = now + self.interval


def is_rate_limited(exc: BaseException) -> bool:
    return (
        getattr(exc, "code", None) == 429
        or getattr(exc, "status_code", None) == 429
        or "RESOURCE_EXHAUSTED" in str(exc)
    )


def retry_delay_seconds(exc: BaseException) -> Optional[float]:
    """The delay the API asked for, if the error carries one."""
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        error = details.get("error", details)
        entries = error.get("details") if isinstance(error, dict) else None
        for entry in entries or []:
            if isinstance(entry, dict) and "retryDelay" in entry:
                match = re.match(r"\s*([\d.]+)\s*s", str(entry["retryDelay"]))
                if match:
                    return float(match.group(1))
    match = re.search(r"retry in ([\d.]+)\s*s", str(exc), re.IGNORECASE)
    return float(match.group(1)) if match else None


def call_with_retry(
    fn: Callable[[], T],
    limiter: RateLimiter,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``fn`` through the limiter, waiting out 429s instead of giving up.

    A 429 waits at least the delay the API asked for (plus a second of slack),
    or an exponential backoff if it named none, and is retried up to
    MAX_RATE_LIMIT_ATTEMPTS times. Any other error is retried briefly, up to
    MAX_OTHER_ATTEMPTS, then raised. Every attempt goes through the limiter,
    so retries are paced too.
    """
    attempt = 0
    while True:
        attempt += 1
        limiter.wait()
        try:
            return fn()
        except Exception as exc:
            if is_rate_limited(exc):
                if attempt >= MAX_RATE_LIMIT_ATTEMPTS:
                    raise
                asked = retry_delay_seconds(exc)
                backoff = min(2.0 ** attempt, _MAX_BACKOFF_SECONDS)
                sleep((asked + _SLACK_SECONDS) if asked is not None else backoff)
            else:
                if attempt >= MAX_OTHER_ATTEMPTS:
                    raise
                sleep(min(2.0 ** (attempt - 1), 10.0))
