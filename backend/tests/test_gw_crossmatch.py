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
    _classify_significance,
    _is_broken_skymap_url,
    fetch_gwosc_events,
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


# ---------------------------------------------------------------------------
# _classify_significance — GWOSC catalog.shortName -> significance tier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tag,expected", [
    ("GWTC-1-confident", "confident"),
    ("GWTC-2", "confident"),
    ("GWTC-2.1-confident", "confident"),
    ("GWTC-3-confident", "confident"),
    ("GWTC-4.1", "confident"),
    ("GWTC-5.0", "confident"),
    ("GWTC-1-marginal", "marginal"),
    ("GWTC-2.1-marginal", "marginal"),
    ("GWTC-3-marginal", "marginal"),
    ("O3_IMBH_marginal", "marginal"),
    ("O1_O2-Preliminary", "preliminary"),
    ("O3_Discovery_Papers", "preliminary"),
    ("O4_Discovery_Papers", "preliminary"),
    ("IAS-O3a", "excluded"),
    ("Initial_LIGO_Virgo", "excluded"),
    ("GWTC-2.1-auxiliary", "excluded"),
    ("SomeFutureCatalogGWOSCAddsLater", "unknown"),
    (None, "unknown"),
])
def test_classify_significance(tag, expected):
    assert _classify_significance(tag) == expected


# ---------------------------------------------------------------------------
# fetch_gwosc_events — excluded-tier events never reach the returned list,
# and every surviving event carries its significance tier + raw catalog tag.
# ---------------------------------------------------------------------------

def _synthetic_gwosc_event(common_name: str, catalog_tag: str, version: int = 1) -> dict:
    return {
        "commonName": common_name,
        "version": version,
        "catalog.shortName": catalog_tag,
        "GPS": 1187008882.4,
        "far": 1e-7,
        "luminosity_distance": 40.0,
        "mass_1_source": 30.0,
        "mass_2_source": 25.0,
    }


@pytest.mark.asyncio
async def test_fetch_gwosc_events_excludes_non_lvk_and_non_detection(httpx_mock):
    """IAS-O3a (third-party) and Initial_LIGO_Virgo (GWOSC: zero detections)
    must never appear in the returned list; every other tier is kept and
    tagged with its significance."""
    payload = {
        "events": {
            "GWX-CONFIDENT-v1": _synthetic_gwosc_event("GWX-CONFIDENT", "GWTC-3-confident"),
            "GWX-MARGINAL-v1": _synthetic_gwosc_event("GWX-MARGINAL", "GWTC-1-marginal"),
            "GWX-PRELIM-v1": _synthetic_gwosc_event("GWX-PRELIM", "O4_Discovery_Papers"),
            "GWX-UNKNOWN-v1": _synthetic_gwosc_event("GWX-UNKNOWN", "SomeFutureCatalog"),
            "170817-v1": _synthetic_gwosc_event("blind_injection", "Initial_LIGO_Virgo"),
            "IAS1-v1": _synthetic_gwosc_event("GWX-IAS", "IAS-O3a"),
        }
    }
    httpx_mock.add_response(
        method="GET",
        url="https://gwosc.org/eventapi/json/allevents/",
        json=payload,
        status_code=200,
    )

    result = await fetch_gwosc_events()

    by_id = {r["superevent_id"]: r for r in result}
    assert "blind_injection" not in by_id  # Initial_LIGO_Virgo: excluded
    assert "GWX-IAS" not in by_id           # IAS-O3a: excluded (third-party)
    assert len(result) == 4

    assert by_id["GWX-CONFIDENT"]["properties"]["significance"] == "confident"
    assert by_id["GWX-CONFIDENT"]["properties"]["catalog"] == "GWTC-3-confident"
    assert by_id["GWX-MARGINAL"]["properties"]["significance"] == "marginal"
    assert by_id["GWX-PRELIM"]["properties"]["significance"] == "preliminary"
    assert by_id["GWX-UNKNOWN"]["properties"]["significance"] == "unknown"
    assert by_id["GWX-UNKNOWN"]["properties"]["catalog"] == "SomeFutureCatalog"


# ---------------------------------------------------------------------------
# get_significance_counts — live tally used by GET /api/gw/stats
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_significance_counts_tallies_by_tier():
    svc = GWCrossMatchService()
    rows = [
        ({"significance": "confident"},),
        ({"significance": "confident"},),
        ({"significance": "marginal"},),
        ({"significance": "preliminary"},),
        ({},),          # ingested before significance tracking existed
        (None,),        # properties column itself is NULL
    ]
    result = MagicMock()
    result.all.return_value = rows
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    counts = await svc.get_significance_counts(session)

    assert counts == {
        "confident": 2,
        "marginal": 1,
        "preliminary": 1,
        "unclassified": 2,
    }


