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
