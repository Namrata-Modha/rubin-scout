"""
Unit tests for app/ingestion/scheduler.py: CHIME scheduling.

CHIME/FRB Catalog 1 is static, so it must not be pulled on every ingestion
cycle. It now runs as its own monthly job with an idempotent manual trigger.
"""
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.ingestion.scheduler as sched


def test_chime_not_pulled_in_ingestion_cycle():
    """run_ingestion_cycle must no longer download the CHIME catalog."""
    src = inspect.getsource(sched.run_ingestion_cycle)
    assert "chime_service.ingest" not in src


def test_run_chime_ingestion_exists():
    assert hasattr(sched, "run_chime_ingestion")


@pytest.mark.asyncio
async def test_run_chime_ingestion_calls_service():
    """The dedicated monthly job delegates to ChimeFRBIngestionService.ingest."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch.object(sched, "async_session", MagicMock(return_value=cm)), \
         patch.object(sched.chime_service, "ingest",
                      new=AsyncMock(return_value=5)) as ingest:
        await sched.run_chime_ingestion()

    ingest.assert_awaited_once()


def test_chime_manual_trigger_route_registered():
    """An idempotent manual trigger endpoint must be exposed."""
    from app.api.ingest import router
    paths = {r.path for r in router.routes}
    assert "/api/ingest/chime/trigger" in paths


def test_lsst_interval_stays_below_max_window_span():
    """scheduler.py asserts this at import time already; this test asserts
    it directly too, so a violation fails here with a clear message rather
    than an opaque ImportError from an unrelated test file."""
    from app.ingestion.lsst_service import MAX_WINDOW_SPAN
    assert sched.LSST_INGESTION_INTERVAL_SECONDS < MAX_WINDOW_SPAN.total_seconds()


def test_lsst_not_pulled_in_ingestion_cycle():
    """run_ingestion_cycle must not ingest LSST directly -- it's its own
    dedicated interval job, same reasoning as CHIME's exclusion (LSST's
    volume is bursty and an order of magnitude larger than TNS/ALeRCE)."""
    src = inspect.getsource(sched.run_ingestion_cycle)
    assert "lsst_service.ingest" not in src


def test_run_lsst_ingestion_exists():
    assert hasattr(sched, "run_lsst_ingestion")


@pytest.mark.asyncio
async def test_run_lsst_ingestion_calls_service():
    """The dedicated interval job delegates to LsstFinkIngestionService.ingest."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch.object(sched, "async_session", MagicMock(return_value=cm)), \
         patch.object(sched.lsst_service, "ingest",
                      new=AsyncMock(return_value=3)) as ingest:
        await sched.run_lsst_ingestion()

    ingest.assert_awaited_once()


def test_lsst_ingestion_job_registered_with_configured_interval():
    """start_background_scheduler must register run_lsst_ingestion using
    LSST_INGESTION_INTERVAL_SECONDS -- the constant already defined and
    already asserted against MAX_WINDOW_SPAN -- not a new literal that
    could silently drift out of sync with that assertion."""
    src = inspect.getsource(sched.start_background_scheduler)
    assert "run_lsst_ingestion" in src
    assert "LSST_INGESTION_INTERVAL_SECONDS" in src
    assert 'id="lsst_ingestion"' in src


# --------------------------------------------------------------------------- #
# refresh_gw_events IngestionLog trail
#
# This was the only scheduled job writing no IngestionLog row, so there was
# no way to tell whether the weekly refresh had ever run, let alone whether
# it worked. Every other source logs one row per run; this now does too.
# --------------------------------------------------------------------------- #

def _session_capturing_logs():
    """Async-session stand-in that records every IngestionLog added to it."""
    added = []
    session = AsyncMock()
    session.add = MagicMock(side_effect=added.append)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, session, added


def _gw_logs(added):
    from app.models.models import IngestionLog
    return [o for o in added if isinstance(o, IngestionLog)]


