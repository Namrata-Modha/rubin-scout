"""
Unit tests for app/ingestion/fink_service.py.

The real Fink API is never contacted — httpx is fully mocked via
pytest-httpx, and SQLAlchemy sessions are replaced with AsyncMock so
no database is required.

Three tests:
  1. _strip_lc_features removes d:lc_features_g and d:lc_features_r
     and leaves every other field untouched.
  2. A mock Fink HTTP response containing one alert causes exactly one
     INSERT execute call on the session.
  3. When the session returns rowcount=0 (ON CONFLICT DO NOTHING path),
     _insert_alert does not raise and returns 0.
"""

from datetime import timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.fink_service import (
    FINK_API_URL,
    FINK_CLASSES,
    FinkIngestionService,
    _parse_lastdate,
    _pick_score,
    _strip_lc_features,
)

# --------------------------------------------------------------------------- #
# Shared fixture — a realistic single-alert payload from the API audit        #
# --------------------------------------------------------------------------- #

SAMPLE_ALERT = {
    "i:objectId": "ZTF26aavkpsy",
    "i:ra": 266.6954038,
    "i:dec": -24.0619362,
    "i:jd": 2461198.9190741,
    "i:candid": 3444419073915015052,
    "i:fid": 1,
    "i:magpsf": 18.291412,
    "i:sigmapsf": 0.10690412,
    "i:drb": 0.9999578,
    "i:ndethist": 6,
    "v:classification": "SN candidate",
    "v:lastdate": "2026-06-07 10:03:28.002",
    "v:firstdate": "2026-05-09 09:31:21.999",
    "v:lapse": 29.0222916999,
    "v:constellation": "Sagittarius",
    "d:snn_sn_vs_all": 0.4815805554,
    "d:snn_snia_vs_nonia": 0.7524275184,
    "d:rf_kn_vs_nonkn": 0.0,
    "d:rf_snia_vs_nonia": 0.0,
    "d:cdsxmatch": "Unknown",
    "d:tns": "",
    # These two must be stripped before storing
    "d:lc_features_g": "{18.153, 18.159, 0.183, 18.076, 0.226, 0.166}",
    "d:lc_features_r": "[]",
}


# --------------------------------------------------------------------------- #
# Test 1 — _strip_lc_features                                                 #
# --------------------------------------------------------------------------- #

class TestStripLcFeatures:
    """_strip_lc_features must remove exactly the two blob fields."""

    def test_removes_lc_features_g(self):
        result = _strip_lc_features(SAMPLE_ALERT)
        assert "d:lc_features_g" not in result

    def test_removes_lc_features_r(self):
        result = _strip_lc_features(SAMPLE_ALERT)
        assert "d:lc_features_r" not in result

    def test_preserves_all_other_fields(self):
        result = _strip_lc_features(SAMPLE_ALERT)
        expected_keys = set(SAMPLE_ALERT.keys()) - {"d:lc_features_g", "d:lc_features_r"}
        assert set(result.keys()) == expected_keys

    def test_values_unchanged(self):
        result = _strip_lc_features(SAMPLE_ALERT)
        assert result["i:objectId"] == "ZTF26aavkpsy"
        assert result["v:classification"] == "SN candidate"
        assert result["d:snn_sn_vs_all"] == pytest.approx(0.4815805554)

    def test_idempotent_when_fields_absent(self):
        """Should not raise when the strip fields are already missing."""
        alert_without_blobs = {
            k: v for k, v in SAMPLE_ALERT.items()
            if k not in {"d:lc_features_g", "d:lc_features_r"}
        }
        result = _strip_lc_features(alert_without_blobs)
        assert "d:lc_features_g" not in result
        assert "d:lc_features_r" not in result
        assert set(result.keys()) == set(alert_without_blobs.keys())


# --------------------------------------------------------------------------- #
# Test 2 — mock Fink response → one insert attempt                            #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_one_alert_produces_one_insert(httpx_mock):
    """A single-alert mock response must cause exactly one session.execute
    call that carries an INSERT statement."""

    # Mock the Fink HTTP endpoint for every class with a one-alert payload.
    # _fetch_class is called once per FINK_CLASS (4 times); we supply the
    # same response for all so the other 3 classes return the same alert
    # under a different objectId — that's fine for this test.
    httpx_mock.add_response(
        method="POST",
        url=FINK_API_URL,
        json=[SAMPLE_ALERT],
        status_code=200,
    )

    # ------------------------------------------------------------------
    # Test at _fetch_class level first: verify HTTP → list[dict] mapping
    # ------------------------------------------------------------------
    service = FinkIngestionService(api_url=FINK_API_URL)
    alerts = await service._fetch_class("SN candidate")

    assert isinstance(alerts, list)
    assert len(alerts) == 1
    assert alerts[0]["i:objectId"] == SAMPLE_ALERT["i:objectId"]

    # ------------------------------------------------------------------
    # Test at _insert_alert level: one alert → one session.execute call
    # ------------------------------------------------------------------
    mock_result = MagicMock()
    mock_result.rowcount = 1  # simulate a successful insert

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)

    n = await service._insert_alert(
        session=session,
        alert=alerts[0],
        class_name="SN candidate",
        source_id=1,
    )

    assert n == 1
    session.execute.assert_called_once()

    # The statement passed to execute must be an INSERT (not a SELECT/DELETE)
    call_args = session.execute.call_args
    stmt = call_args[0][0]  # first positional arg
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "INSERT" in compiled.upper()


