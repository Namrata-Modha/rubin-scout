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

import json
from datetime import timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.ingestion.fink_service import (
    FETCH_MAX_ATTEMPTS,
    FINK_API_URL,
    FINK_CLASSES,
    FinkIngestionService,
    _parse_lastdate,
    _pick_score,
    _scrub_pg_unsafe,
    _strip_lc_features,
)
from app.models.models import IngestionLog

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

class _SavepointCM:
    """Stand-in for AsyncSession.begin_nested()'s async context manager.

    AsyncMock would return a coroutine here, which `async with` rejects, so
    every alert would look like it failed. This mirrors the real return type.
    """

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False  # never suppress -- let ingest() see real failures


def _mock_session() -> AsyncMock:
    """AsyncMock session wired for the savepoint-per-alert insert path."""
    session = AsyncMock()
    session.add = MagicMock()
    session.begin_nested = MagicMock(side_effect=lambda: _SavepointCM())
    return session

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

    session = _mock_session()
    session.execute = AsyncMock(return_value=result)

    inserted = await service.ingest(session)

    assert inserted == 1
    # Inspect every SQL statement passed to execute — none may be a DELETE.
    for call in session.execute.await_args_list:
        sql = str(call.args[0]).upper()
        assert "DELETE" not in sql


# ---------------------------------------------------------------------------
# Session-poisoning regressions.
#
# Catching a per-alert exception is not sufficient on its own: in Postgres a
# failed statement aborts the enclosing transaction, so every later statement
# -- including the final commit -- fails with "current transaction is aborted".
# One bad row would silently cost the entire batch, and the failure handler
# would then be unable to record why.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_one_bad_alert_does_not_poison_the_batch(monkeypatch):
    """A failing insert is rolled back to its savepoint; later alerts survive."""
    service = FinkIngestionService()

    bad = {**SAMPLE_ALERT, "i:objectId": "ZTF_BAD"}
    good = {**SAMPLE_ALERT, "i:objectId": "ZTF_GOOD"}

    async def fake_fetch(class_name):
        return [bad, good] if class_name == FINK_CLASSES[0] else []

    monkeypatch.setattr(service, "_fetch_class", fake_fetch)

    async def flaky_insert(session, alert, class_name, source_id):
        if alert["i:objectId"] == "ZTF_BAD":
            raise RuntimeError("duplicate key value violates unique constraint")
        return 1

    monkeypatch.setattr(service, "_insert_alert", flaky_insert)

    source = MagicMock()
    source.id = 1
    result = MagicMock()
    result.scalar_one_or_none.return_value = source
    session = _mock_session()
    session.execute = AsyncMock(return_value=result)

    inserted = await service.ingest(session)

    # The good alert still landed despite the bad one failing first.
    assert inserted == 1
    # One savepoint per alert attempt, not one for the whole batch.
    assert session.begin_nested.call_count == 2
    # The run committed normally rather than falling into the failure handler.
    assert session.commit.await_count == 1


@pytest.mark.asyncio
async def test_failure_handler_rolls_back_then_records_the_error(monkeypatch):
    """The failure record must be written on a session that is not poisoned.

    The handler rolls back first -- which also discards the flushed "running"
    row -- so it has to insert a fresh IngestionLog carrying the error.
    """
    service = FinkIngestionService()

    async def boom(session):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(service, "_ensure_source", boom)

    session = _mock_session()
    session.execute = AsyncMock(return_value=MagicMock())

    inserted = await service.ingest(session)

    assert inserted == 0
    session.rollback.assert_awaited_once()

    logs = [c.args[0] for c in session.add.call_args_list
            if isinstance(c.args[0], IngestionLog)]
    # The original "running" row, then a replacement recording the failure.
    assert len(logs) == 2
    failure = logs[-1]
    assert failure.status == "failed"
    assert "connection reset by peer" in failure.error_message
    assert failure.completed_at is not None
    # started_at is carried over so the run's real duration is preserved.
    assert failure.started_at == logs[0].started_at