@pytest.mark.asyncio
async def test_gw_stats_route_uses_significance_counts(monkeypatch):
    """GET /api/gw/stats reports a live confident_count derived from
    get_significance_counts, not a hardcoded number."""
    from httpx import ASGITransport, AsyncClient

    from app.api import gw as gw_api
    from app.database import get_db
    from app.main import app

    async def fake_counts(session):
        return {"confident": 391, "marginal": 27, "preliminary": 1, "unknown": 5}

    async def _mock_db():
        yield AsyncMock()

    monkeypatch.setattr(gw_api.gw_service, "get_significance_counts", fake_counts)
    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/gw/stats")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert body["confident_count"] == 391
    assert body["total"] == 391 + 27 + 1 + 5
    assert body["by_significance"]["marginal"] == 27


# ---------------------------------------------------------------------------
# get_all_events — significance + catalog exposed per event (feeds both
# GET /api/gw/events and GET /api/gw/events/{id}, which filters the same list)
# ---------------------------------------------------------------------------

def _events_result(events: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = events
    return result


def _empty_candidates_result() -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    return result


def _count_result(n: int) -> MagicMock:
    """Mock result for the COUNT(*) query get_all_events now runs first."""
    result = MagicMock()
    result.scalar_one.return_value = n
    return result


@pytest.mark.asyncio
async def test_get_all_events_includes_significance_and_catalog():
    """A normally-ingested event surfaces its significance tier and raw
    GWOSC catalog tag, matching the aggregate reported by GET /api/gw/stats."""
    svc = GWCrossMatchService()
    evt = GWEvent(
        superevent_id="GW231123_135430",
        event_time=datetime(2023, 11, 23, tzinfo=timezone.utc),
        classification={"BBH": 1.0},
        properties={"significance": "confident", "catalog": "GWTC-5.0"},
    )
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[_count_result(1), _events_result([evt]), _empty_candidates_result()]
    )

    output, total = await svc.get_all_events(session)

    assert len(output) == 1
    assert total == 1
    assert output[0]["significance"] == "confident"
    assert output[0]["catalog"] == "GWTC-5.0"


@pytest.mark.asyncio
async def test_get_all_events_unclassified_fallback_for_legacy_rows():
    """A row ingested before significance tracking existed (no key in
    properties at all) must report "unclassified" — never defaulted to a
    real tier — matching the convention in get_significance_counts."""
    svc = GWCrossMatchService()
    evt = GWEvent(
        superevent_id="GW150914",
        event_time=datetime(2015, 9, 14, tzinfo=timezone.utc),
        classification={"BBH": 1.0},
        properties={"ra_center": None, "dec_center": None},  # no significance/catalog keys
    )
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[_count_result(1), _events_result([evt]), _empty_candidates_result()]
    )

    output, total = await svc.get_all_events(session)

    assert total == 1
    assert output[0]["significance"] == "unclassified"
    assert output[0]["catalog"] is None


@pytest.mark.asyncio
async def test_get_all_events_null_properties_unclassified_fallback():
    """properties itself can be NULL (not just missing keys) — must not raise."""
    svc = GWCrossMatchService()
    evt = GWEvent(
        superevent_id="GW170817",
        event_time=datetime(2017, 8, 17, tzinfo=timezone.utc),
        classification={"BNS": 1.0},
        properties=None,
    )
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[_count_result(1), _events_result([evt]), _empty_candidates_result()]
    )

    output, total = await svc.get_all_events(session)

    assert total == 1
    assert output[0]["significance"] == "unclassified"
    assert output[0]["catalog"] is None


# ---------------------------------------------------------------------------
# API-level: both GET /api/gw/events and GET /api/gw/events/{id} surface the
# same significance/catalog fields, since the single-event route filters the
# list returned by get_all_events rather than building its own dict.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_events_list_route_includes_significance(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.api import gw as gw_api
    from app.database import get_db
    from app.main import app

    async def fake_get_all_events(session, significance=None, limit=None, offset=0):
        events = [{
            "superevent_id": "GW231123_135430",
            "event_time": "2023-11-23T13:54:30+00:00",
            "significance": "confident",
            "catalog": "GWTC-5.0",
        }]
        return events, len(events)

    async def _mock_db():
        yield AsyncMock()

    monkeypatch.setattr(gw_api.gw_service, "get_all_events", fake_get_all_events)
    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/gw/events")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    event = response.json()["events"][0]
    assert event["significance"] == "confident"
    assert event["catalog"] == "GWTC-5.0"


