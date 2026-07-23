"""
Unit tests for SIMBAD enrichment: FRB exclusion and thread offload.

No network and no database: the blocking SIMBAD call and the rate-limit sleep
are patched, and Object instances are constructed in memory.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.enrichment.crossmatch import SIMBAD_RATE_LIMIT_SECONDS, EnrichmentService
from app.models.models import Object

# ---------------------------------------------------------------------------
# The blocking SIMBAD query is offloaded to a worker thread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_object_offloads_blocking_query_to_thread():
    svc = EnrichmentService()
    session = AsyncMock()

    with patch(
        "app.enrichment.crossmatch.asyncio.to_thread",
        new=AsyncMock(return_value=None),
    ) as to_thread:
        await svc.enrich_object(session, "oid1", 10.0, 20.0)

    to_thread.assert_awaited_once()
    # The function handed to the worker thread must be the sync SIMBAD query.
    assert to_thread.await_args.args[0] == svc._query_simbad
    # No match -> no DB write attempted.
    session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# enrich_batch applies a real (non-blocking) rate-limit delay
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_batch_rate_limits_with_sleep():
    svc = EnrichmentService()
    svc.enrich_object = AsyncMock()
    session = AsyncMock()
    obj = Object(oid="SN1", ra=1.0, dec=2.0, classification="SNIa")

    with patch(
        "app.enrichment.crossmatch.asyncio.sleep", new=AsyncMock()
    ) as sleep:
        await svc.enrich_batch(session, [obj])

    sleep.assert_awaited_with(SIMBAD_RATE_LIMIT_SECONDS)


# ---------------------------------------------------------------------------
# FRBs are excluded from the 5-arcsec SIMBAD path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_batch_skips_frb_objects():
    svc = EnrichmentService()
    svc.enrich_object = AsyncMock()
    session = AsyncMock()

    frb = Object(oid="FRB20180725A", ra=1.0, dec=2.0, classification="FRB")
    sn = Object(oid="SN1", ra=3.0, dec=4.0, classification="SNIa")

    with patch("app.enrichment.crossmatch.asyncio.sleep", new=AsyncMock()):
        enriched = await svc.enrich_batch(session, [frb, sn])

    # Only the supernova was enriched; the FRB was skipped entirely.
    assert enriched == 1
    svc.enrich_object.assert_awaited_once()
    assert svc.enrich_object.await_args.args[1] == "SN1"
