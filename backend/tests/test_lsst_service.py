"""
Unit tests for app/ingestion/lsst_service.py.

The real Fink/LSST API is never contacted for these tests — httpx is fully
mocked via pytest-httpx, and SQLAlchemy sessions are replaced with AsyncMock
so no database is required. SAMPLE_LSST_ALERT is trimmed from a real, live
alert pulled from https://api.lsst.fink-portal.org/api/v1/tags during the
investigation that led to this module (see lsst_service.py's docstring for
the full field comparison against ZTF).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.lsst_service import (
    DEFAULT_LOOKBACK_HOURS,
    LSST_SOURCE_NAME,
    LSST_TAGS,
    MAX_PAGES_PER_TAG,
    PAGE_SIZE,
    LsstFinkIngestionService,
    _extract_cats_score,
    _is_valid_lsst_alert,
    _mjd_to_datetime,
    _mjd_to_jd,
)

# --------------------------------------------------------------------------- #
# Shared fixture — trimmed from a real, live LSST alert                       #
# --------------------------------------------------------------------------- #

SAMPLE_LSST_ALERT = {
    "r:diaObjectId": 170587117485293571,
    "r:diaSourceId": 170666304474710105,
    "r:ra": 62.1275130736,
    "r:dec": -48.5188143359,
    "r:midpointMjdTai": 61235.4191836794,
    "r:band": "i",
    "r:snr": 61.19352,
    "r:psfFlux": -6577.8823,
    "r:psfFluxErr": 734.8993,
    "r:reliability": 0.5290438,
    "r:reliabilityVersion": "0.3",
    "f:clf_cats_class": 11,
    "f:clf_cats_score": 0.9792307,
    "f:clf_earlySNIa_score": -1.0,
    "f:clf_snnSnVsOthers_score": 0.77453464,
    "f:fink_broker_version": "5.0rc0",
    "f:xm_simbad_otype": "Fail",
    "f:xm_tns_fullname": None,
}


def _make_service():
    return LsstFinkIngestionService()


# --------------------------------------------------------------------------- #
# Pure helpers                                                                 #
# --------------------------------------------------------------------------- #

class TestIsValidLsstAlert:
    def test_valid_alert_passes(self):
        assert _is_valid_lsst_alert(SAMPLE_LSST_ALERT) is True

    def test_missing_dia_object_id_fails(self):
        alert = {k: v for k, v in SAMPLE_LSST_ALERT.items() if k != "r:diaObjectId"}
        assert _is_valid_lsst_alert(alert) is False

    def test_missing_dia_source_id_fails(self):
        alert = {k: v for k, v in SAMPLE_LSST_ALERT.items() if k != "r:diaSourceId"}
        assert _is_valid_lsst_alert(alert) is False

    def test_missing_ra_fails(self):
        alert = {k: v for k, v in SAMPLE_LSST_ALERT.items() if k != "r:ra"}
        assert _is_valid_lsst_alert(alert) is False

    def test_missing_dec_fails(self):
        alert = {k: v for k, v in SAMPLE_LSST_ALERT.items() if k != "r:dec"}
        assert _is_valid_lsst_alert(alert) is False


def test_mjd_to_jd_is_exact_offset():
    """JD = MJD + 2400000.5, exact arithmetic -- not an approximation."""
    assert _mjd_to_jd(61235.4191836794) == pytest.approx(61235.4191836794 + 2400000.5)


def test_mjd_to_datetime_matches_known_real_value():
    """Cross-checked against the real alert's own timestamp (2026-07-14, per
    the live investigation that established this alert is from that night)."""
    dt = _mjd_to_datetime(61235.4191836794)
    assert dt.year == 2026
    assert dt.month == 7
    assert dt.day == 14
    assert dt.tzinfo == timezone.utc


class TestExtractCatsScore:
    def test_present_score_parsed_as_float(self):
        assert _extract_cats_score(SAMPLE_LSST_ALERT) == pytest.approx(0.9792307)

    def test_missing_score_returns_none(self):
        alert = {k: v for k, v in SAMPLE_LSST_ALERT.items() if k != "f:clf_cats_score"}
        assert _extract_cats_score(alert) is None

    def test_unparseable_score_returns_none(self):
        alert = {**SAMPLE_LSST_ALERT, "f:clf_cats_score": "not-a-number"}
        assert _extract_cats_score(alert) is None


# --------------------------------------------------------------------------- #
# _insert_alert — INSERT construction, source disambiguation, dedup key       #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_insert_alert_uses_dia_source_id_as_external_id():
    """diaSourceId (the per-detection identifier) is the dedup key, the LSST
    analogue of ZTF's candid -- one row per detection, not per object."""
    service = _make_service()
    mock_result = MagicMock()
    mock_result.rowcount = 1
    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)

    n = await service._insert_alert(
        session=session, alert=SAMPLE_LSST_ALERT, tag="most_likely_sn", source_id=42,
    )

    assert n == 1
    session.execute.assert_called_once()
    stmt = session.execute.call_args[0][0]
    compiled = stmt.compile()
    assert "INSERT" in str(compiled).upper()
    params = compiled.params
    assert params["external_id"] == str(SAMPLE_LSST_ALERT["r:diaSourceId"])
    assert params["source_id"] == 42


