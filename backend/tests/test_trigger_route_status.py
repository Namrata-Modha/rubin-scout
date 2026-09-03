"""
Admin trigger routes must report what the run actually did.

Every one of these routes used to return a hardcoded ``{"status": "ok"}``.
None of the ingestion services raise on failure -- they catch, record the
failure on their IngestionLog row, and return 0 -- so "the HTTP call did not
raise" carried no information. The headline case: a Fink run that failed
outright and a healthy run that simply found nothing new both produced a
byte-identical ``{"status": "ok", "alerts_inserted": 0}``.

Each route is covered twice: a successful underlying run still reports
success, and a failed one now reports the failure in both the status code
and the body.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.models import IngestionLog

UPSTREAM_FAILURE_CODE = 502


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """These routes are limited to 5/minute, and this module exercises some of
    them more than that. The limiter is shared process-wide, so without this a
    later test 429s depending on suite ordering -- nothing to do with the
    behaviour under test. Auth gating is covered in test_admin_auth.py.
    """
    from app.security import limiter

    monkeypatch.setattr(limiter, "enabled", False)


def _run(status="completed", objects_ingested=None, error=None, run_id=101):
    return IngestionLog(
        id=run_id,
        source="test",
        status=status,
        objects_ingested=objects_ingested,
        error_message=error,
    )


def _db_serving(runs):
    """Session whose execute() serves latest_run_id and runs_since."""
    def _execute(_stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = 0
        scalars = MagicMock()
        scalars.all.return_value = runs
        result.scalars.return_value = scalars
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    return db


async def _post(path, runs):
    """POST `path` with admin auth bypassed and the DB serving `runs`."""
    from app.database import get_db
    from app.main import app
    from app.security import require_admin_key

    async def _mock_db():
        yield _db_serving(runs)

    async def _bypass():
        return True

    app.dependency_overrides[get_db] = _mock_db
    app.dependency_overrides[require_admin_key] = _bypass
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_admin_key, None)


def _patch_ingest(monkeypatch, dotted_class, returns=0):
    """Make a service's ingest() succeed silently, returning `returns`.

    Mirrors reality: these services never raise, so the route can only learn
    the outcome from the IngestionLog row.
    """
    module_path, _, cls_name = dotted_class.rpartition(".")
    module = __import__(module_path, fromlist=[cls_name])
    cls = getattr(module, cls_name)

    async def fake_ingest(self, session, *a, **kw):
        return returns

    monkeypatch.setattr(cls, "ingest", fake_ingest)


# --------------------------------------------------------------------------- #
# The five /api/ingest routes, each backed by one IngestionLog row            #
# --------------------------------------------------------------------------- #

INGEST_ROUTES = [
    ("/api/ingest/fink/trigger", "app.ingestion.fink_service.FinkIngestionService", "fink_ztf"),
    ("/api/ingest/lsst/trigger", "app.ingestion.lsst_service.LsstFinkIngestionService", "fink_lsst"),
    ("/api/ingest/chime/trigger", "app.ingestion.chime_service.ChimeFRBIngestionService", "chimefrb_catalog"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,cls,source", INGEST_ROUTES, ids=[r[2] for r in INGEST_ROUTES])
async def test_successful_run_still_reports_success(monkeypatch, path, cls, source):
    _patch_ingest(monkeypatch, cls, returns=7)

    response = await _post(path, [_run("completed")])

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["source"] == source
    assert body["objects_ingested"] == 7
    assert body["error"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("path,cls,source", INGEST_ROUTES, ids=[r[2] for r in INGEST_ROUTES])
async def test_failed_run_reports_the_failure(monkeypatch, path, cls, source):
    """The service still returns 0 without raising -- only the row knows."""
    _patch_ingest(monkeypatch, cls, returns=0)

    response = await _post(
        path, [_run("failed", objects_ingested=0, error="upstream timed out")]
    )

    assert response.status_code == UPSTREAM_FAILURE_CODE
    body = response.json()
    assert body["status"] == "failed"
    assert body["source"] == source
    assert body["error"] == "upstream timed out"
    assert body["run_id"] == 101


@pytest.mark.asyncio
async def test_fink_failure_is_now_distinguishable_from_an_empty_success(monkeypatch):
    """The regression this whole change exists for.

    Both runs insert 0 alerts and neither raises. Before, both returned
    {"status": "ok", "alerts_inserted": 0} -- identical bytes.
    """
    _patch_ingest(monkeypatch, "app.ingestion.fink_service.FinkIngestionService", returns=0)

    empty_success = await _post("/api/ingest/fink/trigger", [_run("completed", 0)])
    real_failure = await _post(
        "/api/ingest/fink/trigger", [_run("failed", 0, "connection reset")]
    )

    assert empty_success.status_code == 200
    assert real_failure.status_code == UPSTREAM_FAILURE_CODE
    assert empty_success.json() != real_failure.json()
    assert empty_success.json()["objects_ingested"] == real_failure.json()["objects_ingested"] == 0


@pytest.mark.asyncio
async def test_run_that_logged_nothing_is_reported_as_failed(monkeypatch):
    """Every service writes its row before doing work, so no row means the
    call died before that -- exactly the pre-2026-09 poisoned-session bug."""
    _patch_ingest(monkeypatch, "app.ingestion.fink_service.FinkIngestionService", returns=0)

    response = await _post("/api/ingest/fink/trigger", [])

    assert response.status_code == UPSTREAM_FAILURE_CODE
    assert response.json()["status"] == "failed"
    assert "no ingestion_log row" in response.json()["error"]


# --------------------------------------------------------------------------- #
# TNS: /daily writes one row, /seed writes one per day                        #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_tns_daily_reports_success(monkeypatch):
    from app.api import ingest as ingest_api

    async def fake(session, *a, **kw):
        return 4
    monkeypatch.setattr(ingest_api.tns_service, "ingest_from_daily_csv", fake)

    response = await _post("/api/ingest/tns/daily", [_run("completed")])

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["objects_ingested"] == 4


@pytest.mark.asyncio
async def test_tns_daily_no_data_is_success_not_failure(monkeypatch):
    """A quiet day with no TNS CSV is a legitimate outcome, not an error --
    this is precisely the distinction the old hardcoded "ok" destroyed."""
    from app.api import ingest as ingest_api

    async def fake(session, *a, **kw):
        return 0
    monkeypatch.setattr(ingest_api.tns_service, "ingest_from_daily_csv", fake)

    response = await _post("/api/ingest/tns/daily", [_run("no_data", 0)])

    assert response.status_code == 200
    assert response.json()["status"] == "no_data"


@pytest.mark.asyncio
async def test_tns_daily_auth_failure_is_surfaced(monkeypatch):
    from app.api import ingest as ingest_api

    async def fake(session, *a, **kw):
        return 0
    monkeypatch.setattr(ingest_api.tns_service, "ingest_from_daily_csv", fake)

    response = await _post("/api/ingest/tns/daily", [_run("auth_failed", 0, "TNS 401")])

    assert response.status_code == UPSTREAM_FAILURE_CODE
    assert response.json()["status"] == "auth_failed"


@pytest.mark.asyncio
async def test_tns_seed_one_bad_day_among_several_is_still_success(monkeypatch):
    """Seeding N days writes N rows. TNS publishes nothing on quiet days, so
    a single non-OK row must not condemn the whole seed."""
    from app.api import ingest as ingest_api

    async def fake(session, days=7):
        return 30
    monkeypatch.setattr(ingest_api.tns_service, "seed_recent_days", fake)

    runs = [_run("completed", 10, run_id=1), _run("no_data", 0, run_id=2),
            _run("failed", 0, "boom", run_id=3)]
    response = await _post("/api/ingest/tns/seed", runs)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["runs"] == 3 and body["runs_ok"] == 2


@pytest.mark.asyncio
async def test_tns_seed_all_days_failing_reports_failure(monkeypatch):
    from app.api import ingest as ingest_api

    async def fake(session, days=7):
        return 0
    monkeypatch.setattr(ingest_api.tns_service, "seed_recent_days", fake)

    runs = [_run("failed", 0, "TNS unreachable", run_id=1),
            _run("failed", 0, None, run_id=2)]
    response = await _post("/api/ingest/tns/seed", runs)

    assert response.status_code == UPSTREAM_FAILURE_CODE
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == "TNS unreachable"
    assert body["runs_ok"] == 0


# --------------------------------------------------------------------------- #
# GW: writes no IngestionLog row at all, so the count is the only signal      #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_gw_seed_reports_success(monkeypatch):
    from app.api import gw as gw_api

    async def fake(session):
        return 12
    monkeypatch.setattr(gw_api.gw_service, "seed_gw_events", fake)

    response = await _post("/api/gw/seed", [])

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["objects_ingested"] == 12
    assert body["run_id"] is None  # seed_gw_events writes no ingestion_log row


@pytest.mark.asyncio
async def test_gw_seed_zero_events_means_gwosc_failed(monkeypatch):
    """Every fetched event is inserted or updated, so 0 can only mean
    fetch_gwosc_events returned nothing -- an upstream failure that used to
    report {"status": "ok", "events_seeded": 0}."""
    from app.api import gw as gw_api

    async def fake(session):
        return 0
    monkeypatch.setattr(gw_api.gw_service, "seed_gw_events", fake)

    response = await _post("/api/gw/seed", [])

    assert response.status_code == UPSTREAM_FAILURE_CODE
    body = response.json()
    assert body["status"] == "failed"
    assert "GWOSC returned no events" in body["error"]
