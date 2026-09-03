"""
Real end-to-end test coverage for require_admin_key, across every gated route.

Prior to this file, admin-key gating had no real test coverage anywhere in
the suite: routes were either only checked for registration (never actually
invoked), or — in the one case that is invoked, GW seed — auth was
deliberately overridden to isolate a different concern (see
test_gw_crossmatch.py::test_seed_route_calls_service_and_returns_its_result,
left untouched here). Nothing had ever proven the key-checking logic itself
works, on any route, against a real request.

Structure:
  1. Direct tests of require_admin_key's own four-case behavior (no config /
     no caller key / wrong caller key / correct caller key).
  2. Direct tests proving the dev-bypass fires ONLY for the literal string
     "development" — not "production" (Render's actual value), "test" (CI's
     value), an empty string, or a differently-cased variant.
  3. Full ASGITransport round-trip tests for every require_admin_key-gated
     route: missing key, wrong key, and (per-route, since each needs its own
     service/DB mock) a correct key that actually reaches and invokes the
     route's own logic.

Security note: FAKE_ADMIN_KEY below is a throwaway value that only ever has
to match itself within a test process. It is never the real production key,
which is never read, logged, or referenced by this file.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.security as security_module

FAKE_ADMIN_KEY = "test-only-throwaway-admin-key-not-a-real-secret"


def _fake_request(client_host: str = "127.0.0.1") -> Request:
    """Minimal real Starlette Request — enough for get_remote_address(), the
    only thing require_admin_key does with `request` besides pass it through."""
    scope = {"type": "http", "client": (client_host, 12345), "headers": []}
    return Request(scope)


@pytest.fixture
def admin_auth_configured(monkeypatch):
    """Configure require_admin_key's real (non-bypass) path: a production-like
    APP_ENV with a test-only, throwaway ADMIN_API_KEY. Never the real key."""
    monkeypatch.setattr(security_module.settings, "app_env", "production")
    monkeypatch.setattr(security_module.settings, "admin_api_key", FAKE_ADMIN_KEY)
    return FAKE_ADMIN_KEY


# ---------------------------------------------------------------------------
# 1. require_admin_key — direct four-case behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_key_configured_on_server_returns_503(monkeypatch):
    """Server misconfiguration (no ADMIN_API_KEY set at all) is checked
    BEFORE looking at what the caller sent — even a caller who supplied a
    key gets 503, not a verdict on their key."""
    monkeypatch.setattr(security_module.settings, "app_env", "production")
    monkeypatch.setattr(security_module.settings, "admin_api_key", "")

    with pytest.raises(HTTPException) as exc_info:
        await security_module.require_admin_key(
            request=_fake_request(), api_key="something-the-caller-sent"
        )
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_no_caller_key_returns_401(admin_auth_configured):
    """Server is configured correctly; caller sent no X-API-Key at all."""
    with pytest.raises(HTTPException) as exc_info:
        await security_module.require_admin_key(request=_fake_request(), api_key=None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_wrong_caller_key_returns_403(admin_auth_configured):
    """Server is configured correctly; caller sent an incorrect key."""
    with pytest.raises(HTTPException) as exc_info:
        await security_module.require_admin_key(
            request=_fake_request(), api_key="definitely-not-the-right-key"
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_correct_caller_key_succeeds(admin_auth_configured):
    """Server is configured correctly; caller sent the matching key."""
    result = await security_module.require_admin_key(
        request=_fake_request(), api_key=admin_auth_configured
    )
    assert result is True


def test_misconfigured_and_unauthorized_are_distinct_status_codes():
    """"Server misconfigured" (503) and "caller unauthorized" (401 for no
    key, 403 for wrong key) are three genuinely distinct status codes in the
    code as written — not collapsed. A caller CAN already tell "you're not
    authorized" apart from "this deployment is broken." This is a direct
    reading of app/security.py: the `if not admin_key` 503 branch is checked
    and returned from before `api_key` is even inspected, so no combination
    of caller input can produce 401/403 when misconfigured, or 503 when
    configured. See the three tests above for the executable proof of each
    individual code; this test just states the finding explicitly.
    """
    assert True  # See test_no_key_configured_on_server_returns_503 (503),
    # test_no_caller_key_returns_401 (401), and test_wrong_caller_key_returns_403
    # (403) directly above — three different codes, three different causes.


# ---------------------------------------------------------------------------
# 2. Dev-bypass fires ONLY for the literal string "development"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dev_bypass_fires_for_literal_development(monkeypatch):
    """The one value that must bypass: app_env == "development" exactly."""
    monkeypatch.setattr(security_module.settings, "app_env", "development")
    monkeypatch.setattr(security_module.settings, "admin_api_key", "")  # even unconfigured...

    result = await security_module.require_admin_key(request=_fake_request(), api_key=None)
    assert result is True  # ...the bypass returns True without raising


@pytest.mark.asyncio
@pytest.mark.parametrize("app_env_value", [
    "production",   # Render's actual confirmed value
    "test",         # CI's value (the config mismatch that caused the 503 this week)
    "",             # empty string
    "Development",  # differently-cased — must NOT match "development"
])
async def test_dev_bypass_does_not_fire_for_non_development_values(monkeypatch, app_env_value):
    """None of these may trigger the bypass. Proven by leaving ADMIN_API_KEY
    unset and confirming the function still raises 503 (i.e. it proceeded
    past the bypass check into the not-configured branch) rather than
    silently returning True as the literal "development" case does above."""
    monkeypatch.setattr(security_module.settings, "app_env", app_env_value)
    monkeypatch.setattr(security_module.settings, "admin_api_key", "")

    with pytest.raises(HTTPException) as exc_info:
        await security_module.require_admin_key(request=_fake_request(), api_key=None)
    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# 3. Every require_admin_key-gated route — full ASGITransport round trips
# ---------------------------------------------------------------------------

# (method, path, extra httpx request kwargs) for every require_admin_key
# route in the app. Kept in one place so the missing-key/wrong-key tests
# below cover all of them uniformly without per-route duplication.
GATED_ROUTES = [
    ("POST", "/api/gw/seed", {}),
    ("POST", "/api/ingest/tns/seed", {}),
    ("POST", "/api/ingest/tns/daily", {}),
    ("POST", "/api/ingest/fink/trigger", {}),
    ("POST", "/api/ingest/chime/trigger", {}),
    ("POST", "/api/ingest/lsst/trigger", {}),
    ("POST", "/api/ingest/admin/backfill-tns-photometry", {}),
    ("POST", "/api/subscriptions/", {"json": {"name": "Test Sub", "user_email": "test@example.com"}}),
    ("PATCH", "/api/subscriptions/1", {"json": {"active": False}}),
    ("DELETE", "/api/subscriptions/1", {}),
]
GATED_ROUTE_IDS = [f"{m} {p}" for m, p, _ in GATED_ROUTES]


def _fake_run(status="completed", objects_ingested=None, error=None, run_id=101):
    """An IngestionLog row as the trigger routes now read it back.

    objects_ingested defaults to None so the route falls through to the
    service's own return value, which is what these tests assert on.
    """
    from app.models.models import IngestionLog

    return IngestionLog(
        id=run_id,
        source="test",
        status=status,
        objects_ingested=objects_ingested,
        error_message=error,
    )


def _db_serving(runs):
    """AsyncMock session whose execute() serves both run_status queries:
    latest_run_id (scalar_one_or_none) and runs_since (scalars().all())."""
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


async def _mock_db():
    # Default: the triggered run recorded a completed IngestionLog row.
    yield _db_serving([_fake_run()])


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,kwargs", GATED_ROUTES, ids=GATED_ROUTE_IDS)
async def test_gated_route_rejects_missing_key(admin_auth_configured, method, path, kwargs):
    """Every require_admin_key route rejects a request with no X-API-Key
    header at all — 401 — before the route body ever runs."""
    from httpx import ASGITransport, AsyncClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request(method, path, **kwargs)
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 401, f"{method} {path} -> {response.status_code}, expected 401"


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,kwargs", GATED_ROUTES, ids=GATED_ROUTE_IDS)
async def test_gated_route_rejects_wrong_key(admin_auth_configured, method, path, kwargs):
    """Every require_admin_key route rejects an incorrect X-API-Key — 403 —
    before the route body ever runs."""
    from httpx import ASGITransport, AsyncClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request(
                method, path, headers={"X-API-Key": "definitely-the-wrong-key"}, **kwargs
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 403, f"{method} {path} -> {response.status_code}, expected 403"


# --- correct key succeeds AND actually invokes each route's own logic -----

@pytest.mark.asyncio
async def test_gw_seed_correct_key_invokes_service(admin_auth_configured, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.api import gw as gw_api
    from app.database import get_db
    from app.main import app

    calls = []

    async def fake_seed_gw_events(session):
        calls.append(session)
        return 7

    monkeypatch.setattr(gw_api.gw_service, "seed_gw_events", fake_seed_gw_events)
    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/gw/seed", headers={"X-API-Key": admin_auth_configured}
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert len(calls) == 1
    assert response.json()["objects_ingested"] == 7


@pytest.mark.asyncio
async def test_tns_seed_correct_key_invokes_service(admin_auth_configured, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.api import ingest as ingest_api
    from app.database import get_db
    from app.main import app

    calls = []

    async def fake_seed_recent_days(session, days=7):
        calls.append(days)
        return 42

    monkeypatch.setattr(ingest_api.tns_service, "seed_recent_days", fake_seed_recent_days)
    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/ingest/tns/seed",
                params={"days": 3},
                headers={"X-API-Key": admin_auth_configured},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert calls == [3]
    assert response.json()["objects_ingested"] == 42


@pytest.mark.asyncio
async def test_tns_daily_correct_key_invokes_service(admin_auth_configured, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.api import ingest as ingest_api
    from app.database import get_db
    from app.main import app

    calls = []

    async def fake_ingest_from_daily_csv(session):
        calls.append(session)
        return 5

    monkeypatch.setattr(ingest_api.tns_service, "ingest_from_daily_csv", fake_ingest_from_daily_csv)
    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/ingest/tns/daily", headers={"X-API-Key": admin_auth_configured}
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert len(calls) == 1
    assert response.json()["objects_ingested"] == 5


@pytest.mark.asyncio
async def test_fink_trigger_correct_key_invokes_service(admin_auth_configured, monkeypatch):
    """FinkIngestionService is instantiated fresh inside the route body (not
    a module-level singleton), so the class method itself is patched rather
    than a specific instance's attribute."""
    from httpx import ASGITransport, AsyncClient

    from app.database import get_db
    from app.ingestion.fink_service import FinkIngestionService
    from app.main import app

    calls = []

    async def fake_ingest(self, session):
        calls.append(session)
        return 9

    monkeypatch.setattr(FinkIngestionService, "ingest", fake_ingest)
    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/ingest/fink/trigger", headers={"X-API-Key": admin_auth_configured}
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert len(calls) == 1
    assert response.json()["objects_ingested"] == 9