@pytest.mark.asyncio
async def test_failure_record_survives_a_poisoned_session(monkeypatch):
    """Rollback must happen BEFORE the commit, or the record is lost.

    Simulates Postgres refusing every statement until the transaction ends:
    commit fails while the session is poisoned and succeeds once rolled back.
    """
    service = FinkIngestionService()

    async def boom(session):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(service, "_ensure_source", boom)

    session = _mock_session()
    session.execute = AsyncMock(return_value=MagicMock())

    poisoned = {"value": True}

    async def commit():
        if poisoned["value"]:
            raise RuntimeError(
                "current transaction is aborted, commands ignored until "
                "end of transaction block"
            )

    async def rollback():
        poisoned["value"] = False

    session.commit = AsyncMock(side_effect=commit)
    session.rollback = AsyncMock(side_effect=rollback)

    await service.ingest(session)

    # Rollback cleared the poison, so the failure record's commit went through.
    assert poisoned["value"] is False
    session.commit.assert_awaited()


# ---------------------------------------------------------------------------
# _scrub_pg_unsafe
#
# Postgres cannot store a NUL (or a lone UTF-16 surrogate) inside a jsonb
# string: asyncpg raises UntranslatableCharacterError, "unsupported Unicode
# escape sequence", and the whole INSERT is rejected. Fink began serving
# d:blazar_stats_m0/m1/m2 as a lossily-decoded 4-byte float rather than a
# number, so alerts carrying it were being skipped in their entirety.
# ---------------------------------------------------------------------------

# The exact value Fink serves, captured live 2026-09-08.
FINK_MANGLED_BLAZAR = "\ufffd\ufffd\x00\x00"


def test_scrub_removes_the_real_fink_value():
    """Two replacement chars survive; the two trailing NULs do not."""
    assert _scrub_pg_unsafe(FINK_MANGLED_BLAZAR) == "\ufffd\ufffd"


def test_scrub_removes_lone_surrogates():
    assert _scrub_pg_unsafe("a\ud800b\udfffc") == "abc"


def test_scrub_recurses_through_dicts_and_lists():
    payload = {"d:blazar_stats_m0": "x\x00", "nested": {"k": ["a\x00b", "ok"]}}
    assert _scrub_pg_unsafe(payload) == {
        "d:blazar_stats_m0": "x",
        "nested": {"k": ["ab", "ok"]},
    }


def test_scrub_leaves_everything_else_untouched():
    """Numbers, nulls and booleans must not be coerced to strings, and a clean
    string must come back byte-identical."""
    assert _scrub_pg_unsafe(-1.0) == -1.0
    assert _scrub_pg_unsafe(None) is None
    assert _scrub_pg_unsafe(42) == 42
    assert _scrub_pg_unsafe(True) is True
    assert _scrub_pg_unsafe("SN candidate") == "SN candidate"
    # Tab/newline are legal in jsonb and must survive.
    assert _scrub_pg_unsafe("a\tb\nc") == "a\tb\nc"


@pytest.mark.asyncio
async def test_insert_alert_stores_a_scrubbed_payload():
    """The value actually bound into the INSERT carries no NUL.

    Asserted on the statement's parameters rather than the return value: the
    row is what Postgres would have rejected, so that is what must be clean.
    """
    service = FinkIngestionService()
    alert = {
        **SAMPLE_ALERT,
        "d:blazar_stats_m0": FINK_MANGLED_BLAZAR,
        "d:blazar_stats_m1": FINK_MANGLED_BLAZAR,
    }

    captured = {}

    async def capture(stmt):
        captured["stmt"] = stmt
        result = MagicMock()
        result.rowcount = 1
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=capture)

    await service._insert_alert(session, alert, FINK_CLASSES[0], 1)

    params = captured["stmt"].compile().params
    payload = params["raw_payload"]
    assert payload["d:blazar_stats_m0"] == "\ufffd\ufffd"
    assert "\x00" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_alert_with_nul_is_no_longer_skipped(monkeypatch):
    """End to end: an alert Fink mangles must now be ingested, not dropped.

    Before the scrub this raised inside _insert_alert, the savepoint rolled it
    back, and the alert was lost with only a warning.
    """
    service = FinkIngestionService()
    alert = {**SAMPLE_ALERT, "d:blazar_stats_m0": FINK_MANGLED_BLAZAR}

    async def fake_fetch(class_name):
        return [alert] if class_name == FINK_CLASSES[0] else []
    monkeypatch.setattr(service, "_fetch_class", fake_fetch)

    source = MagicMock()
    source.id = 1
    result = MagicMock()
    result.scalar_one_or_none.return_value = source
    result.rowcount = 1
    session = _mock_session()
    session.execute = AsyncMock(return_value=result)

    inserted = await service.ingest(session)

    assert inserted == 1  # not skipped


