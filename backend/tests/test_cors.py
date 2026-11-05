"""CORS matches what docs/architecture.md says: listed origins, limited methods and
headers, no credentials."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

ORIGIN = "http://localhost:5173"          # in the default allow-list


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _preflight(method="POST", headers="content-type", origin=ORIGIN):
    return {
        "Origin": origin,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": headers,
    }


@pytest.mark.asyncio
async def test_the_frontends_own_preflight_is_allowed():
    """POST /api/ask with a JSON body: exactly what the chat widget sends."""
    async with _client() as client:
        resp = await client.options("/api/ask", headers=_preflight("POST", "content-type"))
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ORIGIN


@pytest.mark.asyncio
async def test_credentials_are_never_allowed():
    async with _client() as client:
        pre = await client.options("/api/ask", headers=_preflight())
        real = await client.get("/health", headers={"Origin": ORIGIN})
    assert "access-control-allow-credentials" not in pre.headers
    assert "access-control-allow-credentials" not in real.headers
    assert real.headers["access-control-allow-origin"] == ORIGIN


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "DELETE"])
async def test_the_documented_methods_are_allowed(method):
    async with _client() as client:
        resp = await client.options("/api/ask", headers=_preflight(method))
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["PUT", "TRACE"])
async def test_other_methods_are_refused(method):
    async with _client() as client:
        resp = await client.options("/api/ask", headers=_preflight(method))
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_the_admin_key_header_is_allowed_but_arbitrary_headers_are_not():
    async with _client() as client:
        ok = await client.options("/api/ask", headers=_preflight("POST", "content-type, x-api-key"))
        bad = await client.options("/api/ask", headers=_preflight("POST", "content-type, authorization"))
    assert ok.status_code == 200
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_an_unlisted_origin_gets_no_cors_headers():
    async with _client() as client:
        resp = await client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers
