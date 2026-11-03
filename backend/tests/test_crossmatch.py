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


# ---------------------------------------------------------------------------
# Bounded SIMBAD step: per-call timeout, step budget, short transactions
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

import app.enrichment.crossmatch as xm  # noqa: E402


async def _never_returns(*_args, **_kwargs):
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_a_hung_simbad_call_is_cut_off_at_the_per_call_timeout():
    svc = EnrichmentService()
    session = AsyncMock()

    with patch.object(xm, "SIMBAD_CALL_TIMEOUT_SECONDS", 0.05), patch(
        "app.enrichment.crossmatch.asyncio.to_thread", new=_never_returns
    ):
        answered = await asyncio.wait_for(svc.enrich_object(session, "oid1", 1.0, 2.0), 5)

    assert answered is False
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_per_call_timeout_constant_is_about_fifteen_seconds():
    assert 5 <= xm.SIMBAD_CALL_TIMEOUT_SECONDS <= 30
    assert 120 <= xm.SIMBAD_STEP_BUDGET_SECONDS <= 600


@pytest.mark.asyncio
async def test_batch_ends_cleanly_when_the_step_budget_is_spent():
    svc = EnrichmentService()
    svc.enrich_object = AsyncMock(return_value=True)
    session = AsyncMock()
    objs = [Object(oid=f"SN{i}", ra=1.0, dec=2.0, classification="SNIa") for i in range(10)]

    # Clock: start at 0 (deadline = budget); the loop reads it once per object.
    # Objects 0-2 run inside the budget, the 4th read is past it.
    ticks = iter([0.0, 1.0, 2.0, 3.0, xm.SIMBAD_STEP_BUDGET_SECONDS + 1])
    with patch("app.enrichment.crossmatch.time.monotonic", side_effect=lambda: next(ticks)), patch(
        "app.enrichment.crossmatch.asyncio.sleep", new=AsyncMock()
    ):
        enriched = await svc.enrich_batch(session, objs)

    assert enriched == 3
    assert svc.enrich_object.await_count == 3


@pytest.mark.asyncio
async def test_batch_stops_after_consecutive_timeouts_instead_of_leaking_threads():
    svc = EnrichmentService()
    svc.enrich_object = AsyncMock(return_value=False)
    session = AsyncMock()
    objs = [Object(oid=f"SN{i}", ra=1.0, dec=2.0, classification="SNIa") for i in range(20)]

    with patch("app.enrichment.crossmatch.asyncio.sleep", new=AsyncMock()):
        enriched = await svc.enrich_batch(session, objs)

    assert enriched == 0
    assert svc.enrich_object.await_count == xm.SIMBAD_MAX_CONSECUTIVE_TIMEOUTS


@pytest.mark.asyncio
async def test_a_successful_answer_resets_the_consecutive_timeout_count():
    svc = EnrichmentService()
    # timeout, timeout, ok, timeout, timeout, ok ... never 3 in a row.
    svc.enrich_object = AsyncMock(side_effect=[False, False, True] * 4)
    session = AsyncMock()
    objs = [Object(oid=f"SN{i}", ra=1.0, dec=2.0, classification="SNIa") for i in range(12)]

    with patch("app.enrichment.crossmatch.asyncio.sleep", new=AsyncMock()):
        enriched = await svc.enrich_batch(session, objs)

    assert enriched == 4
    assert svc.enrich_object.await_count == 12


@pytest.mark.asyncio
async def test_no_transaction_is_open_while_waiting_on_simbad():
    """The caller's read transaction is ended before the first SIMBAD call,
    and each match is committed before the next call starts."""
    svc = EnrichmentService()
    events: list[str] = []
    session = AsyncMock()
    session.commit = AsyncMock(side_effect=lambda: events.append("commit"))
    session.execute = AsyncMock()

    def fake_query(ra, dec, radius):
        events.append("simbad")
        return {"name": "X", "otype": "Star", "distance_arcsec": 1.0}

    svc._query_simbad = fake_query
    objs = [Object(oid=f"SN{i}", ra=1.0, dec=2.0, classification="SNIa") for i in range(2)]

    with patch("app.enrichment.crossmatch.asyncio.sleep", new=AsyncMock()):
        await svc.enrich_batch(session, objs)

    assert events == ["commit", "simbad", "commit", "simbad", "commit"]


@pytest.mark.asyncio
async def test_objects_are_snapshotted_so_commit_cannot_break_attribute_access():
    """expire_on_commit is False in prod, but the batch must not depend on it:
    values are read before the first commit."""
    svc = EnrichmentService()
    svc.enrich_object = AsyncMock(return_value=True)
    session = AsyncMock()
    obj = MagicMock(oid="SN1", ra=1.0, dec=2.0, classification="SNIa", cross_match_catalog=None)

    def expire(*_a, **_k):
        obj.oid = obj.ra = obj.dec = None  # simulate expired attributes

    session.commit = AsyncMock(side_effect=expire)
    with patch("app.enrichment.crossmatch.asyncio.sleep", new=AsyncMock()):
        await svc.enrich_batch(session, [obj])

    assert svc.enrich_object.await_args.args[1:] == ("SN1", 1.0, 2.0)