@pytest.mark.asyncio
async def test_gw_refresh_writes_a_completed_row_with_the_count():
    cm, _session, added = _session_capturing_logs()

    report = {"retired": [], "retired_unresolved": [], "unretired": [],
              "skipped_reason": None}

    with patch.object(sched, "async_session", MagicMock(return_value=cm)), \
         patch.object(sched, "_fetch_gwosc_payload", new=AsyncMock(return_value={})), \
         patch.object(sched, "fetch_gwosc_catalog_index", new=AsyncMock(return_value=object())), \
         patch.object(sched.gw_service, "seed_gw_events",
                      new=AsyncMock(return_value=421)), \
         patch.object(sched.gw_service, "reconcile_retired_events",
                      new=AsyncMock(return_value=report)):
        await sched.refresh_gw_events()

    logs = _gw_logs(added)
    assert len(logs) == 1
    row = logs[0]
    assert row.source == sched.GW_SOURCE_NAME
    assert row.status == "completed"
    assert row.objects_ingested == 421      # the real seeded/updated count
    assert row.started_at is not None
    assert row.completed_at >= row.started_at
    assert row.error_message is None


@pytest.mark.asyncio
async def test_gw_refresh_writes_a_failed_row_with_the_error():
    """seed_gw_events raising must leave a failed row, not silence.

    A fresh row is expected rather than an edit of the "running" one: the
    handler rolls back first (the raise may have aborted the transaction),
    which discards the uncommitted original.
    """
    cm, session, added = _session_capturing_logs()

    with patch.object(sched, "async_session", MagicMock(return_value=cm)), \
         patch.object(sched, "_fetch_gwosc_payload", new=AsyncMock(return_value={})), \
         patch.object(sched.gw_service, "seed_gw_events",
                      new=AsyncMock(side_effect=RuntimeError("GWOSC unreachable"))):
        await sched.refresh_gw_events()

    session.rollback.assert_awaited()       # before writing the failure record
    logs = _gw_logs(added)
    assert len(logs) == 2                   # "running", then the replacement
    failure = logs[-1]
    assert failure.source == sched.GW_SOURCE_NAME
    assert failure.status == "failed"
    assert "GWOSC unreachable" in failure.error_message
    assert failure.objects_ingested == 0
    assert failure.started_at == logs[0].started_at   # true duration preserved
    assert failure.completed_at is not None


@pytest.mark.asyncio
async def test_reconciliation_failure_does_not_mark_the_seed_failed():
    """Seeding already committed. A follow-on reconciliation error is recorded
    on the row but must not misreport a good seed as a failed run."""
    cm, _session, added = _session_capturing_logs()

    with patch.object(sched, "async_session", MagicMock(return_value=cm)), \
         patch.object(sched, "_fetch_gwosc_payload", new=AsyncMock(return_value={})), \
         patch.object(sched, "fetch_gwosc_catalog_index", new=AsyncMock(return_value=object())), \
         patch.object(sched.gw_service, "seed_gw_events",
                      new=AsyncMock(return_value=7)), \
         patch.object(sched.gw_service, "reconcile_retired_events",
                      new=AsyncMock(side_effect=RuntimeError("reconcile boom"))):
        await sched.refresh_gw_events()

    row = _gw_logs(added)[0]
    assert row.status == "completed"        # NOT failed
    assert row.objects_ingested == 7
    assert row.query_params["reconciliation"]["error"] == "reconcile boom"


@pytest.mark.asyncio
async def test_reconciliation_summary_is_recorded_on_the_row():
    cm, _session, added = _session_capturing_logs()

    report = {"retired": [{"superevent_id": "GWX"}], "retired_unresolved": ["GWY"],
              "unretired": [], "skipped_reason": None}

    with patch.object(sched, "async_session", MagicMock(return_value=cm)), \
         patch.object(sched, "_fetch_gwosc_payload", new=AsyncMock(return_value={})), \
         patch.object(sched, "fetch_gwosc_catalog_index", new=AsyncMock(return_value=object())), \
         patch.object(sched.gw_service, "seed_gw_events", new=AsyncMock(return_value=3)), \
         patch.object(sched.gw_service, "reconcile_retired_events",
                      new=AsyncMock(return_value=report)):
        await sched.refresh_gw_events()

    recon = _gw_logs(added)[0].query_params["reconciliation"]
    assert recon["retired"] == 1
    assert recon["retired_unresolved"] == 1
    assert recon["skipped_reason"] is None


def test_gw_source_name_follows_the_column_convention():
    """Every other IngestionLog.source names the data source, not the job."""
    assert sched.GW_SOURCE_NAME == "gwosc_catalog"