@pytest.mark.asyncio
async def test_single_event_route_includes_significance(monkeypatch):
    """GET /api/gw/events/{id} filters get_all_events' list, so it must
    surface the same significance/catalog fields for the matched event."""
    from httpx import ASGITransport, AsyncClient

    from app.api import gw as gw_api
    from app.database import get_db
    from app.main import app

    async def fake_get_all_events(session):
        events = [{
            "superevent_id": "GW150914",
            "event_time": "2015-09-14T09:50:45+00:00",
            "significance": "unclassified",
            "catalog": None,
        }]
        return events, len(events)

    async def _mock_db():
        yield AsyncMock()

    monkeypatch.setattr(gw_api.gw_service, "get_all_events", fake_get_all_events)
    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/gw/events/GW150914")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert body["significance"] == "unclassified"
    assert body["catalog"] is None


# ---------------------------------------------------------------------------
# Server-side significance filtering — GET /api/gw/events?significance=...
# ---------------------------------------------------------------------------

def _capturing_session(n_calls_beyond_events=0):
    """AsyncMock session whose execute() records every query passed to it.

    get_all_events issues, in order: the COUNT(*) query, then the paginated
    GWEvent select, then one GWCandidate count query per returned event. Both
    the COUNT and the GWEvent select carry the identical significance WHERE
    clause, so captured[0] (the COUNT query) is sufficient for asserting the
    filter predicate even though it isn't the events select itself.
    """
    captured = []

    async def fake_execute(query):
        captured.append(query)
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=fake_execute)
    return session, captured


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["confident", "marginal", "preliminary", "unknown"])
async def test_get_all_events_filters_by_named_tier_at_sql_level(tier):
    """Each real tier value produces a WHERE clause equating
    properties.significance to that tier — filtering happens in SQL, before
    any pagination in the route, not by fetching everything and discarding."""
    svc = GWCrossMatchService()
    session, captured = _capturing_session()

    await svc.get_all_events(session, significance=tier)

    compiled = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
    assert f"= '{tier}'" in compiled
    assert "IS NULL" not in compiled


@pytest.mark.asyncio
async def test_get_all_events_unclassified_filters_via_is_null():
    """"unclassified" filters via IS NULL, not string equality — this is what
    correctly matches both a NULL properties column and a properties dict
    with no "significance" key (Postgres ->> propagates NULL through both)."""
    svc = GWCrossMatchService()
    session, captured = _capturing_session()

    await svc.get_all_events(session, significance="unclassified")

    compiled = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
    assert "IS NULL" in compiled
    assert "= 'unclassified'" not in compiled


@pytest.mark.asyncio
async def test_get_all_events_no_significance_arg_is_unfiltered():
    """The default (no significance argument) must not add any WHERE clause
    on properties.significance — existing callers see identical behavior."""
    svc = GWCrossMatchService()
    session, captured = _capturing_session()

    await svc.get_all_events(session)

    compiled = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
    assert "significance" not in compiled
    assert "WHERE" not in compiled.upper()


@pytest.mark.asyncio
async def test_events_route_passes_through_service_total_and_page(monkeypatch):
    """GET /api/gw/events must return exactly what get_all_events reports —
    no route-side len()-based total, no route-side re-slicing. get_all_events
    now does real SQL pagination itself, so the route is a thin pass-through:
    it forwards limit/offset/significance in and returns (events, total) out
    unmodified."""
    from httpx import ASGITransport, AsyncClient

    from app.api import gw as gw_api
    from app.database import get_db
    from app.main import app

    captured_args = []

    async def fake_get_all_events(session, significance=None, limit=None, offset=0):
        captured_args.append(
            {"significance": significance, "limit": limit, "offset": offset}
        )
        # Simulate the last page of a 446-row filtered set: only 3 events on
        # this page, but 446 matching rows overall. If the route recomputed
        # total as len(events) or re-sliced the page itself, this would fail.
        events = [
            {"superevent_id": f"GW{i}", "event_time": None,
             "significance": "marginal", "catalog": "GWTC-1-marginal"}
            for i in range(3)
        ]
        return events, 446

    async def _mock_db():
        yield AsyncMock()

    monkeypatch.setattr(gw_api.gw_service, "get_all_events", fake_get_all_events)
    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/gw/events",
                params={"significance": "marginal", "limit": 20, "offset": 440},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    # limit/offset/significance were forwarded to the service, not consumed
    # by the route itself.
    assert captured_args == [{"significance": "marginal", "limit": 20, "offset": 440}]
    assert body["total"] == 446           # from the service, NOT len(events)
    assert len(body["events"]) == 3       # exactly what the service returned, not re-sliced