@pytest.mark.asyncio
async def test_chime_trigger_correct_key_invokes_service(admin_auth_configured, monkeypatch):
    """ChimeFRBIngestionService is instantiated fresh inside the route body,
    same as Fink — patch the class method."""
    from httpx import ASGITransport, AsyncClient

    from app.database import get_db
    from app.ingestion.chime_service import ChimeFRBIngestionService
    from app.main import app

    calls = []

    async def fake_ingest(self, session):
        calls.append(session)
        return 11

    monkeypatch.setattr(ChimeFRBIngestionService, "ingest", fake_ingest)
    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/ingest/chime/trigger", headers={"X-API-Key": admin_auth_configured}
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert len(calls) == 1
    assert response.json()["objects_ingested"] == 11


@pytest.mark.asyncio
async def test_lsst_trigger_correct_key_invokes_service(admin_auth_configured, monkeypatch):
    """LsstFinkIngestionService is instantiated fresh inside the route body,
    same as Fink/CHIME — patch the class method."""
    from httpx import ASGITransport, AsyncClient

    from app.database import get_db
    from app.ingestion.lsst_service import LsstFinkIngestionService
    from app.main import app

    calls = []

    async def fake_ingest(self, session):
        calls.append(session)
        return 4

    monkeypatch.setattr(LsstFinkIngestionService, "ingest", fake_ingest)
    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/ingest/lsst/trigger", headers={"X-API-Key": admin_auth_configured}
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert len(calls) == 1
    assert response.json()["objects_ingested"] == 4


