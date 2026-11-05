"""
Security middleware and utilities.

Provides:
- Rate limiting (IP-based using slowapi)
- Security headers (OWASP recommended)
- Admin API key validation for write endpoints
- Request size limiting
"""

import ipaddress
import logging
import secrets
from typing import Optional

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Client address (what the rate limiter keys on)
# ---------------------------------------------------------------------------
def client_ip(request: Request) -> str:
    """The address to rate-limit this request under.

    ``X-Forwarded-For`` is just a header: anyone can send their own, and each
    proxy APPENDS the address it saw connecting to it. So the only entries a
    client cannot forge are the right-most ones added by proxies we run or
    trust, and the leftmost entry -- which many stacks (including uvicorn
    trusting ``*``) happily use -- is exactly the one a client controls.

    ``settings.trusted_proxy_hops`` is how many right-most entries belong to
    trusted proxies. With N hops the client is the Nth entry from the right:

    * 0 (default): ignore the header entirely and use the socket peer. Nothing
      a client sends can change its key.
    * 1: the last entry, i.e. the address our one proxy saw. A client-supplied
      ``X-Forwarded-For: 1.2.3.4`` arrives as ``1.2.3.4, <real>`` and we read
      ``<real>``.

    If the header is shorter than N (the request did not come through the
    proxy), or the chosen entry is not an IP address, fall back to the socket
    peer rather than trust it.

    uvicorn must NOT also rewrite ``request.client`` from this header, or the
    peer is already attacker-controlled: start it with ``--no-proxy-headers``.
    """
    peer = request.client.host if request.client and request.client.host else "127.0.0.1"
    hops = settings.trusted_proxy_hops
    if hops <= 0:
        return peer

    header = request.headers.get("x-forwarded-for", "")
    entries = [e.strip() for e in header.split(",") if e.strip()]
    if len(entries) < hops:
        return peer
    candidate = entries[-hops]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return peer


# ---------------------------------------------------------------------------
# Rate Limiter (IP-based)
# ---------------------------------------------------------------------------
# Defaults: 60 requests/minute for reads, 10/minute for writes.
# Free tier friendly, prevents abuse without blocking normal usage.
limiter = Limiter(
    key_func=client_ip,
    default_limits=["60/minute"],
    storage_uri="memory://",  # In-memory store, resets on restart. Fine for single-instance.
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Return a clean 429 response when rate limit is hit."""
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "detail": "Too many requests. Please slow down.",
            "retry_after": exc.detail,
        },
        headers={"Retry-After": str(60)},
    )


# ---------------------------------------------------------------------------
# Security Headers Middleware (OWASP)
# ---------------------------------------------------------------------------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds OWASP-recommended security headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # XSS protection (legacy browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Prevent referrer leakage
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Restrict permissions
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

        # Remove server identification
        if "server" in response.headers:
            del response.headers["server"]

        return response


# ---------------------------------------------------------------------------
# Admin API Key Protection (for write/seed endpoints)
# ---------------------------------------------------------------------------
# The admin key is set via ADMIN_API_KEY env var.
# If not set, admin endpoints are disabled in production
# and open in development (for convenience).
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_admin_key(
    request: Request,
    api_key: Optional[str] = Security(api_key_header),
):
    """
    Dependency that protects write endpoints with an API key.

    - In development (APP_ENV=development): all requests pass through.
    - In production: requires a valid X-API-Key header.
    - If ADMIN_API_KEY is not configured in production, returns 503.
    """
    if settings.app_env == "development":
        logger.warning(
            "Admin endpoint accessed without key validation "
            "(app_env=development). Ensure APP_ENV=production on Render."
        )
        return True

    admin_key = settings.admin_api_key
    if not admin_key:
        raise HTTPException(
            status_code=503,
            detail="Admin API key not configured. Set ADMIN_API_KEY environment variable.",
        )

    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing X-API-Key header. This endpoint requires admin access.",
        )

    # Constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(api_key, admin_key):
        logger.warning(f"Invalid admin API key attempt from {client_ip(request)}")
        raise HTTPException(
            status_code=403,
            detail="Invalid API key.",
        )

    return True


# ---------------------------------------------------------------------------
# Request Size Limiting Middleware
# ---------------------------------------------------------------------------
MAX_REQUEST_BODY_BYTES = 1_048_576  # 1 MB


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects requests with bodies larger than 1 MB."""

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_REQUEST_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"error": "payload_too_large", "detail": "Request body exceeds 1 MB limit."},
            )
        return await call_next(request)