# ---------------------------------------------------------------------------
# get_all_events — real SQL pagination: separate COUNT query + LIMIT/OFFSET,
# not fetch-everything-then-slice-in-Python.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_all_events_total_comes_from_count_query_not_page_length():
    """total must come from the COUNT(*) query's own result, not len(events)
    — proven by mocking a COUNT result that differs from the number of event
    rows actually returned, exactly as a non-final page would look."""
    svc = GWCrossMatchService()
    evt = GWEvent(
        superevent_id="GW1", event_time=datetime(2020, 1, 1, tzinfo=timezone.utc),
        classification={"BBH": 1.0}, properties={"significance": "confident"},
    )
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[_count_result(360), _events_result([evt]), _empty_candidates_result()]
    )

    events, total = await svc.get_all_events(session, significance="confident", limit=1, offset=0)

    assert total == 360  # from the COUNT query, not len(events) == 1
    assert len(events) == 1


@pytest.mark.asyncio
async def test_get_all_events_limit_offset_produce_real_sql_pagination():
    """Passing limit/offset produces a LIMIT/OFFSET clause on the events
    SELECT (the second query issued) — real SQL pagination, not an in-memory
    slice of a fully-fetched list. The COUNT query never carries LIMIT/OFFSET."""
    svc = GWCrossMatchService()
    session, captured = _capturing_session()

    await svc.get_all_events(session, limit=20, offset=40)

    events_query_sql = str(captured[1].compile(compile_kwargs={"literal_binds": True}))
    assert "LIMIT 20" in events_query_sql
    assert "OFFSET 40" in events_query_sql

    count_query_sql = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
    assert "LIMIT" not in count_query_sql.upper()


@pytest.mark.asyncio
async def test_get_all_events_no_limit_omits_sql_limit_clause():
    """limit=None (the single-event route's use case, which must search
    across every matching row rather than one page) must not add a LIMIT
    clause at all."""
    svc = GWCrossMatchService()
    session, captured = _capturing_session()

    await svc.get_all_events(session)

    events_query_sql = str(captured[1].compile(compile_kwargs={"literal_binds": True}))
    assert "LIMIT" not in events_query_sql.upper()
    assert "OFFSET" not in events_query_sql.upper()


@pytest.mark.asyncio
async def test_get_all_events_candidate_count_only_runs_for_returned_page():
    """The per-event GWCandidate count query (an existing N+1 pattern,
    unchanged here) must only execute once per event actually returned on
    the page — not once per row in the full filtered set. With limit=2 and
    a COUNT of 500 matching rows overall, exactly 2 candidate-count queries
    run, proving the loop iterates the page, not the filtered total."""
    svc = GWCrossMatchService()
    evts = [
        GWEvent(superevent_id=f"GW{i}", event_time=datetime(2020, 1, i + 1, tzinfo=timezone.utc),
                classification={"BBH": 1.0}, properties={"significance": "confident"})
        for i in range(2)
    ]
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _count_result(500),          # COUNT: 500 matching rows total
            _events_result(evts),        # events SELECT: only the 2-row page
            _empty_candidates_result(),  # candidate count for evts[0]
            _empty_candidates_result(),  # candidate count for evts[1]
        ]
    )

    events, total = await svc.get_all_events(
        session, significance="confident", limit=2, offset=0
    )

    assert total == 500
    assert len(events) == 2
    # COUNT + events SELECT + 2 candidate counts == 4, never 500 + 2.
    assert session.execute.await_count == 4


@pytest.mark.asyncio
async def test_events_route_rejects_invalid_significance(monkeypatch):
    """An unrecognized significance value is rejected with 400, not silently
    ignored (which would return the full unfiltered list under a filter the
    caller thought was applied)."""
    from httpx import ASGITransport, AsyncClient

    from app.api import gw as gw_api
    from app.database import get_db
    from app.main import app

    called = []

    async def fake_get_all_events(session, significance=None):
        called.append(significance)
        return []

    async def _mock_db():
        yield AsyncMock()

    monkeypatch.setattr(gw_api.gw_service, "get_all_events", fake_get_all_events)
    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/gw/events", params={"significance": "extremely_confident"}
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 400
    assert "extremely_confident" in response.json()["detail"]
    # Validation must short-circuit before ever calling the service.
    assert called == []


# ---------------------------------------------------------------------------
# POST /api/gw/seed — the existing manual trigger for seed_gw_events. No new
# route was added: this endpoint already exists and already does exactly
# what a "POST /api/ingest/gw/trigger" would (require_admin_key-gated,
# calls seed_gw_events, returns a count) — see the deliverable report.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_seed_route_calls_service_and_returns_its_result(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.api import gw as gw_api
    from app.database import get_db
    from app.main import app

    calls = []

    async def fake_seed_gw_events(session):
        calls.append(session)
        return 12

    async def _mock_db():
        yield AsyncMock()

    monkeypatch.setattr(gw_api.gw_service, "seed_gw_events", fake_seed_gw_events)
    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/gw/seed")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert len(calls) == 1  # seed_gw_events was actually invoked
    body = response.json()
    assert body["status"] == "ok"
    assert body["events_seeded"] == 12  # the route returns the service's own result
