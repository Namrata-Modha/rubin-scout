"""
Tests for the alerts API endpoints.

Run with: cd backend && python -m pytest tests/ -v
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.main import app

# ── Shared DB mock ────────────────────────────────────────────────────────────

async def _mock_db():
    """Yield a fake AsyncSession that returns empty result sets."""
    session = MagicMock()
    # execute() returns an object whose scalars().all() → []
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    result.scalar_one_or_none.return_value = None
    result.scalar.return_value = 0
    session.execute = AsyncMock(return_value=result)
    session.close = AsyncMock()
    yield session


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_root_endpoint():
    """Root endpoint returns app info."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Rubin Scout"
    assert data["version"] == "0.1.0"
    assert data["status"] == "operational"


@pytest.mark.anyio
async def test_health_check():
    """Health check returns healthy status."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


@pytest.mark.anyio
async def test_ping_is_pure_liveness_no_db_no_lsst_field():
    """GET /api/health/ping must stay unaffected by LSST ingestion status --
    no lsst_ingestion field, no DB dependency override needed, always 200.
    This is the endpoint most likely to double as a platform-level
    liveness check (Render or similar), so it must never fail for a reason
    that restarting the process wouldn't fix. See GET
    /api/ingest/lsst/status for the separate, application-level signal."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health/ping")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "timestamp" in data
    assert "lsst_ingestion" not in data


@pytest.mark.anyio
async def test_lsst_status_returns_200_when_not_stalled():
    """GET /api/ingest/lsst/status is the dedicated, separate endpoint for
    LSST ingestion stall status -- no admin key required, read-only."""
    result = MagicMock()
    result.all.return_value = []  # not enough run history yet -> not stalled
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    async def _mock_db():
        yield session

    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/ingest/lsst/status")
        assert response.status_code == 200
        data = response.json()
        assert data["lsst_ingestion"]["stalled"] is False
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_lsst_status_returns_503_when_stalled():
    """A genuine stall must return 503 here -- so a monitor pointed
    specifically at this path can alert on it, without any risk to
    /api/health/ping's liveness contract."""
    from app.ingestion.lsst_service import STALL_THRESHOLD_CYCLES

    rows = []
    for _ in range(STALL_THRESHOLD_CYCLES):
        row = MagicMock()
        row.status = "partial"
        row.query_params = {"window_start": "2026-07-14T00:00:00+00:00"}
        rows.append(row)

    result = MagicMock()
    result.all.return_value = rows
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    async def _mock_db():
        yield session

    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/ingest/lsst/status")
        assert response.status_code == 503
        data = response.json()
        assert data["lsst_ingestion"]["stalled"] is True
        assert data["lsst_ingestion"]["stuck_window_start"] == "2026-07-14T00:00:00+00:00"
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_recent_alerts_params():
    """Recent alerts endpoint accepts filter parameters."""
    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/alerts/recent",
                params={
                    "classification": "SNIa",
                    "min_probability": 0.8,
                    "hours": 48,
                    "limit": 10,
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert "alerts" in data
        assert data["alerts"] == []
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_cone_search_validation():
    """Cone search validates coordinate ranges."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # RA out of range should fail validation
        response = await client.get(
            "/api/alerts/conesearch/query",
            params={"ra": 999, "dec": 0, "radius": 60},
        )
    assert response.status_code == 422  # Validation error


@pytest.mark.anyio
@pytest.mark.skip(reason="Requires test database setup")
@pytest.mark.anyio
async def test_alert_detail_not_found():
    """Requesting a nonexistent object returns 404 or 500 (no DB)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/alerts/ZTF99zzzzzzz")  # Valid format, doesn't exist
    assert response.status_code in (404, 500)


class TestMJDConversion:
    """Test astronomical time conversions."""

    def test_mjd_to_datetime(self):

        from app.ingestion.alerce_service import mjd_to_datetime

        # MJD 51544.0 = January 1, 2000 00:00 UTC (J2000.0 epoch)
        dt = mjd_to_datetime(51544.0)
        assert dt.year == 2000
        assert dt.month == 1
        assert dt.day == 1
        assert dt.tzinfo is not None  # Should be timezone-aware

    def test_mjd_to_datetime_recent(self):
        from app.ingestion.alerce_service import mjd_to_datetime

        # MJD 60000 ~ February 2023
        dt = mjd_to_datetime(60000.0)
        assert dt.year == 2023
        assert dt.month == 2


class TestFilterMap:
    """Test ZTF filter ID mapping."""

    def test_filter_ids(self):
        from app.ingestion.alerce_service import FILTER_MAP

        assert FILTER_MAP[1] == "g"
        assert FILTER_MAP[2] == "r"
        assert FILTER_MAP[3] == "i"


class TestTargetClasses:
    """Test that we're tracking the right transient classes."""

    def test_supernovae_included(self):
        from app.ingestion.alerce_service import TARGET_CLASSES

        assert "SNIa" in TARGET_CLASSES
        assert "SNII" in TARGET_CLASSES

    def test_kilonova_included(self):
        from app.ingestion.alerce_service import TARGET_CLASSES

        # Kilonovae are critical for GW counterpart searches
        assert "KN" in TARGET_CLASSES

    def test_tde_included(self):
        from app.ingestion.alerce_service import TARGET_CLASSES

        assert "TDE" in TARGET_CLASSES
