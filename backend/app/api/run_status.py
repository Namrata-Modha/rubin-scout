"""Shared helpers for reporting what an ingestion run actually did.

Admin trigger routes used to return a hardcoded ``{"status": "ok", ...}``
regardless of outcome. None of the ingestion services raise on failure --
they catch, record the failure on their ``IngestionLog`` row, and return 0 --
so "the HTTP call didn't raise" says nothing at all about whether the run
worked. A fully failed Fink run and a healthy run that found nothing new
both produced a byte-identical ``{"status": "ok", "alerts_inserted": 0}``.

These helpers read back the row the run actually wrote and report it.
"""

from typing import Any, Optional

from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import IngestionLog

# Statuses that mean "the run did what it could; no operator action implied".
#   completed  -- worked
#   no_data    -- worked, upstream had nothing (TNS on a quiet day)
#   partial    -- LSST drained some of its window; the cursor deliberately
#                 does not advance and the next cycle retries. Expected and
#                 self-healing, so not an error to the caller.
OK_STATUSES = frozenset({"completed", "no_data", "partial"})

# An ingestion failure is almost always an upstream broker problem (Fink,
# TNS, VizieR or GWOSC unreachable), not a bug in this service and not an
# auth problem. 502 keeps it distinct from the 401/403 these routes already
# return for OUR admin key, and from a 500 raised by our own code.
UPSTREAM_FAILURE_CODE = 502


async def latest_run_id(db: AsyncSession, source: str) -> int:
    """Highest existing IngestionLog id for `source`, or 0 if none.

    Captured BEFORE triggering a run so the rows that run creates can be
    identified afterwards without guessing from timestamps.
    """
    result = await db.execute(
        select(IngestionLog.id)
        .where(IngestionLog.source == source)
        .order_by(IngestionLog.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none() or 0


async def runs_since(db: AsyncSession, source: str, after_id: int) -> list[IngestionLog]:
    """Every IngestionLog row `source` wrote after `after_id`, oldest first."""
    result = await db.execute(
        select(IngestionLog)
        .where(IngestionLog.source == source, IngestionLog.id > after_id)
        .order_by(IngestionLog.id)
    )
    return list(result.scalars().all())


def _body(
    status: str,
    source: str,
    objects_ingested: int,
    run_id: Optional[int],
    error: Optional[str],
    extra: Optional[dict] = None,
) -> dict:
    """The one response shape every trigger route returns."""
    body: dict[str, Any] = {
        "status": status,
        "source": source,
        "objects_ingested": objects_ingested,
        "run_id": run_id,
        "error": error,
    }
    if extra:
        body.update(extra)
    return body


def respond(body: dict) -> Any:
    """200 for an OK status, 502 for anything else."""
    if body["status"] in OK_STATUSES or body["status"] == "ok":
        return body
    return JSONResponse(status_code=UPSTREAM_FAILURE_CODE, content=body)


def from_single_run(
    source: str,
    count: int,
    runs: list[IngestionLog],
    extra: Optional[dict] = None,
) -> Any:
    """Report a service that writes exactly one IngestionLog row per call.

    Covers fink, lsst and chime triggers, plus tns/daily.

    A run that wrote NO row at all is itself a failure worth surfacing: every
    one of these services creates its row before doing any work, so a missing
    row means the call died before that point (or, pre-2026-09, that a
    poisoned session swallowed the failure record entirely).
    """
    if not runs:
        return respond(
            _body(
                "failed",
                source,
                count,
                None,
                "Run recorded no ingestion_log row; it failed before logging.",
                extra,
            )
        )

    run = runs[-1]
    status = "ok" if run.status == "completed" else (run.status or "failed")
    # Prefer the row's own count, but only when it actually recorded one --
    # `or` would treat a legitimately-zero row as unset and silently
    # substitute the return value.
    ingested = run.objects_ingested if run.objects_ingested is not None else count
    return respond(_body(status, source, ingested, run.id, run.error_message, extra))


def from_multiple_runs(
    source: str,
    count: int,
    runs: list[IngestionLog],
    extra: Optional[dict] = None,
) -> Any:
    """Report a service that writes several IngestionLog rows per call.

    Only tns/seed, which loops ingest_from_daily_csv over N days and so
    produces one row per day. The call is reported as failed when EVERY row
    failed -- a single bad day among several is normal (TNS publishes no CSV
    on quiet days) and must not mark the whole seed as broken.
    """
    if not runs:
        return respond(
            _body(
                "failed",
                source,
                count,
                None,
                "Seed recorded no ingestion_log rows; it failed before logging "
                "(most likely missing TNS_USER_ID / TNS_USER_NAME).",
                extra,
            )
        )

    ok = [r for r in runs if r.status in OK_STATUSES]
    detail = {**(extra or {}), "runs": len(runs), "runs_ok": len(ok)}

    if not ok:
        first_error = next((r.error_message for r in runs if r.error_message), None)
        worst = runs[-1].status or "failed"
        return respond(_body(worst, source, count, runs[-1].id, first_error, detail))

    return respond(_body("ok", source, count, runs[-1].id, None, detail))
