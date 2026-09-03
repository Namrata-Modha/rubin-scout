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
from astropy.time import Time

from app.ingestion.lsst_service import (
    CLUSTER_FETCH_SIZE,
    DEFAULT_LOOKBACK_HOURS,
    LSST_SOURCE_NAME,
    LSST_TAGS,
    MAX_PAGES_PER_TAG,
    MAX_WINDOW_SPAN,
    PAGE_SIZE,
    STALL_THRESHOLD_CYCLES,
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
async def test_fetch_tag_window_cluster_recovery_succeeds_and_continues_paging(
    httpx_mock, caplog
):
    """A full page sharing one timestamp must trigger a targeted recovery
    fetch (_fetch_cluster) rather than immediately giving up. If that
    confirms the true cluster size, pagination continues past it instead
    of retrying the identical window forever (see docs/lsst-ingestion-
    recovery.md for the deterministic-stall bug this fixes)."""
    cluster_mjd = 61235.3751192781
    full_cluster_page = [_alert_at_mjd(i, cluster_mjd) for i in range(PAGE_SIZE)]
    # Recovery fetch confirms the true cluster size -- well under
    # CLUSTER_FETCH_SIZE, so it comes back uncapped.
    recovered_cluster = [_alert_at_mjd(1000 + i, cluster_mjd) for i in range(700)]
    short_page = [_alert_at_mjd(99999, cluster_mjd - 0.001)]

    httpx_mock.add_response(method="GET", json=full_cluster_page, status_code=200)
    httpx_mock.add_response(method="GET", json=recovered_cluster, status_code=200)
    httpx_mock.add_response(method="GET", json=short_page, status_code=200)

    service = _make_service()
    with caplog.at_level("WARNING"):
        alerts, exhausted, reached = await service._fetch_tag_window(
            "most_likely_sn", WINDOW_START_DT, WINDOW_STOP_DT
        )

    assert exhausted is True
    # The recovered 700-alert cluster replaces the original capped page
    # (avoids double-counting the same alerts), plus the final short page.
    assert len(alerts) == 700 + 1
    assert len(httpx_mock.get_requests()) == 3
    assert any(
        "recovered a 700-alert same-timestamp cluster" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_fetch_tag_window_cluster_recovery_fails_when_still_capped(
    httpx_mock, caplog
):
    """If even the generous targeted recovery fetch is itself capped at
    CLUSTER_FETCH_SIZE, the cluster's true size genuinely can't be
    confirmed automatically -- falls back to the original defensive
    not-exhausted behavior, with a loud ERROR, and stops rather than
    looping or guessing."""
    cluster_mjd = 61235.3751192781
    full_cluster_page = [_alert_at_mjd(i, cluster_mjd) for i in range(PAGE_SIZE)]
    still_capped = [_alert_at_mjd(2000 + i, cluster_mjd) for i in range(CLUSTER_FETCH_SIZE)]

    httpx_mock.add_response(method="GET", json=full_cluster_page, status_code=200)
    httpx_mock.add_response(method="GET", json=still_capped, status_code=200)

    service = _make_service()
    with caplog.at_level("ERROR"):
        alerts, exhausted, reached = await service._fetch_tag_window(
            "most_likely_sn", WINDOW_START_DT, WINDOW_STOP_DT
        )

    assert len(alerts) == PAGE_SIZE  # falls back to the original page, not discarded
    assert exhausted is False
    assert reached == pytest.approx(cluster_mjd)
    # Normal page + one recovery attempt, then stop -- no infinite loop.
    assert len(httpx_mock.get_requests()) == 2
    assert any(
        "could not confirm the cluster's true size" in r.message
        for r in caplog.records
    )


# --------------------------------------------------------------------------- #
# _fetch_cluster — targeted same-timestamp recovery fetch                    #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_fetch_cluster_returns_exact_timestamp_subset(httpx_mock):
    """_fetch_cluster must filter the bracket response down to exactly the
    target timestamp -- protects correctness even if a neighboring visit's
    cluster fell inside the +/-2s bracket too (see docs/lsst-ingestion-
    recovery.md for why that's rare, though the filter doesn't rely on it)."""
    cluster_mjd = 61235.3751192781
    neighbor_mjd = cluster_mjd + 0.0005
    mixed = (
        [_alert_at_mjd(i, cluster_mjd) for i in range(10)]
        + [_alert_at_mjd(100 + i, neighbor_mjd) for i in range(3)]
    )
    httpx_mock.add_response(method="GET", json=mixed, status_code=200)

    service = _make_service()
    result = await service._fetch_cluster("most_likely_sn", cluster_mjd)

    assert result is not None
    assert len(result) == 10
    assert all(r["r:midpointMjdTai"] == cluster_mjd for r in result)


@pytest.mark.asyncio
async def test_fetch_cluster_returns_none_when_still_capped(httpx_mock):
    """A recovery fetch that comes back at exactly CLUSTER_FETCH_SIZE means
    the true cluster size still isn't confirmed -- must report None, not a
    truncated (silently wrong) result."""
    cluster_mjd = 61235.3751192781
    capped = [_alert_at_mjd(i, cluster_mjd) for i in range(CLUSTER_FETCH_SIZE)]
    httpx_mock.add_response(method="GET", json=capped, status_code=200)

    service = _make_service()
    result = await service._fetch_cluster("most_likely_sn", cluster_mjd)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_cluster_returns_none_on_http_error(httpx_mock):
    httpx_mock.add_response(method="GET", status_code=500, text="boom")

    service = _make_service()
    result = await service._fetch_cluster("most_likely_sn", 61235.3751192781)
    assert result is None


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
async def test_ingest_marks_partial_when_a_tag_does_not_fully_drain(monkeypatch):
    """If even one tag's _fetch_tag_window reports exhausted=False, the
    whole cycle is marked "partial", not "completed", so the next cycle
    retries the identical window rather than silently skip anything.

    _fetch_tag_window is monkeypatched directly here -- its own pagination
    mechanics are already covered above; this test is only about ingest()'s
    status aggregation across tags."""
    service = _make_service()

    async def fake_window_start(session):
        return WINDOW_START_DT
    monkeypatch.setattr(service, "_get_window_start", fake_window_start)

    async def fake_fetch_tag_window(tag, start_dt, stop_dt):
        if tag == LSST_TAGS[0]:  # first tag never fully drains
            return [SAMPLE_LSST_ALERT], False, Time(start_dt).mjd
        return [SAMPLE_LSST_ALERT], True, Time(start_dt).mjd
    monkeypatch.setattr(service, "_fetch_tag_window", fake_fetch_tag_window)

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


# --------------------------------------------------------------------------- #
# ingest() — MAX_WINDOW_SPAN caps the window regardless of how far behind    #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_ingest_caps_window_span_when_far_behind(monkeypatch):
    """window_stop_dt must never exceed window_start_dt + MAX_WINDOW_SPAN,
    even when the natural gap to "now" is much larger (first run, long
    dormancy, or a sustained partial-retry stall) -- see MAX_WINDOW_SPAN's
    docstring for the bug this fixes."""
    service = _make_service()
    far_past = datetime.now(timezone.utc) - timedelta(days=3)

    async def fake_window_start(session):
        return far_past
    monkeypatch.setattr(service, "_get_window_start", fake_window_start)

    seen_windows = []

    async def fake_fetch_tag_window(tag, start_dt, stop_dt):
        seen_windows.append((start_dt, stop_dt))
        return [], True, Time(start_dt).mjd
    monkeypatch.setattr(service, "_fetch_tag_window", fake_fetch_tag_window)

    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None  # _ensure_source: not seeded
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    await service.ingest(session)

    assert len(seen_windows) == len(LSST_TAGS)
    for start_dt, stop_dt in seen_windows:
        assert start_dt == far_past
        assert stop_dt == far_past + MAX_WINDOW_SPAN  # capped exactly, not "now"
        assert stop_dt - start_dt <= MAX_WINDOW_SPAN


@pytest.mark.asyncio
async def test_ingest_window_stop_is_now_when_caught_up(monkeypatch):
    """When caught up (the last completed run was recent), window_stop_dt
    must still resolve to "now" -- MAX_WINDOW_SPAN is a no-op in the normal
    case, not an unconditional truncation."""
    service = _make_service()
    recent = datetime.now(timezone.utc) - timedelta(minutes=1)

    async def fake_window_start(session):
        return recent
    monkeypatch.setattr(service, "_get_window_start", fake_window_start)

    seen_windows = []

    async def fake_fetch_tag_window(tag, start_dt, stop_dt):
        seen_windows.append((start_dt, stop_dt))
        return [], True, Time(start_dt).mjd
    monkeypatch.setattr(service, "_fetch_tag_window", fake_fetch_tag_window)

    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    before = datetime.now(timezone.utc)
    await service.ingest(session)
    after = datetime.now(timezone.utc)

    assert len(seen_windows) == len(LSST_TAGS)
    for start_dt, stop_dt in seen_windows:
        assert start_dt == recent
        assert before <= stop_dt <= after


# --------------------------------------------------------------------------- #
# A zero-alert night (e.g. the observatory's ongoing dormancy) must not      #
# look like a stall -- verified end-to-end, not assumed.                     #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_ingest_zero_new_alerts_completes_normally(httpx_mock):
    """A real empty-page response for every tag (a genuine zero-alert
    cycle, confirmed live as recently as 2026-08-27 -- see
    docs/lsst-ingestion-recovery.md) must go through the actual
    _fetch_tag_window code path and land on status "completed", not
    "partial". This is what makes check_stall() safe: it only ever fires
    on "partial" rows (see below), and this confirms a real zero-alert
    cycle never produces one."""
    for _ in range(len(LSST_TAGS)):  # one empty page per tag
        httpx_mock.add_response(method="GET", json=[], status_code=200)

    service = _make_service()
    added = []
    session = AsyncMock()
    call_state = {"n": 0}

    async def fake_execute(stmt):
        call_state["n"] += 1
        result = MagicMock()
        if call_state["n"] in (1, 2):  # _get_window_start, _ensure_source
            result.scalar_one_or_none.return_value = None
            return result
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

    assert inserted == 0
    ingestion_logs = [o for o in added if type(o).__name__ == "IngestionLog"]
    assert len(ingestion_logs) == 1
    assert ingestion_logs[0].status == "completed"
    assert ingestion_logs[0].completed_at is not None


# --------------------------------------------------------------------------- #
# check_stall — surfaces stall state for GET /api/ingest/lsst/status         #
# --------------------------------------------------------------------------- #

def _log_row(status: str, window_start: str):
    row = MagicMock()
    row.status = status
    row.query_params = {"window_start": window_start}
    return row


@pytest.mark.asyncio
async def test_check_stall_not_enough_history():
    service = _make_service()
    result = MagicMock()
    result.all.return_value = []
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    status = await service.check_stall(session)
    assert status["stalled"] is False


@pytest.mark.asyncio
async def test_check_stall_not_stalled_when_recent_run_completed():
    service = _make_service()
    rows = [_log_row("completed", "x") for _ in range(STALL_THRESHOLD_CYCLES)]
    result = MagicMock()
    result.all.return_value = rows
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    status = await service.check_stall(session)
    assert status["stalled"] is False


@pytest.mark.asyncio
async def test_check_stall_not_triggered_by_repeated_zero_alert_nights():
    """Several consecutive real zero-alert cycles (as
    test_ingest_zero_new_alerts_completes_normally produces) are all
    "completed", each with a different, advancing window_start -- sustained
    observatory dormancy must never look like a stall."""
    service = _make_service()
    rows = [
        _log_row("completed", f"2026-08-{20 + i:02d}T00:00:00+00:00")
        for i in range(STALL_THRESHOLD_CYCLES)
    ]
    result = MagicMock()
    result.all.return_value = rows
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    status = await service.check_stall(session)
    assert status["stalled"] is False


@pytest.mark.asyncio
async def test_check_stall_detects_identical_window_partial_streak():
    """Only meaningful now that MAX_WINDOW_SPAN keeps window_start/stop
    stable across repeated failures -- see its docstring."""
    service = _make_service()
    rows = [
        _log_row("partial", "2026-07-14T00:00:00+00:00")
        for _ in range(STALL_THRESHOLD_CYCLES)
    ]
    result = MagicMock()
    result.all.return_value = rows
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    status = await service.check_stall(session)
    assert status["stalled"] is True
    assert status["consecutive_partial_cycles"] == STALL_THRESHOLD_CYCLES
    assert status["stuck_window_start"] == "2026-07-14T00:00:00+00:00"


@pytest.mark.asyncio
async def test_check_stall_not_stalled_when_partial_windows_differ():
    """Partial cycles with DIFFERENT window_start values means the cursor
    is genuinely advancing -- not a stall, just normal catch-up progress
    (e.g. multiple cycles draining a backlog in MAX_WINDOW_SPAN-sized
    increments)."""
    service = _make_service()
    rows = [
        _log_row("partial", f"2026-07-1{i}T00:00:00+00:00")
        for i in range(STALL_THRESHOLD_CYCLES)
    ]
    result = MagicMock()
    result.all.return_value = rows
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    status = await service.check_stall(session)
    assert status["stalled"] is False


# --------------------------------------------------------------------------- #
# cursor_position vs completed_at                                              #
#                                                                              #
# lsst_service used to store its resume point in completed_at, which every     #
# other source uses for wall-clock completion time. That made 577 of 578       #
# fink_lsst rows report a negative duration. The cursor now has its own        #
# column and completed_at means one thing everywhere.                          #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_completed_run_records_cursor_and_a_real_completed_at(monkeypatch):
    """A drained window advances cursor_position, and completed_at is a
    genuine timestamp -- specifically NOT the window stop."""
    service = _make_service()

    async def fake_window_start(session):
        return WINDOW_START_DT
    monkeypatch.setattr(service, "_get_window_start", fake_window_start)

    async def fake_fetch_tag_window(tag, start_dt, stop_dt):
        return [], True, None  # nothing to ingest, but fully drained
    monkeypatch.setattr(service, "_fetch_tag_window", fake_fetch_tag_window)

    async def fake_ensure_source(session):
        return 7
    monkeypatch.setattr(service, "_ensure_source", fake_ensure_source)

    added = []
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.add = MagicMock(side_effect=added.append)
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    before = datetime.now(timezone.utc)
    await service.ingest(session)
    after = datetime.now(timezone.utc)

    log = [o for o in added if type(o).__name__ == "IngestionLog"][0]
    assert log.status == "completed"

    # The cursor is the window stop, in its own column.
    assert log.cursor_position is not None
    assert log.cursor_position >= WINDOW_START_DT

    # completed_at is wall-clock, and must not have been handed the cursor.
    assert before <= log.completed_at <= after
    assert log.completed_at != log.cursor_position

    # The regression that started all this: completed_at bounded by real
    # wall-clock time is what makes a duration non-negative. (started_at is
    # populated by the ORM column default at flush, which a mocked session
    # never performs, so it is not asserted on here.)


@pytest.mark.asyncio
async def test_partial_run_leaves_cursor_null_but_still_timestamps(monkeypatch):
    """A window that did not fully drain must not advance the cursor, yet
    still records a real completed_at like every other source."""
    service = _make_service()

    async def fake_window_start(session):
        return WINDOW_START_DT
    monkeypatch.setattr(service, "_get_window_start", fake_window_start)

    async def fake_fetch_tag_window(tag, start_dt, stop_dt):
        return [], False, None  # never drains
    monkeypatch.setattr(service, "_fetch_tag_window", fake_fetch_tag_window)

    async def fake_ensure_source(session):
        return 7
    monkeypatch.setattr(service, "_ensure_source", fake_ensure_source)

    added = []
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.add = MagicMock(side_effect=added.append)
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    before = datetime.now(timezone.utc)
    await service.ingest(session)
    after = datetime.now(timezone.utc)

    log = [o for o in added if type(o).__name__ == "IngestionLog"][0]
    assert log.status == "partial"
    assert log.cursor_position is None  # window will be retried, not skipped
    assert before <= log.completed_at <= after


@pytest.mark.asyncio
async def test_get_window_start_reads_cursor_position_not_completed_at():
    """The resume point must come from cursor_position.

    Asserted on the emitted SQL rather than the return value, because a mock
    returning one datetime cannot distinguish which column was selected --
    and reading the wrong one is exactly the bug being fixed.
    """
    service = _make_service()
    cursor = datetime(2026, 7, 10, 3, 0, 0, tzinfo=timezone.utc)
    result = MagicMock()
    result.scalar_one_or_none.return_value = cursor

    captured = {}

    async def capture(stmt):
        captured["sql"] = str(stmt)
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=capture)

    start = await service._get_window_start(session)

    assert start == cursor
    sql = captured["sql"]
    assert "cursor_position" in sql
    # completed_at must not appear anywhere -- not selected, not filtered on,
    # not ordered by.
    assert "completed_at" not in sql
