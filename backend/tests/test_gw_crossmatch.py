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
)
from app.models.models import GWEvent

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
# Task 1 — cross_match_event must refuse events with no localization
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

    This is the core Task 1 guarantee: a spatially unfiltered candidate list
    is scientifically invalid and must not be produced.
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
# Task 2 / Task 3 — seed_gw_events upsert + null skymap_url
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
    """Task 2: an existing row is refreshed (far/classification/properties),
    Task 3: skymap_url is set to None, and locally computed localisation is
    preserved rather than clobbered by the incoming Nones."""
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
    """Task 3: newly inserted events store skymap_url = None (no wrong URL)."""
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
