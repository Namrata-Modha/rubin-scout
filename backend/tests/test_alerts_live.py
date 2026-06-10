"""
Tests for backend/app/api/alerts_live.py.

Coverage:
  1. _extract_payload_fields() pure function
  2. GET /api/alerts/live
  3. GET /api/alerts/live/classifications
  4. GET /api/live-alerts/live/{external_id}

No real database or HTTP calls — DB sessions are replaced with AsyncMock via
dependency_overrides, following the same pattern as test_api.py.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.alerts_live import _extract_payload_fields
from app.database import get_db
from app.main import app


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def anyio_backend():
    return "asyncio"


# Realistic raw_payload that mirrors what Fink returns after stripping lc blobs
SAMPLE_PAYLOAD = {
    "i:objectId":          "ZTF26aavkpsy",
    "i:ra":                266.6954038,
    "i:dec":               -24.0619362,
    "i:jd":                2461198.9190741,
    "i:magpsf":            18.291412,
    "i:sigmapsf":          0.10690412,
    "i:magzpsci":          26.1,
    "i:diffmaglim":        20.5,
    "i:rb":                0.98,
    "i:drb":               0.9999578,
    "i:classtar":          0.12,
    "i:distnr":            2.34,
    "i:magnr":             19.5,
    "i:ndethist":          6,
    "i:nmtchps":           3,
    "v:classification":    "SN candidate",
    "v:constellation":     "Sagittarius",
    "v:firstdate":         "2026-05-09 09:31:21.999",
    "v:lastdate":          "2026-06-07 10:03:28.002",
    "v:lapse":             29.022,
    "d:snn_sn_vs_all":     0.4815805554,
    "d:snn_snia_vs_nonia": 0.7524275184,
    "d:rf_kn_vs_nonkn":    0.0,
    "d:slsn_score":        0.01,
    "d:cdsxmatch":         "Unknown",
    "d:tns":               "",
    "d:vsx":               "",
    "d:mangrove_2MASS_name":     "",
    "d:mangrove_HyperLEDA_name": "",
    "d:mangrove_lum_dist": None,
}


def _make_alert_row(
    external_id="3444419073915015052",
    classification="SN candidate",
    raw_payload=None,
):
    """Return a MagicMock that looks like an AlertLive ORM row."""
    row = MagicMock()
    row.id = 1
    row.external_id = external_id
    row.ra = 266.695
    row.dec = -24.062
    row.alert_type = "ztf_fink"
    row.classification = classification
    row.classification_score = 0.48
    row.jd = 2461198.919
    row.detected_at = None   # endpoint guards with `if row.detected_at`
    row.ingested_at = None
    row.oid = None
    row.raw_payload = raw_payload if raw_payload is not None else SAMPLE_PAYLOAD
    return row


def _make_class_row(classification, count):
    """Return a MagicMock that looks like a SQLAlchemy Row with named columns."""
    row = MagicMock()
    row.classification = classification
    row.count = count
    return row


# ── 1. _extract_payload_fields — pure function tests ─────────────────────────

class TestExtractPayloadFields:
    """_extract_payload_fields must map Fink raw_payload keys to typed groups."""

    EXPECTED_KEYS = {
        "coords", "photometry", "classification_scores",
        "context", "crossmatch", "host", "object_id",
    }

    def test_returns_all_expected_top_level_keys(self):
        result = _extract_payload_fields(SAMPLE_PAYLOAD)
        assert set(result.keys()) == self.EXPECTED_KEYS

    def test_coords_populated_from_full_payload(self):
        result = _extract_payload_fields(SAMPLE_PAYLOAD)
        assert result["coords"]["ra"]  == pytest.approx(266.6954038)
        assert result["coords"]["dec"] == pytest.approx(-24.0619362)
        assert result["coords"]["jd"]  == pytest.approx(2461198.9190741)

    def test_photometry_populated_from_full_payload(self):
        result = _extract_payload_fields(SAMPLE_PAYLOAD)
        phot = result["photometry"]
        assert phot["magpsf"]   == pytest.approx(18.291412)
        assert phot["sigmapsf"] == pytest.approx(0.10690412)
        assert phot["rb"]       == pytest.approx(0.98)
        assert phot["drb"]      == pytest.approx(0.9999578)

    def test_classification_scores_populated(self):
        result = _extract_payload_fields(SAMPLE_PAYLOAD)
        scores = result["classification_scores"]
        assert scores["snn_sn_vs_all"]    == pytest.approx(0.4815805554)
        assert scores["snn_snia_vs_nonia"] == pytest.approx(0.7524275184)
        assert scores["rf_kn_vs_nonkn"]   == pytest.approx(0.0)
        assert scores["slsn_score"]        == pytest.approx(0.01)

    def test_context_populated(self):
        result = _extract_payload_fields(SAMPLE_PAYLOAD)
        ctx = result["context"]
        assert ctx["constellation"] == "Sagittarius"
        assert ctx["classification"] == "SN candidate"
        assert ctx["lapse"] == pytest.approx(29.022)

    def test_crossmatch_empty_strings_normalised_to_none(self):
        """Empty-string catalog fields (d:tns, d:vsx, etc.) must become None."""
        result = _extract_payload_fields(SAMPLE_PAYLOAD)
        xm = result["crossmatch"]
        assert xm["tns"] is None
        assert xm["vsx"] is None
        assert xm["mangrove_2MASS_name"] is None
        assert xm["mangrove_HyperLEDA_name"] is None

    def test_host_fields_populated(self):
        result = _extract_payload_fields(SAMPLE_PAYLOAD)
        host = result["host"]
        assert host["classtar"]  == pytest.approx(0.12)
        assert host["distnr"]    == pytest.approx(2.34)
        assert host["ndethist"]  == 6
        assert host["nmtchps"]   == 3

    def test_object_id_extracted(self):
        result = _extract_payload_fields(SAMPLE_PAYLOAD)
        assert result["object_id"] == "ZTF26aavkpsy"

    def test_empty_dict_does_not_raise(self):
        """An empty payload must not raise — every field returns None."""
        result = _extract_payload_fields({})
        assert set(result.keys()) == self.EXPECTED_KEYS
        assert result["object_id"] is None
        assert result["coords"]["ra"] is None
        assert result["photometry"]["magpsf"] is None
        assert result["classification_scores"]["snn_sn_vs_all"] is None
        assert result["context"]["constellation"] is None
        assert result["crossmatch"]["cdsxmatch"] is None
        assert result["host"]["classtar"] is None

    def test_missing_optional_fields_return_none_not_keyerror(self):
        """Partial payload must not raise KeyError for any missing field."""
        partial = {"i:ra": 10.0, "i:dec": -5.0}  # most keys absent
        result = _extract_payload_fields(partial)
        # Present fields map through
        assert result["coords"]["ra"] == pytest.approx(10.0)
        # Absent fields are None
        assert result["coords"]["jd"] is None
        assert result["photometry"]["magpsf"] is None
        assert result["host"]["ndethist"] is None


# ── 2. GET /api/alerts/live ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_live_alerts_returns_200_with_correct_shape():
    """/api/alerts/live returns the expected envelope with empty results."""
    # execute() called twice: once for count (scalar_one), once for rows (scalars().all())
    count_result = MagicMock()
    count_result.scalar_one.return_value = 0

    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = []

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[count_result, rows_result])

    async def _mock_db():
        yield session

    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/alerts/live")
        assert response.status_code == 200
        data = response.json()
        assert "alerts" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data
        assert data["alerts"] == []
        assert data["total"] == 0
        assert data["limit"] == 50   # default
        assert data["offset"] == 0   # default
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_live_alerts_classification_filter_accepted():
    """?classification= is accepted and the endpoint returns 200."""
    count_result = MagicMock()
    count_result.scalar_one.return_value = 0
    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = []

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[count_result, rows_result])

    async def _mock_db():
        yield session

    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/alerts/live",
                params={"classification": "SN candidate"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["alerts"] == []
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_live_alerts_returns_alert_rows():
    """/api/alerts/live serialises AlertLive rows into the response."""
    mock_row = _make_alert_row()

    count_result = MagicMock()
    count_result.scalar_one.return_value = 1

    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = [mock_row]

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[count_result, rows_result])

    async def _mock_db():
        yield session

    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/alerts/live")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["alerts"]) == 1
        alert = data["alerts"][0]
        assert alert["external_id"] == "3444419073915015052"
        assert alert["classification"] == "SN candidate"
        assert "ra" in alert
        assert "dec" in alert
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_live_alerts_pagination_params_respected():
    """limit and offset query params are reflected in the response envelope."""
    count_result = MagicMock()
    count_result.scalar_one.return_value = 0
    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = []

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[count_result, rows_result])

    async def _mock_db():
        yield session

    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/alerts/live", params={"limit": 10, "offset": 20}
            )
        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 10
        assert data["offset"] == 20
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── 3. GET /api/alerts/live/classifications ───────────────────────────────────

@pytest.mark.anyio
async def test_live_classifications_returns_200_with_correct_shape():
    """/api/alerts/live/classifications returns classifications array."""
    rows = [
        _make_class_row("SN candidate", 120),
        _make_class_row("Kilonova candidate", 30),
        _make_class_row("SLSN candidate", 15),
    ]
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
            response = await client.get("/api/alerts/live/classifications")
        assert response.status_code == 200
        data = response.json()
        assert "classifications" in data
        assert "total" in data
        assert len(data["classifications"]) == 3
        assert data["total"] == 165  # 120 + 30 + 15
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_live_classifications_items_have_required_keys():
    """Each classification item must have classification and count."""
    rows = [_make_class_row("SN candidate", 50)]
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
            response = await client.get("/api/alerts/live/classifications")
        data = response.json()
        item = data["classifications"][0]
        assert "classification" in item
        assert "count" in item
        assert item["classification"] == "SN candidate"
        assert item["count"] == 50
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_live_classifications_empty_table():
    """Empty alerts_live returns empty classifications list with total 0."""
    result = MagicMock()
    result.all.return_value = []

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    async def _mock_db():
        yield session

    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/alerts/live/classifications")
        data = response.json()
        assert data["classifications"] == []
        assert data["total"] == 0
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── 4. GET /api/live-alerts/live/{external_id} ────────────────────────────────

@pytest.mark.anyio
async def test_live_alert_detail_returns_200_with_payload_fields():
    """/api/live-alerts/live/{id} returns 200 with all payload sections."""
    mock_row = _make_alert_row(raw_payload=SAMPLE_PAYLOAD)

    result = MagicMock()
    result.scalar_one_or_none.return_value = mock_row

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    async def _mock_db():
        yield session

    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/live-alerts/live/3444419073915015052"
            )
        assert response.status_code == 200
        data = response.json()

        # Core row fields
        assert data["external_id"] == "3444419073915015052"
        assert data["classification"] == "SN candidate"

        # All parsed payload sections must be present
        assert "coords" in data
        assert "photometry" in data
        assert "classification_scores" in data
        assert "context" in data
        assert "crossmatch" in data
        assert "host" in data
        assert "object_id" in data

        # Spot-check a few values
        assert data["coords"]["ra"] == pytest.approx(266.6954038)
        assert data["photometry"]["drb"] == pytest.approx(0.9999578)
        assert data["classification_scores"]["snn_sn_vs_all"] == pytest.approx(0.4815805554)
        assert data["context"]["constellation"] == "Sagittarius"
        assert data["object_id"] == "ZTF26aavkpsy"
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_live_alert_detail_returns_404_when_not_found():
    """/api/live-alerts/live/{id} returns 404 for unknown external_id."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    async def _mock_db():
        yield session

    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/live-alerts/live/9999999999999999999"
            )
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_live_alert_detail_empty_raw_payload():
    """Detail endpoint handles a row with null/empty raw_payload gracefully."""
    mock_row = _make_alert_row(raw_payload={})

    result = MagicMock()
    result.scalar_one_or_none.return_value = mock_row

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    async def _mock_db():
        yield session

    app.dependency_overrides[get_db] = _mock_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/live-alerts/live/3444419073915015052"
            )
        assert response.status_code == 200
        data = response.json()
        # All payload sections present but values are None
        assert data["coords"]["ra"] is None
        assert data["photometry"]["magpsf"] is None
        assert data["object_id"] is None
    finally:
        app.dependency_overrides.pop(get_db, None)