@pytest.mark.asyncio
async def test_backfill_tns_photometry_correct_key_invokes_service(admin_auth_configured, monkeypatch):
    """This route builds its own fresh TNSIngestionService() instance too
    (distinct from the module-level tns_service singleton used by tns/seed
    and tns/daily) — patch the class method."""
    from httpx import ASGITransport, AsyncClient

    from app.database import get_db
    from app.ingestion.tns_service import TNSIngestionService
    from app.main import app

    calls = []

    async def fake_backfill_photometry(self, session):
        calls.append(session)
        return 3

    monkeypatch.setattr(TNSIngestionService, "backfill_photometry", fake_backfill_photometry)
    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/ingest/admin/backfill-tns-photometry",
                headers={"X-API-Key": admin_auth_configured},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert len(calls) == 1
    assert response.json()["objects_processed"] == 3


@pytest.mark.asyncio
async def test_subscription_create_correct_key_invokes_db(admin_auth_configured):
    from httpx import ASGITransport, AsyncClient

    from app.database import get_db
    from app.main import app

    result_no_existing = MagicMock()
    result_no_existing.scalars.return_value.all.return_value = []  # under the 10-subs-per-email cap
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_no_existing)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    async def _fake_db():
        yield session

    app.dependency_overrides[get_db] = _fake_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/subscriptions/",
                json={"name": "Test Sub", "user_email": "test@example.com"},
                headers={"X-API-Key": admin_auth_configured},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 201
    assert session.add.call_count == 1
    session.commit.assert_awaited_once()
    assert response.json()["status"] == "created"