# ---------------------------------------------------------------------------
# _fetch_class retry
#
# A single 60s timeout used to lose an entire run, and /latests has no date
# window so a failed run's alerts are unrecoverable once they age off the
# latest-N window. Transport failures are now retried; a real HTTP status or
# an unparseable body is not, because re-asking cannot change either.
#
# Backoff is patched out in these tests -- the point under test is the retry
# decision, not tenacity's ability to sleep.
# ---------------------------------------------------------------------------

@pytest.fixture
def no_backoff(monkeypatch):
    """Make tenacity's waits instant so the tests don't actually sleep."""
    import tenacity
    monkeypatch.setattr(tenacity.nap, "sleep", lambda _s: None)

    async def _asleep(_s):
        return None
    monkeypatch.setattr("asyncio.sleep", _asleep)


@pytest.mark.asyncio
async def test_fetch_recovers_after_a_transient_failure(no_backoff, monkeypatch):
    """First attempt times out, second succeeds -- the run keeps its data."""
    service = FinkIngestionService()
    calls = {"n": 0}

    async def flaky_post(self, url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("")
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = [SAMPLE_ALERT]
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", flaky_post)

    result = await service._fetch_class(FINK_CLASSES[0])

    assert result == [SAMPLE_ALERT]   # recovered, not lost
    assert calls["n"] == 2            # exactly one retry was needed


@pytest.mark.asyncio
async def test_fetch_gives_up_cleanly_after_exhausting_attempts(no_backoff, monkeypatch):
    """Every attempt fails: return None rather than raising, so the caller's
    had_http_error path still runs and the row records a failure."""
    service = FinkIngestionService()
    calls = {"n": 0}

    async def always_timeout(self, url, **kw):
        calls["n"] += 1
        raise httpx.ConnectTimeout("")

    monkeypatch.setattr(httpx.AsyncClient, "post", always_timeout)

    result = await service._fetch_class(FINK_CLASSES[0])

    assert result is None
    assert calls["n"] == FETCH_MAX_ATTEMPTS   # tried, and only tried, 3 times


@pytest.mark.asyncio
async def test_a_real_http_status_is_not_retried(no_backoff, monkeypatch):
    """A 404 from Fink is an answer. Asking again three times wastes the
    run's budget without changing it."""
    service = FinkIngestionService()
    calls = {"n": 0}

    async def not_found(self, url, **kw):
        calls["n"] += 1
        response = MagicMock()
        response.status_code = 404
        response.text = "no such class"
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", not_found)

    result = await service._fetch_class(FINK_CLASSES[0])

    assert result is None
    assert calls["n"] == 1  # NOT retried


@pytest.mark.asyncio
async def test_unparseable_body_is_not_retried(no_backoff, monkeypatch):
    """Same reasoning: a malformed body will be malformed again."""
    service = FinkIngestionService()
    calls = {"n": 0}

    async def bad_json(self, url, **kw):
        calls["n"] += 1
        response = MagicMock()
        response.status_code = 200
        response.json.side_effect = ValueError("not json")
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", bad_json)

    result = await service._fetch_class(FINK_CLASSES[0])

    assert result is None
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_healthy_fetch_makes_exactly_one_request(no_backoff, monkeypatch):
    """The normal case must be untouched: one request, no retry machinery
    cost, no sleep before the first attempt."""
    service = FinkIngestionService()
    calls = {"n": 0}

    async def ok(self, url, **kw):
        calls["n"] += 1
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = [SAMPLE_ALERT]
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", ok)

    result = await service._fetch_class(FINK_CLASSES[0])

    assert result == [SAMPLE_ALERT]
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_retry_and_giving_up_are_logged_distinctly(no_backoff, monkeypatch, caplog):
    """A log read must be able to tell "recovered after retry" from
    "exhausted every attempt"."""
    service = FinkIngestionService()

    async def always_timeout(self, url, **kw):
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(httpx.AsyncClient, "post", always_timeout)

    with caplog.at_level("WARNING"):
        await service._fetch_class(FINK_CLASSES[0])

    text = caplog.text
    assert "retrying (attempt 1/3)" in text     # per-attempt warning
    assert "Giving up on Fink class" in text    # distinct final error
    # The exception type is named, not swallowed into an empty string.
    assert "ReadTimeout" in text
