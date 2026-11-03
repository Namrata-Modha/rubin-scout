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
    assert to_thread.await_args.args[0] == svc._query_simbad_host
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
    svc._simbad_hosts = lambda: ["simbad.cds.unistra.fr"]   # single host: no fallback
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
    svc._simbad_hosts = lambda: ["simbad.cds.unistra.fr"]   # single host: no fallback
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

    def fake_query(host, ra, dec, radius):
        events.append("simbad")
        return {"name": "X", "otype": "Star", "distance_arcsec": 1.0}

    svc._query_simbad_host = fake_query
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


# ---------------------------------------------------------------------------
# Host fallback: CDS -> Harvard mirror, real socket timeouts, honest failures
# ---------------------------------------------------------------------------

import logging  # noqa: E402

import requests  # noqa: E402
from pyvo.dal.exceptions import DALServiceError  # noqa: E402
from requests.adapters import HTTPAdapter  # noqa: E402

CDS = "simbad.cds.unistra.fr"
HARVARD = "simbad.harvard.edu"


def _objs(n):
    return [Object(oid=f"SN{i}", ra=1.0, dec=2.0, classification="SNIa") for i in range(n)]


def _by_host(down: set):
    """enrich_object stand-in: hosts in `down` fail, the others answer."""
    calls = []

    async def fake(session, oid, ra, dec, host=None):
        calls.append(host)
        return host not in down

    return AsyncMock(side_effect=fake), calls


@pytest.mark.asyncio
async def test_default_host_is_cds_then_the_mirror():
    assert EnrichmentService()._simbad_hosts() == [CDS, HARVARD]


@pytest.mark.asyncio
async def test_simbad_server_setting_picks_the_primary_and_the_other_is_the_fallback():
    svc = EnrichmentService()
    with patch("app.enrichment.crossmatch.get_settings") as gs:
        gs.return_value.simbad_server = HARVARD
        assert svc._simbad_hosts() == [HARVARD, CDS]


@pytest.mark.asyncio
async def test_an_unknown_simbad_server_falls_back_to_the_default_with_a_warning(caplog):
    svc = EnrichmentService()
    with patch("app.enrichment.crossmatch.get_settings") as gs, caplog.at_level(logging.WARNING):
        gs.return_value.simbad_server = "simbad.example.org"
        assert svc._simbad_hosts() == [CDS, HARVARD]
    assert "simbad.example.org" in caplog.text


@pytest.mark.asyncio
async def test_first_failure_switches_the_rest_of_the_cycle_to_the_mirror(caplog):
    svc = EnrichmentService()
    svc.enrich_object, calls = _by_host(down={CDS})
    session = AsyncMock()

    with patch("app.enrichment.crossmatch.asyncio.sleep", new=AsyncMock()), caplog.at_level(logging.INFO):
        enriched = await svc.enrich_batch(session, _objs(4))

    # The failing object is retried on the mirror; later objects skip CDS entirely.
    assert calls == [CDS, HARVARD, HARVARD, HARVARD, HARVARD]
    assert enriched == 4
    # Exactly one summary line per cycle, naming the host that answered.
    summary = [r.message for r in caplog.records if r.message.startswith("Enriched ")]
    assert summary == [f"Enriched 4/4 objects with SIMBAD data; SIMBAD answered by {HARVARD}"]


@pytest.mark.asyncio
async def test_a_healthy_primary_never_touches_the_mirror(caplog):
    svc = EnrichmentService()
    svc.enrich_object, calls = _by_host(down=set())

    with patch("app.enrichment.crossmatch.asyncio.sleep", new=AsyncMock()), caplog.at_level(logging.INFO):
        await svc.enrich_batch(AsyncMock(), _objs(3))

    assert set(calls) == {CDS}
    assert f"SIMBAD answered by {CDS}" in caplog.text