@pytest.mark.asyncio
async def test_insert_alert_classification_is_the_matched_tag():
    """No single-label classification field exists on LSST's schema (see
    module docstring) -- the matched tag is stored as the classification,
    since it's the most honest label actually available."""
    service = _make_service()
    mock_result = MagicMock()
    mock_result.rowcount = 1
    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)

    await service._insert_alert(
        session=session, alert=SAMPLE_LSST_ALERT, tag="sn_near_galaxy_candidate", source_id=1,
    )

    stmt = session.execute.call_args[0][0]
    params = stmt.compile().params
    assert params["classification"] == "sn_near_galaxy_candidate"


@pytest.mark.asyncio
async def test_insert_alert_duplicate_does_not_raise():
    """ON CONFLICT DO NOTHING must never raise; rowcount 0 means duplicate."""
    service = _make_service()
    mock_result = MagicMock()
    mock_result.rowcount = 0
    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)

    n = await service._insert_alert(
        session=session, alert=SAMPLE_LSST_ALERT, tag="most_likely_sn", source_id=1,
    )
    assert n == 0


# --------------------------------------------------------------------------- #
# _ensure_source — LSST gets its own alert_sources row, distinct from ZTF     #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_ensure_source_seeds_fink_lsst_not_fink_ztf():
    service = _make_service()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None  # not seeded yet
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    added = []
    session.add = MagicMock(side_effect=added.append)
    session.flush = AsyncMock()

    async def _fake_flush():
        if added:
            added[-1].id = 99
    session.flush.side_effect = _fake_flush

    source_id = await service._ensure_source(session)

    assert len(added) == 1
    assert added[0].name == LSST_SOURCE_NAME
    assert added[0].name != "fink_ztf"
    assert source_id == 99


# --------------------------------------------------------------------------- #
# _get_window_start — real date-window cursor, not a fixed count              #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_get_window_start_resumes_from_last_completed_run():
    service = _make_service()
    last_completed = datetime(2026, 7, 10, 3, 0, 0, tzinfo=timezone.utc)
    result = MagicMock()
    result.scalar_one_or_none.return_value = last_completed
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    start = await service._get_window_start(session)
    assert start == last_completed


@pytest.mark.asyncio
async def test_get_window_start_falls_back_to_default_lookback_on_first_run():
    service = _make_service()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None  # no prior completed run
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    before = datetime.now(timezone.utc)
    start = await service._get_window_start(session)
    after = datetime.now(timezone.utc)

    expected_earliest = before - timedelta(hours=DEFAULT_LOOKBACK_HOURS)
    expected_latest = after - timedelta(hours=DEFAULT_LOOKBACK_HOURS)
    assert expected_earliest <= start <= expected_latest