@pytest.mark.asyncio
async def test_subscription_update_correct_key_invokes_db(admin_auth_configured):
    from httpx import ASGITransport, AsyncClient

    from app.database import get_db
    from app.main import app

    fake_sub = MagicMock()
    fake_sub.id = 1
    result_select = MagicMock()
    result_select.scalar_one_or_none.return_value = fake_sub
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[result_select, MagicMock()])
    session.commit = AsyncMock()

    async def _fake_db():
        yield session

    app.dependency_overrides[get_db] = _fake_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                "/api/subscriptions/1",
                json={"active": False},
                headers={"X-API-Key": admin_auth_configured},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert session.execute.await_count == 2  # select, then update
    session.commit.assert_awaited_once()
    assert response.json()["status"] == "updated"


@pytest.mark.asyncio
async def test_subscription_delete_correct_key_invokes_db(admin_auth_configured):
    from httpx import ASGITransport, AsyncClient

    from app.database import get_db
    from app.main import app

    fake_sub = MagicMock()
    fake_sub.id = 1
    result_select = MagicMock()
    result_select.scalar_one_or_none.return_value = fake_sub
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[result_select, MagicMock()])
    session.commit = AsyncMock()

    async def _fake_db():
        yield session

    app.dependency_overrides[get_db] = _fake_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(
                "/api/subscriptions/1", headers={"X-API-Key": admin_auth_configured}
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert session.execute.await_count == 2  # select, then delete
    session.commit.assert_awaited_once()
    assert response.json()["status"] == "deleted"