@pytest.mark.asyncio
async def test_both_hosts_failing_still_ends_the_step_after_three_failures(caplog):
    svc = EnrichmentService()
    svc.enrich_object, calls = _by_host(down={CDS, HARVARD})

    with patch("app.enrichment.crossmatch.asyncio.sleep", new=AsyncMock()), caplog.at_level(logging.INFO):
        enriched = await svc.enrich_batch(AsyncMock(), _objs(20))

    assert enriched == 0
    # Object 1 tries CDS then the mirror; objects 2 and 3 try the mirror only.
    assert calls == [CDS, HARVARD, HARVARD, HARVARD]
    assert f"no host answered ({CDS}, {HARVARD})" in caplog.text


@pytest.mark.asyncio
async def test_the_mirror_failing_after_the_switch_counts_toward_the_stop():
    """CDS fails once, the mirror answers once, then the mirror dies: the
    consecutive count restarts at the answer and still reaches the cap."""
    svc = EnrichmentService()
    answers = iter([False, True])   # CDS fails, mirror answers object 1
    calls = []

    async def fake(session, oid, ra, dec, host=None):
        calls.append(host)
        return next(answers, False)

    svc.enrich_object = AsyncMock(side_effect=fake)
    with patch("app.enrichment.crossmatch.asyncio.sleep", new=AsyncMock()):
        enriched = await svc.enrich_batch(AsyncMock(), _objs(20))

    assert enriched == 1
    assert calls == [CDS, HARVARD] + [HARVARD] * xm.SIMBAD_MAX_CONSECUTIVE_TIMEOUTS


# --- failures are reported as failures, not as "no match" -------------------

@pytest.mark.parametrize(
    "exc",
    [
        DALServiceError("Unable to access the capabilities endpoint at:\n- https://x"),
        requests.ConnectTimeout("connect timed out"),
        requests.ConnectionError("refused"),
        TimeoutError("slow"),
        OSError("network is unreachable"),
    ],
)
def test_network_level_failures_raise_simbad_unavailable_instead_of_returning_none(exc):
    svc = EnrichmentService()
    client = MagicMock()
    client.query_region.side_effect = exc
    with patch("app.enrichment.crossmatch._simbad_client", return_value=client):
        with pytest.raises(xm.SimbadUnavailable):
            svc._query_simbad_host(CDS, 1.0, 2.0)


def test_an_empty_answer_is_none_and_an_unexpected_error_is_none_not_unavailable():
    svc = EnrichmentService()
    client = MagicMock()
    client.query_region.return_value = None
    with patch("app.enrichment.crossmatch._simbad_client", return_value=client):
        assert svc._query_simbad_host(CDS, 1.0, 2.0) is None
    client.query_region.side_effect = ValueError("bad column")
    with patch("app.enrichment.crossmatch._simbad_client", return_value=client):
        assert svc._query_simbad_host(CDS, 1.0, 2.0) is None


@pytest.mark.asyncio
async def test_enrich_object_reports_an_unavailable_host_as_a_failed_call():
    svc = EnrichmentService()
    svc._query_simbad_host = MagicMock(side_effect=xm.SimbadUnavailable("down"))
    session = AsyncMock()
    assert await svc.enrich_object(session, "SN1", 1.0, 2.0, host=CDS) is False
    session.execute.assert_not_called()


def test_the_legacy_single_call_helper_falls_through_to_the_mirror():
    svc = EnrichmentService()
    hit = {"name": "M31", "otype": "Galaxy", "distance_arcsec": 1.0}

    def fake(host, ra, dec, radius):
        if host == CDS:
            raise xm.SimbadUnavailable("down")
        return hit

    svc._query_simbad_host = fake
    assert svc._query_simbad(1.0, 2.0) == hit

    svc._query_simbad_host = MagicMock(side_effect=xm.SimbadUnavailable("down"))
    assert svc._query_simbad(1.0, 2.0) is None


# --- the real socket timeout --------------------------------------------------

def test_timeout_adapter_injects_a_connect_and_read_timeout_when_none_is_given():
    adapter = xm._TimeoutAdapter((5.0, 10.0))
    with patch.object(HTTPAdapter, "send", return_value="resp") as send:
        adapter.send(MagicMock(), stream=True)
        assert send.call_args.kwargs["timeout"] == (5.0, 10.0)
        adapter.send(MagicMock(), timeout=99)       # an explicit timeout wins
        assert send.call_args.kwargs["timeout"] == 99