# --------------------------------------------------------------------------- #
# _fetch_page — HTTP layer, mocked                                            #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_fetch_page_success(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        json=[SAMPLE_LSST_ALERT],
        status_code=200,
    )
    service = _make_service()
    result = await service._fetch_page("most_likely_sn", "2026-07-14 08:00:00.000000", "2026-07-14 10:00:00.000000")
    assert result == [SAMPLE_LSST_ALERT]


@pytest.mark.asyncio
async def test_fetch_page_non_200_returns_none(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        status_code=500,
        text='{"message": "Internal Server Error"}',
    )
    service = _make_service()
    result = await service._fetch_page("most_likely_sn", "2026-07-14 08:00:00.000000", "2026-07-14 10:00:00.000000")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_page_unexpected_shape_returns_none(httpx_mock):
    """A dict instead of a list (e.g. an error payload) must not be treated
    as a valid alert list."""
    httpx_mock.add_response(
        method="GET",
        json={"error": "unexpected"},
        status_code=200,
    )
    service = _make_service()
    result = await service._fetch_page("most_likely_sn", "2026-07-14 08:00:00.000000", "2026-07-14 10:00:00.000000")
    assert result is None


# --------------------------------------------------------------------------- #
# _fetch_tag_window — cursor pagination: the core fix for LSST's volume      #
#                                                                              #
# Confirmed live (2026-07-31) the API returns alerts DESCENDING by            #
# r:midpointMjdTai (newest first), not ascending -- these tests exercise the  #
# corrected backward walk (start_dt fixed, stopdate shrinks), matching that   #
# confirmed real behavior. See lsst_service.py's module docstring for the    #
# live evidence this design is based on.                                     #
# --------------------------------------------------------------------------- #

def _alert_at_mjd(dia_source_id: int, mjd: float) -> dict:
    return {**SAMPLE_LSST_ALERT, "r:diaSourceId": dia_source_id, "r:midpointMjdTai": mjd}


# mjd 61235.0 == 2026-07-14 00:00:00 UTC and 61236.0 == 2026-07-15 00:00:00
# UTC (cross-checked against test_mjd_to_datetime_matches_known_real_value
# above) -- using real, MJD-consistent bounds keeps every synthetic alert
# timestamp below unambiguously comparable against the window floor.
WINDOW_START_DT = _mjd_to_datetime(61235.0)
WINDOW_STOP_DT = _mjd_to_datetime(61236.0)


@pytest.mark.asyncio
async def test_fetch_tag_window_short_page_is_exhausted(httpx_mock):
    """A single page shorter than PAGE_SIZE means the window is fully
    drained -- no further pages are requested."""
    httpx_mock.add_response(
        method="GET",
        # Descending, newest first -- matches the confirmed real ordering.
        json=[_alert_at_mjd(2, 61235.6), _alert_at_mjd(1, 61235.5)],
        status_code=200,
    )
    service = _make_service()
    alerts, exhausted, reached = await service._fetch_tag_window(
        "most_likely_sn", WINDOW_START_DT, WINDOW_STOP_DT
    )
    assert len(alerts) == 2
    assert exhausted is True


@pytest.mark.asyncio
async def test_fetch_tag_window_full_page_advances_cursor_and_fetches_next(httpx_mock):
    """A full page (== PAGE_SIZE) is NOT assumed to be the end -- the query's
    stopdate shrinks to the OLDEST alert's own timestamp in the page (the
    last one, since results are descending) and a follow-up page is
    fetched, walking backward -- proving this is real pagination in the
    confirmed descending direction, not a single fixed-n fetch."""
    full_page = [_alert_at_mjd(i, 61235.9 - i * 0.0001) for i in range(PAGE_SIZE)]
    short_page = [_alert_at_mjd(99999, 61235.9 - PAGE_SIZE * 0.0001 - 0.001)]

    httpx_mock.add_response(method="GET", json=full_page, status_code=200)
    httpx_mock.add_response(method="GET", json=short_page, status_code=200)

    service = _make_service()
    alerts, exhausted, reached = await service._fetch_tag_window(
        "most_likely_sn", WINDOW_START_DT, WINDOW_STOP_DT
    )

    assert len(alerts) == PAGE_SIZE + 1  # both pages collected
    assert exhausted is True
    # Two distinct GET requests were actually made -- real pagination.
    requests = httpx_mock.get_requests()
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_fetch_tag_window_hits_safety_cap_does_not_claim_exhausted(httpx_mock):
    """If every page comes back full all the way to MAX_PAGES_PER_TAG, the
    window is NOT fully drained -- exhausted must be False, so the ingest()
    caller knows not to advance its cursor past this point."""
    def _full_page(offset):
        return [_alert_at_mjd(i, 61235.9 - (offset + i) * 0.00001) for i in range(PAGE_SIZE)]

    for page_i in range(MAX_PAGES_PER_TAG):
        httpx_mock.add_response(
            method="GET", json=_full_page(page_i * PAGE_SIZE), status_code=200,
        )

    service = _make_service()
    alerts, exhausted, reached = await service._fetch_tag_window(
        "most_likely_sn", WINDOW_START_DT, WINDOW_STOP_DT
    )

    assert exhausted is False
    assert len(alerts) == PAGE_SIZE * MAX_PAGES_PER_TAG
    # Cursor genuinely retreated toward start_dt, just didn't finish.
    assert 61235.0 < reached < 61235.9


