"""Rate-limit pacing and retry for the embedding API.

Gemini's free tier allows about 100 embedding requests per minute and answers
a burst with HTTP 429 RESOURCE_EXHAUSTED, usually with the delay it wants
("Please retry in 21s" / a RetryInfo detail). The old code retried three times
within a few seconds and gave up, so a 75-chunk build died on a quota that
would have cleared in 21 seconds.

Two modes, because the same embeddings class serves two very different callers:

* patient=True  -- the knowledge-base build. Nobody is waiting on a request, so
  it paces itself and waits out 429s (any single wait capped at
  MAX_PATIENT_WAIT_SECONDS; a longer ask means a daily quota and raises).
* patient=False -- the live chat assistant. A visitor is waiting inside a
  worker thread, so it keeps the original short behaviour: at most 3 attempts
  with a 1-10 s backoff, never sleeping out a server-requested delay.

This module has no third-party imports so it can be tested anywhere; the clock
and sleep are injectable so tests run instantly.
"""

import re
import threading
import time
from typing import Callable, Optional, TypeVar

T = TypeVar("T")

# Stay clearly under the 100/minute free-tier limit.
DEFAULT_MAX_PER_MINUTE = 90

# Patient mode: 429s are expected and worth waiting out.
MAX_RATE_LIMIT_ATTEMPTS = 8
MAX_PATIENT_WAIT_SECONDS = 90.0
# Default (query-time) mode, and non-429 errors in either mode.
MAX_OTHER_ATTEMPTS = 3
MAX_QUICK_WAIT_SECONDS = 10.0
_SLACK_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 60.0


class EmbeddingQuotaExhausted(RuntimeError):
    """The API asked for a wait longer than patient mode will sit through.

    A multi-minute or multi-hour retry delay means a daily quota, not a burst.
    """


class RateLimiter:
    """Space calls at least ``60 / max_per_minute`` seconds apart.

    Even spacing guarantees no 60-second window holds more than
    ``max_per_minute`` calls. Thread-safe: each caller reserves its slot under
    a lock and then sleeps outside it, so concurrent callers queue up in order
    without serialising on the sleep.
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
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            start = now if self._next_allowed is None else max(now, self._next_allowed)
            self._next_allowed = start + self.interval
        if start > now:
            self._sleep(start - now)


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
    limiter: Optional[RateLimiter] = None,
    sleep: Callable[[float], None] = time.sleep,
    patient: bool = False,
) -> T:
    """Call ``fn``, retrying failures.

    patient=False (default, for a visitor waiting on a request): at most
    MAX_OTHER_ATTEMPTS attempts with a 1-10 s backoff. A 429's requested delay
    is NOT waited out -- it is retried like any error and then raised -- so no
    call sleeps longer than MAX_QUICK_WAIT_SECONDS.

    patient=True (the offline build): a 429 waits at least the delay the API
    asked for (plus a second of slack), or an exponential backoff if it named
    none, up to MAX_RATE_LIMIT_ATTEMPTS attempts. Any single wait is capped at
    MAX_PATIENT_WAIT_SECONDS; if the API asks for more (a daily quota) it
    raises EmbeddingQuotaExhausted instead of sleeping for hours. Other errors
    get the short retry.

    If a limiter is given, every attempt goes through it, so retries are paced.
    """
    attempt = 0
    while True:
        attempt += 1
        if limiter is not None:
            limiter.wait()
        try:
            return fn()
        except Exception as exc:
            if patient and is_rate_limited(exc):
                asked = retry_delay_seconds(exc)
                if asked is not None and asked + _SLACK_SECONDS > MAX_PATIENT_WAIT_SECONDS:
                    raise EmbeddingQuotaExhausted(
                        f"Gemini asked us to wait {asked:.0f}s before retrying, longer than "
                        f"the {MAX_PATIENT_WAIT_SECONDS:.0f}s this build will wait. That "
                        "usually means a quota (for example the daily limit) is used up; "
                        "re-run the build later."
                    ) from exc
                if attempt >= MAX_RATE_LIMIT_ATTEMPTS:
                    raise
                wait = (asked + _SLACK_SECONDS) if asked is not None else min(
                    2.0 ** attempt, _MAX_BACKOFF_SECONDS
                )
                sleep(min(wait, MAX_PATIENT_WAIT_SECONDS))
            else:
                if attempt >= MAX_OTHER_ATTEMPTS:
                    raise
                sleep(min(2.0 ** (attempt - 1), MAX_QUICK_WAIT_SECONDS))
