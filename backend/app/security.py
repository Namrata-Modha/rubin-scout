"""
Security middleware and utilities.

Provides:
- Rate limiting (IP-based using slowapi)
- Security headers (OWASP recommended)
- Admin API key validation for write endpoints
- Request size limiting
"""

import logging
import secrets
from typing import Optional

from fastapi import Request, HTTPException, Security
from fastapi.security import APIKeyHeader
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Rate Limiter (IP-based)
# ---------------------------------------------------------------------------
# Defaults: 60 requests/minute for reads, 10/minute for writes.
# Free tier friendly, prevents abuse without blocking normal usage.
limiter = Limiter(
    key_func=get_remote_address,
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
        logger.warning(f"Invalid admin API key attempt from {get_remote_address(request)}")
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