@pytest.mark.asyncio
async def test_fetch_tag_window_http_error_mid_pagination_is_not_exhausted(httpx_mock):
    """An HTTP error on a later page must not be silently treated as
    "window complete" -- the alerts already collected are kept, but
    exhausted is False so the cursor doesn't skip ahead."""
    full_page = [_alert_at_mjd(i, 61235.9 - i * 0.0001) for i in range(PAGE_SIZE)]
    httpx_mock.add_response(method="GET", json=full_page, status_code=200)
    httpx_mock.add_response(method="GET", status_code=500, text="boom")

    service = _make_service()
    alerts, exhausted, reached = await service._fetch_tag_window(
        "most_likely_sn", WINDOW_START_DT, WINDOW_STOP_DT
    )

    assert exhausted is False
    assert len(alerts) == PAGE_SIZE  # first page's alerts are not discarded


@pytest.mark.asyncio
async def test_fetch_tag_window_first_page_http_error_returns_none(httpx_mock):
    """If the very first page fails outright with nothing collected yet,
    alerts is None (matches fink_service's convention: caller treats this
    as a hard failure for this tag)."""
    httpx_mock.add_response(method="GET", status_code=500, text="boom")
    service = _make_service()
    alerts, exhausted, reached = await service._fetch_tag_window(
        "most_likely_sn", WINDOW_START_DT, WINDOW_STOP_DT
    )
    assert alerts is None
    assert exhausted is False