# --------------------------------------------------------------------------- #
# Test 3 — duplicate alert does not raise                                     #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_duplicate_alert_does_not_raise():
    """ON CONFLICT DO NOTHING must never raise an exception.

    When the DB skips a duplicate row it returns rowcount=0.
    _insert_alert must return 0 cleanly — no exception, no partial state.
    """
    mock_result = MagicMock()
    mock_result.rowcount = 0  # DB took the DO NOTHING path

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)

    service = FinkIngestionService()

    # First call — simulates the "duplicate" scenario from the start
    result = await service._insert_alert(
        session=session,
        alert=SAMPLE_ALERT,
        class_name="SN candidate",
        source_id=1,
    )

    assert result == 0  # skipped, not inserted
    session.execute.assert_called_once()

    # Second call with identical data — must also not raise
    session.execute.reset_mock()
    session.execute.return_value = mock_result

    result2 = await service._insert_alert(
        session=session,
        alert=SAMPLE_ALERT,
        class_name="SN candidate",
        source_id=1,
    )

    assert result2 == 0
    session.execute.assert_called_once()


# --------------------------------------------------------------------------- #
# Bonus — non-200 response returns None (not an exception)                   #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_non_200_response_returns_none(httpx_mock):
    """A non-200 from Fink must return None, not raise."""
    httpx_mock.add_response(
        method="POST",
        url=FINK_API_URL,
        status_code=503,
        text="Service Unavailable",
    )

    service = FinkIngestionService(api_url=FINK_API_URL)
    result = await service._fetch_class("SN candidate")
    assert result is None


# --------------------------------------------------------------------------- #
# _parse_lastdate — both Fink date formats                                    #
# --------------------------------------------------------------------------- #

def test_parse_lastdate_with_milliseconds():
    dt = _parse_lastdate("2026-06-07 10:03:28.002")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 6
    assert dt.day == 7
    assert dt.tzinfo == timezone.utc


def test_parse_lastdate_without_milliseconds():
    dt = _parse_lastdate("2026-06-07 10:03:28")
    assert dt is not None
    assert dt.year == 2026
    assert dt.tzinfo == timezone.utc


def test_parse_lastdate_none_returns_none():
    assert _parse_lastdate(None) is None


def test_parse_lastdate_empty_returns_none():
    assert _parse_lastdate("") is None


# --------------------------------------------------------------------------- #
# _pick_score — correct classifier field per class                            #
# --------------------------------------------------------------------------- #

def test_pick_score_kilonova_uses_rf_kn():
    alert = {"d:rf_kn_vs_nonkn": 0.667, "d:snn_sn_vs_all": 0.123}
    assert _pick_score(alert, "Kilonova candidate") == pytest.approx(0.667)


def test_pick_score_sn_candidate_uses_snn():
    alert = {"d:rf_kn_vs_nonkn": 0.667, "d:snn_sn_vs_all": 0.481}
    assert _pick_score(alert, "SN candidate") == pytest.approx(0.481)


def test_pick_score_missing_field_returns_none():
    assert _pick_score({}, "SN candidate") is None


# --------------------------------------------------------------------------- #
# The 90-day retention DELETE must be gone                                     #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_ingest_issues_no_delete(monkeypatch):
    """A full ingest() run must never execute a DELETE against alerts_live.

    alerts_live is an append-only detection log; the rolling retention DELETE
    that used to run here has been removed.
    """
    service = FinkIngestionService()

    # One alert for the first class, nothing for the rest — no HTTP either way.
    async def fake_fetch(class_name):
        return [SAMPLE_ALERT] if class_name == FINK_CLASSES[0] else []

    monkeypatch.setattr(service, "_fetch_class", fake_fetch)

    # A single mock result serves both _ensure_source (scalar_one_or_none)
    # and _insert_alert (rowcount).
    source = MagicMock()
    source.id = 1
    result = MagicMock()
    result.scalar_one_or_none.return_value = source
    result.rowcount = 1

    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()

    inserted = await service.ingest(session)

    assert inserted == 1
    # Inspect every SQL statement passed to execute — none may be a DELETE.
    for call in session.execute.await_args_list:
        sql = str(call.args[0]).upper()
        assert "DELETE" not in sql
