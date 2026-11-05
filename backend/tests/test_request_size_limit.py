"""The request-body cap: declared, streamed, lying, and wired in."""

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

import app.security as security
from app.main import app
from app.security import MAX_REQUEST_BODY_BYTES, RequestSizeLimitMiddleware, limiter

ORIGIN = "http://localhost:5173"          # in the default CORS allow-list


@pytest.fixture(autouse=True)
def _fresh_limiter():
    limiter.reset()
    yield
    limiter.reset()


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def test_the_cap_is_64_kb_and_is_wired_in_as_the_innermost_user_middleware():
    assert MAX_REQUEST_BODY_BYTES == 64 * 1024
    classes = [m.cls for m in app.user_middleware]
    assert RequestSizeLimitMiddleware in classes
    # add_middleware inserts at the front, so the first one added is last: innermost,
    # which keeps its 413 inside CORS and the security headers.
    assert classes[-1] is RequestSizeLimitMiddleware


@pytest.mark.asyncio
async def test_a_declared_oversize_body_is_refused_with_413():
    async with _client() as client:
        resp = await client.post(
            "/api/ask",
            content=b"x" * (MAX_REQUEST_BODY_BYTES + 1),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 413
    assert resp.json()["error"] == "payload_too_large"


@pytest.mark.asyncio
async def test_a_streamed_body_with_no_content_length_is_counted_and_refused():
    async def chunks():
        for _ in range(20):
            yield b"x" * 8192                      # 160 KB, never declared

    async with _client() as client:
        resp = await client.post(
            "/api/ask", content=chunks(), headers={"Content-Type": "application/json"}
        )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_a_normal_chat_request_is_far_below_the_cap():
    async with _client() as client:
        resp = await client.post("/api/ask", json={"question": "I want to die"})
    assert resp.status_code == 200                # answered by the guard, not refused


@pytest.mark.asyncio
async def test_a_body_exactly_at_the_cap_gets_past_the_middleware():
    body = b"x" * MAX_REQUEST_BODY_BYTES
    async with _client() as client:
        resp = await client.post(
            "/api/ask", content=body, headers={"Content-Type": "application/json"}
        )
    assert resp.status_code != 413                # rejected later, as invalid JSON (422)


@pytest.mark.asyncio
async def test_get_requests_are_unaffected():
    async with _client() as client:
        resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_the_413_still_carries_cors_and_security_headers():
    async with _client() as client:
        resp = await client.post(
            "/api/ask",
            content=b"x" * (MAX_REQUEST_BODY_BYTES + 1),
            headers={"Content-Type": "application/json", "Origin": ORIGIN},
        )
    assert resp.status_code == 413
    assert resp.headers["access-control-allow-origin"] == ORIGIN     # the browser can read it
    assert resp.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_a_client_that_lies_about_content_length_is_still_stopped():
    """Declares 10 bytes, sends 100: the received bytes are what count."""

    async def inner(scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body"):
                break

    middleware = RequestSizeLimitMiddleware(inner, max_bytes=50)
    scope = {"type": "http", "headers": [(b"content-length", b"10")]}
    sent = iter([
        {"type": "http.request", "body": b"x" * 60, "more_body": True},
        {"type": "http.request", "body": b"x" * 40, "more_body": False},
    ])

    async def receive():
        return next(sent)

    async def send(message):
        raise AssertionError("no response should be sent by the inner app")

    with pytest.raises(HTTPException) as caught:
        await middleware(scope, receive, send)
    assert caught.value.status_code == 413


@pytest.mark.asyncio
async def test_non_http_scopes_pass_straight_through():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    await RequestSizeLimitMiddleware(inner)({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"]


def test_the_old_one_megabyte_claim_is_gone_from_the_module():
    assert "1_048_576" not in open(security.__file__, encoding="utf-8").read()
