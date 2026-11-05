"""Which address the rate limiter keys on, and that a client cannot choose it."""

import logging
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

import app.main as main_module
import app.security as security
from app.main import app
from app.security import client_ip, limiter

REPO = Path(__file__).resolve().parents[2]

# Throwaway value that only has to match itself within a test process; never a real key.
FAKE_ADMIN_KEY = "test-only-throwaway-admin-key-not-a-real-secret"


def _request(peer="10.0.0.5", xff=None):
    headers = [] if xff is None else [(b"x-forwarded-for", xff.encode())]
    return Request({"type": "http", "client": (peer, 4242), "headers": headers})


@pytest.fixture
def hops(monkeypatch):
    def set_hops(n):
        monkeypatch.setattr(security.settings, "trusted_proxy_hops", n)
    return set_hops


@pytest.fixture(autouse=True)
def _fresh_limiter():
    limiter.reset()
    yield
    limiter.reset()


# --- the resolution rule ---------------------------------------------------------

def test_by_default_the_header_is_ignored_and_the_socket_peer_is_used(hops):
    hops(0)
    assert client_ip(_request(peer="10.0.0.5", xff="1.2.3.4")) == "10.0.0.5"
    assert client_ip(_request(peer="10.0.0.5")) == "10.0.0.5"


def test_with_one_trusted_hop_the_last_entry_is_the_client(hops):
    hops(1)
    assert client_ip(_request(xff="203.0.113.9")) == "203.0.113.9"


def test_a_client_supplied_leftmost_entry_cannot_change_the_result(hops):
    hops(1)
    # The client sent "9.9.9.9"; the proxy appended the address it really saw.
    assert client_ip(_request(xff="9.9.9.9, 203.0.113.9")) == "203.0.113.9"
    assert client_ip(_request(xff="1.1.1.1, 2.2.2.2, 203.0.113.9")) == "203.0.113.9"


def test_with_two_trusted_hops_the_second_from_the_right_is_the_client(hops):
    hops(2)
    assert client_ip(_request(xff="9.9.9.9, 203.0.113.9, 172.16.0.1")) == "203.0.113.9"


def test_a_header_shorter_than_the_trusted_hops_falls_back_to_the_peer(hops):
    hops(2)
    assert client_ip(_request(peer="10.0.0.5", xff="203.0.113.9")) == "10.0.0.5"
    hops(1)
    assert client_ip(_request(peer="10.0.0.5")) == "10.0.0.5"        # no header at all
    assert client_ip(_request(peer="10.0.0.5", xff="  ,  ")) == "10.0.0.5"


def test_an_entry_that_is_not_an_ip_address_falls_back_to_the_peer(hops):
    hops(1)
    assert client_ip(_request(peer="10.0.0.5", xff="1.2.3.4, not-an-ip")) == "10.0.0.5"
    assert client_ip(_request(peer="10.0.0.5", xff="1.2.3.4, <script>")) == "10.0.0.5"


def test_ipv6_clients_are_normalised(hops):
    hops(1)
    assert client_ip(_request(xff="9.9.9.9, 2001:DB8:0:0:0:0:0:1")) == "2001:db8::1"


# --- end to end through the limiter -----------------------------------------------

async def _ask(xff=None):
    headers = {} if xff is None else {"X-Forwarded-For": xff}
    # A self-harm message is answered by the guard, so no model stack is needed; it
    # still passes through the route's rate limits like any question.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/ask", json={"question": "I want to die"}, headers=headers)


@pytest.mark.asyncio
async def test_rotating_a_spoofed_header_does_not_escape_the_per_minute_limit(hops):
    hops(1)
    statuses = [(await _ask(f"10.99.{i}.1, 203.0.113.9")).status_code for i in range(12)]
    assert statuses[:10] == [200] * 10
    assert statuses[10:] == [429, 429]


@pytest.mark.asyncio
async def test_different_real_clients_get_separate_buckets(hops):
    hops(1)
    for _ in range(10):
        assert (await _ask("1.1.1.1, 203.0.113.9")).status_code == 200
    assert (await _ask("1.1.1.1, 203.0.113.9")).status_code == 429
    assert (await _ask("1.1.1.1, 203.0.113.10")).status_code == 200      # someone else


@pytest.mark.asyncio
async def test_with_the_default_the_spoofed_header_is_not_even_read(hops):
    hops(0)
    statuses = [(await _ask(f"10.99.{i}.1")).status_code for i in range(11)]
    assert statuses[-1] == 429                       # all one peer, header ignored


# --- the diagnostic and the start command -------------------------------------------

@pytest.mark.asyncio
async def test_the_diagnostic_shows_the_resolved_address_not_the_spoof(hops, monkeypatch):
    # The route is admin-only. Configure the real (non-bypass) auth path with a
    # throwaway key, as tests/test_admin_auth.py does, so this does not depend on
    # APP_ENV: CI runs with APP_ENV=test and no ADMIN_API_KEY.
    monkeypatch.setattr(security.settings, "app_env", "production")
    monkeypatch.setattr(security.settings, "admin_api_key", FAKE_ADMIN_KEY)
    hops(1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/health/client-ip",
            headers={
                "X-API-Key": FAKE_ADMIN_KEY,
                "X-Forwarded-For": "9.9.9.9, 198.51.100.7",
            },
        )
    body = resp.json()
    assert resp.status_code == 200
    assert body["resolved_ip"] == "198.51.100.7"
    assert body["trusted_proxy_hops"] == 1
    assert body["x_forwarded_for"] == "9.9.9.9, 198.51.100.7"


def test_uvicorn_is_started_without_proxy_header_rewriting():
    """If uvicorn rewrote request.client from X-Forwarded-For, the peer itself
    would be client-controlled and no hop setting could help."""
    assert "--no-proxy-headers" in (REPO / "render.yaml").read_text(encoding="utf-8")
    assert "--no-proxy-headers" in (REPO / "backend" / "Dockerfile").read_text(encoding="utf-8")


def test_a_wildcard_forwarded_allow_ips_is_warned_about(monkeypatch, caplog):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    with caplog.at_level(logging.WARNING):
        assert main_module.warn_if_forwarded_headers_trusted() is True
    assert "--no-proxy-headers" in caplog.text

    caplog.clear()
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
    assert main_module.warn_if_forwarded_headers_trusted() is False
    assert caplog.text == ""