def test_each_host_gets_its_own_client_with_the_timeout_adapter_mounted():
    xm._simbad_clients.clear()
    a, b = xm._simbad_client(CDS), xm._simbad_client(HARVARD)
    assert a is not b and xm._simbad_client(CDS) is a   # cached per host
    assert (a.server, b.server) == (CDS, HARVARD)
    for client in (a, b):
        for url in ("https://x/", "http://x/"):
            assert isinstance(client._session.get_adapter(url), xm._TimeoutAdapter)


def test_socket_timeouts_are_short_enough_to_release_the_shared_lock_quickly():
    """pyvo holds one class-wide lock for the whole capabilities request; the
    connect timeout bounds how long a dead host can keep every host waiting."""
    assert xm.SIMBAD_CONNECT_TIMEOUT_SECONDS <= 10
    assert xm.SIMBAD_CONNECT_TIMEOUT_SECONDS + xm.SIMBAD_READ_TIMEOUT_SECONDS <= xm.SIMBAD_CALL_TIMEOUT_SECONDS


# --- match selection: the nearest row wins and its real separation is stored ---

from astropy.table import Table  # noqa: E402


def _cone_result(rows):
    """A TAP-shaped SIMBAD result: (main_id, ra_deg, dec_deg, otype) rows."""
    return Table(
        rows=rows or None,
        names=("main_id", "ra", "dec", "otype"),
        dtype=("U30", "f8", "f8", "U8"),
    )


def _query_with(result, ra=10.0, dec=20.0):
    svc = EnrichmentService()
    client = MagicMock()
    client.query_region.return_value = result
    with patch("app.enrichment.crossmatch._simbad_client", return_value=client):
        return svc._query_simbad_host(CDS, ra, dec)


def test_the_nearer_row_is_chosen_even_when_the_farther_one_comes_first():
    far = ("FAR", 10.0, 20.0 + 3.0 / 3600, "X")      # 3 arcsec north
    near = ("NEAR", 10.0, 20.0 + 1.0 / 3600, "Star")  # 1 arcsec north
    out = _query_with(_cone_result([far, near]))
    assert out["name"] == "NEAR"
    assert out["otype"] == "Star"
    assert out["distance_arcsec"] == pytest.approx(1.0, abs=1e-3)


def test_an_offset_source_gets_a_real_separation_above_zero():
    out = _query_with(_cone_result([("OFF", 10.0, 20.0 + 2.5 / 3600, "Gl")]))
    assert out["distance_arcsec"] > 0.0
    assert out["distance_arcsec"] == pytest.approx(2.5, abs=1e-3)


def test_ra_offset_is_scaled_by_cos_dec():
    # 2 arcsec of RA at dec 60 is only ~1 arcsec on the sky.
    out = _query_with(_cone_result([("E", 10.0 + 2.0 / 3600, 60.0, "Star")]), dec=60.0)
    assert out["distance_arcsec"] == pytest.approx(1.0, abs=1e-2)


def test_sexagesimal_positions_from_the_legacy_votable_are_understood():
    result = Table(
        rows=[("A", "00 40 00.0", "+20 00 03.0", "Star")],
        names=("MAIN_ID", "RA", "DEC", "OTYPE"),
        dtype=("U10", "U16", "U16", "U8"),
    )
    out = _query_with(result, ra=10.0, dec=20.0)   # 00h40m = 10 deg
    assert out["distance_arcsec"] == pytest.approx(3.0, abs=1e-2)


def test_rows_without_a_usable_position_report_an_unknown_separation_not_zero():
    result = Table(rows=[("A", "Star")], names=("main_id", "otype"), dtype=("U10", "U8"))
    out = _query_with(result)
    assert out["name"] == "A"
    assert out["distance_arcsec"] is None


def test_no_rows_still_returns_none():
    assert _query_with(_cone_result([])) is None
    assert _query_with(None) is None