@pytest.mark.asyncio
async def test_fetch_tag_window_full_page_all_same_timestamp_is_not_exhausted(
    httpx_mock, caplog
):
    """A full page where EVERY alert shares the exact same r:midpointMjdTai
    is the confirmed-live cluster-overflow scenario (460 alerts sharing one
    exact timestamp, observed for real against the 2026-07-14 window) -- the
    true cluster size may exceed PAGE_SIZE, so shrinking stopdate to it could
    silently drop whatever didn't fit in this page. This must NOT be treated
    as exhausted, must stop rather than loop, and must log loudly (ERROR),
    not guess."""
    cluster_mjd = 61235.3751192781
    full_cluster_page = [_alert_at_mjd(i, cluster_mjd) for i in range(PAGE_SIZE)]
    httpx_mock.add_response(method="GET", json=full_cluster_page, status_code=200)

    service = _make_service()
    with caplog.at_level("ERROR"):
        alerts, exhausted, reached = await service._fetch_tag_window(
            "most_likely_sn", WINDOW_START_DT, WINDOW_STOP_DT
        )

    assert len(alerts) == PAGE_SIZE  # collected, not discarded
    assert exhausted is False
    assert reached == pytest.approx(cluster_mjd)
    # Only one request was made -- it stops instead of looping on the stall.
    assert len(httpx_mock.get_requests()) == 1
    assert any("all share r:midpointMjdTai" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# ingest() — full cycle, mocked HTTP + mocked session                        #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_ingest_inserts_alerts_and_advances_cursor_when_exhausted(httpx_mock):
    """A full, successful cycle across every configured tag: each tag
    returns one short (exhausted) page, so the log's completed_at should be
    the window's real stop time and status "completed" -- the normal,
    fully-caught-up case."""
    for _ in range(len(LSST_TAGS)):  # one short (exhausted) page per tag
        httpx_mock.add_response(
            method="GET", json=[SAMPLE_LSST_ALERT], status_code=200,
        )

    service = _make_service()

    # Session mock: source lookup (None -> seed), then per-tag inserts.
    added = []

    session = AsyncMock()
    call_state = {"n": 0}

    async def fake_execute(stmt):
        call_state["n"] += 1
        result = MagicMock()
        # 1st call: _get_window_start's select -> no prior run
        if call_state["n"] == 1:
            result.scalar_one_or_none.return_value = None
            return result
        # 2nd call: _ensure_source's select -> not seeded
        if call_state["n"] == 2:
            result.scalar_one_or_none.return_value = None
            return result
        # Remaining calls: one INSERT statement per tag
        result.rowcount = 1
        return result

    session.execute = AsyncMock(side_effect=fake_execute)

    def _track_add(obj):
        added.append(obj)
        if type(obj).__name__ == "AlertSource":
            obj.id = 7

    session.add = MagicMock(side_effect=_track_add)
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    inserted = await service.ingest(session)

    assert inserted == len(LSST_TAGS)  # one per tag
    ingestion_logs = [o for o in added if type(o).__name__ == "IngestionLog"]
    assert len(ingestion_logs) == 1
    assert ingestion_logs[0].source == LSST_SOURCE_NAME
    assert ingestion_logs[0].status == "completed"
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_ingest_marks_partial_when_a_tag_does_not_fully_drain(httpx_mock, monkeypatch):
    """If even one tag fails to fully drain its window (here: it hits
    MAX_PAGES_PER_TAG), the whole cycle is marked "partial", not
    "completed" -- and completed_at is NOT set to the window's real stop
    time. _get_window_start only ever considers status == "completed" rows,
    so this makes the next cycle retry the identical window from scratch
    rather than silently skip whatever this run didn't confirm was fully
    ingested."""
    service = _make_service()

    # Pin the window so synthetic alert timestamps stay unambiguously
    # comparable against start_dt (see WINDOW_START_DT/STOP_DT above),
    # instead of depending on the real wall-clock "now".
    async def fake_window_start(session):
        return WINDOW_START_DT
    monkeypatch.setattr(service, "_get_window_start", fake_window_start)

    def _full_page(offset):
        return [_alert_at_mjd(i, 61235.9 - (offset + i) * 0.00001) for i in range(PAGE_SIZE)]

    # First tag (most_likely_sn, first in LSST_TAGS) never fully drains.
    for page_i in range(MAX_PAGES_PER_TAG):
        httpx_mock.add_response(
            method="GET", json=_full_page(page_i * PAGE_SIZE), status_code=200,
        )
    # Every other tag drains in one short page.
    for _ in range(len(LSST_TAGS) - 1):
        httpx_mock.add_response(
            method="GET", json=[SAMPLE_LSST_ALERT], status_code=200,
        )

    added = []
    session = AsyncMock()
    call_state = {"n": 0}

    async def fake_execute(stmt):
        call_state["n"] += 1
        result = MagicMock()
        if call_state["n"] == 1:  # _ensure_source's select -> not seeded
            result.scalar_one_or_none.return_value = None
            return result
        result.rowcount = 1  # every INSERT
        return result

    session.execute = AsyncMock(side_effect=fake_execute)

    def _track_add(obj):
        added.append(obj)
        if type(obj).__name__ == "AlertSource":
            obj.id = 7

    session.add = MagicMock(side_effect=_track_add)
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    await service.ingest(session)

    ingestion_logs = [o for o in added if type(o).__name__ == "IngestionLog"]
    assert len(ingestion_logs) == 1
    assert ingestion_logs[0].status == "partial"
    assert ingestion_logs[0].completed_at is not None
    session.commit.assert_awaited()
