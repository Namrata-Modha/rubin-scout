"""
Unit tests for GW-related pure functions.

_classify_from_masses lives in app/enrichment/gw_crossmatch.py.
_gw_sky_separation_deg lives in app/api/alerts.py.

Neither function touches the database, so no fixtures are required.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.alerts import _gw_sky_separation_deg
from app.enrichment.gw_crossmatch import (
    GWCrossMatchService,
    LocalizationUnavailableError,
    _classify_from_masses,
    _is_broken_skymap_url,
)
from app.models.models import GWCandidate, GWEvent, Object

# ---------------------------------------------------------------------------
# _classify_from_masses
# ---------------------------------------------------------------------------

def test_classify_bbh():
    """Two heavy black holes (35+30 solar masses) -> BBH."""
    assert _classify_from_masses(35.0, 30.0) == {"BBH": 1.0}


def test_classify_bns():
    """Two neutron-star-mass components (1.4+1.2 solar masses, both < 3) -> BNS."""
    assert _classify_from_masses(1.4, 1.2) == {"BNS": 1.0}


def test_classify_nsbh():
    """Heavy primary (9 solar masses) + light secondary (1.9, < 3) -> NSBH."""
    assert _classify_from_masses(9.0, 1.9) == {"NSBH": 1.0}


def test_classify_none_masses():
    """Unknown masses should default to the most common class, BBH."""
    assert _classify_from_masses(None, None) == {"BBH": 1.0}


# ---------------------------------------------------------------------------
# _gw_sky_separation_deg
# ---------------------------------------------------------------------------

def test_sky_separation_same_point():
    """A point separated from itself should have zero angular distance."""
    assert _gw_sky_separation_deg(0.0, 0.0, 0.0, 0.0) == 0.0


def test_sky_separation_known_value():
    """Two points 90 degrees apart in RA along the equator should give approx 90 degrees."""
    sep = _gw_sky_separation_deg(0.0, 0.0, 90.0, 0.0)
    assert abs(sep - 90.0) < 0.01


def test_sky_separation_gw170817():
    """GW170817 and host galaxy NGC 4993 share the same sky coordinates.

    Separation must be < 1e-4 degrees (< 0.4 arcsec). A tiny floating-point
    residual can appear because sin^2 + cos^2 != exactly 1.0 in IEEE 754
    arithmetic for non-zero angles.
    """
    # GW170817: RA 197.45, Dec -23.38 / NGC 4993: RA 197.45, Dec -23.38
    sep = _gw_sky_separation_deg(197.45, -23.38, 197.45, -23.38)
    assert sep == pytest.approx(0.0, abs=1e-4)


# ---------------------------------------------------------------------------
# cross_match_event must refuse events with no localization
# ---------------------------------------------------------------------------

def _session_returning(scalar):
    """Build an AsyncMock session whose execute() yields `scalar`."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_cross_match_raises_without_localization():
    """An event with ra_center/dec_center None must raise, never return rows.

    This is the core guarantee: a spatially unfiltered candidate list is
    scientifically invalid and must not be produced.
    """
    svc = GWCrossMatchService()
    event = GWEvent(
        superevent_id="GW170817",
        event_time=datetime(2017, 8, 17, tzinfo=timezone.utc),
        properties={"ra_center": None, "dec_center": None, "area_90_deg2": None},
    )
    session = _session_returning(event)

    with pytest.raises(LocalizationUnavailableError):
        await svc.cross_match_event(session, "GW170817")


@pytest.mark.asyncio
async def test_cross_match_missing_event_raises_valueerror():
    """A genuinely absent event still raises ValueError (mapped to 404)."""
    svc = GWCrossMatchService()
    session = _session_returning(None)
    with pytest.raises(ValueError):
        await svc.cross_match_event(session, "GW999999")


def test_search_by_time_only_is_deleted():
    """The dead, spatially-unfiltered fallback must no longer exist."""
    assert not hasattr(GWCrossMatchService, "_search_by_time_only")


# ---------------------------------------------------------------------------
# seed_gw_events upsert + null skymap_url
# ---------------------------------------------------------------------------

_NEW_EVENT = {
    "superevent_id": "GW150914",
    "event_time": datetime(2015, 9, 14, tzinfo=timezone.utc),
    "far": 1e-7,
    "classification": {"BBH": 1.0},
    "properties": {
        # Flat GWOSC catalog never carries localisation
        "ra_center": None,
        "dec_center": None,
        "area_90_deg2": None,
        "distance_mpc": 440.0,
        "distance_err_mpc": None,
        "mass_1_solar": 36.0,
        "mass_2_solar": 29.0,
        "description": None,
    },
}


@pytest.mark.asyncio
async def test_seed_updates_existing_and_preserves_local_fields(monkeypatch):
    """An existing row is refreshed (far/classification/properties), skymap_url
    is set to None, and locally computed localisation is preserved rather than
    clobbered by the incoming Nones."""
    svc = GWCrossMatchService()

    async def fake_fetch():
        return [_NEW_EVENT]

    monkeypatch.setattr(
        "app.enrichment.gw_crossmatch.fetch_gwosc_events", fake_fetch
    )

    existing = GWEvent(
        superevent_id="GW150914",
        event_time=datetime(2015, 9, 14, tzinfo=timezone.utc),
        far=9.9,
        classification={"STALE": 1.0},
        skymap_url="https://gracedb.ligo.org/apiweb/superevents/GW150914/files/bayestar.multiorder.fits",
        properties={
            # Pretend a future skymap job wrote real localisation locally
            "ra_center": 150.0,
            "dec_center": -70.0,
            "area_90_deg2": 180.0,
            "distance_mpc": 400.0,
        },
    )
    session = _session_returning(existing)

    n = await svc.seed_gw_events(session)

    assert n == 1
    # Refreshed from GWOSC
    assert existing.far == 1e-7
    assert existing.classification == {"BBH": 1.0}
    assert existing.skymap_url is None
    assert existing.properties["distance_mpc"] == 440.0
    assert existing.properties["mass_1_solar"] == 36.0
    # Preserved locally computed localisation (incoming None must not clobber)
    assert existing.properties["ra_center"] == 150.0
    assert existing.properties["dec_center"] == -70.0
    assert existing.properties["area_90_deg2"] == 180.0
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_seed_inserts_new_with_null_skymap(monkeypatch):
    """Newly inserted events store skymap_url = None (no wrong URL)."""
    svc = GWCrossMatchService()

    async def fake_fetch():
        return [_NEW_EVENT]

    monkeypatch.setattr(
        "app.enrichment.gw_crossmatch.fetch_gwosc_events", fake_fetch
    )

    result = MagicMock()
    result.scalar_one_or_none.return_value = None  # no existing row
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    added = []
    session.add = MagicMock(side_effect=added.append)

    n = await svc.seed_gw_events(session)

    assert n == 1
    assert len(added) == 1
    assert added[0].skymap_url is None
    assert added[0].superevent_id == "GW150914"


# ---------------------------------------------------------------------------
# skymap_url is not clobbered unconditionally
# ---------------------------------------------------------------------------

def test_is_broken_skymap_url():
    assert _is_broken_skymap_url(None) is True
    assert _is_broken_skymap_url(
        "https://gracedb.ligo.org/apiweb/superevents/GW170817/files/bayestar.multiorder.fits"
    ) is True
    assert _is_broken_skymap_url(
        "https://zenodo.org/records/8177023/files/IGWN-GWTC3p0-v2-PESkyLocalizations.tar.gz"
    ) is False


async def _run_seed_against_existing(monkeypatch, existing):
    svc = GWCrossMatchService()

    async def fake_fetch():
        return [_NEW_EVENT]

    monkeypatch.setattr(
        "app.enrichment.gw_crossmatch.fetch_gwosc_events", fake_fetch
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    await svc.seed_gw_events(session)
    return existing


@pytest.mark.asyncio
async def test_seed_preserves_locally_set_skymap_url(monkeypatch):
    """A real (locally-ingested) skymap URL must survive the weekly refresh.

    The incoming GWOSC payload has no skymap, so a non-broken existing URL must
    be left untouched.
    """
    real_url = (
        "https://zenodo.org/records/8177023/files/"
        "IGWN-GWTC3p0-v2-PESkyLocalizations.tar.gz#GW150914"
    )
    existing = GWEvent(
        superevent_id="GW150914",
        far=9.9,
        classification={"STALE": 1.0},
        skymap_url=real_url,
        properties={"ra_center": 150.0, "dec_center": -70.0},
    )
    await _run_seed_against_existing(monkeypatch, existing)
    assert existing.skymap_url == real_url  # preserved


@pytest.mark.asyncio
async def test_seed_clears_broken_gracedb_skymap_url(monkeypatch):
    """A legacy broken GraceDB URL must still be cleared to None on refresh."""
    existing = GWEvent(
        superevent_id="GW150914",
        far=9.9,
        classification={"STALE": 1.0},
        skymap_url="https://gracedb.ligo.org/apiweb/superevents/GW150914/files/bayestar.multiorder.fits",
        properties={},
    )
    await _run_seed_against_existing(monkeypatch, existing)
    assert existing.skymap_url is None  # cleared


# ---------------------------------------------------------------------------
# GET candidates is a pure read (no compute / insert / commit)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_stored_candidates_is_read_only():
    svc = GWCrossMatchService()

    event = GWEvent(superevent_id="GW150914", properties={})
    cand = GWCandidate(
        superevent_id="GW150914", oid="ZTF1",
        distance_to_peak_arcsec=42.0, probability_in_skymap=0.8,
    )
    obj = Object(
        oid="ZTF1", ra=1.0, dec=2.0, classification="SNIa",
        classification_probability=0.9, n_detections=3, cross_match_name="NGC-x",
    )

    event_result = MagicMock()
    event_result.scalar_one_or_none.return_value = event
    rows_result = MagicMock()
    rows_result.all.return_value = [(cand, obj)]

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[event_result, rows_result])

    candidates = await svc.get_stored_candidates(session, "GW150914")

    assert len(candidates) == 1
    c = candidates[0]
    assert c["oid"] == "ZTF1"
    assert c["distance_arcsec"] == 42.0
    assert c["distance_deg"] == round(42.0 / 3600.0, 3)
    assert c["probability_in_skymap"] == 0.8
    # Pure read: never commits, never inserts.
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_get_stored_candidates_missing_event_raises():
    svc = GWCrossMatchService()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    with pytest.raises(ValueError):
        await svc.get_stored_candidates(session, "GW999999")
    session.commit.assert_not_called()
