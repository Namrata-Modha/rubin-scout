"""
Rubin Scout -- FastAPI Application (Security Hardened).

Security measures:
- Rate limiting (60 req/min reads, 10 req/min writes) via slowapi
- OWASP security headers on all responses
- Admin API key required for write endpoints in production
- Request body size limit (1 MB)
- CORS restricted to configured origins only
- Swagger docs disabled in production unless explicitly enabled
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import get_settings
from app.security import (
    limiter,
    rate_limit_exceeded_handler,
    SecurityHeadersMiddleware,
    RequestSizeLimitMiddleware,
)
from app.api.alerts import router as alerts_router
from app.api.subscriptions import router as subscriptions_router
from app.api.gw import router as gw_router

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("Rubin Scout starting up")
    logger.info(f"Environment: {settings.app_env}")
    logger.info(f"CORS origins: {settings.cors_origin_list}")
    # SECURITY: Never log database URL or secrets
    logger.info(f"Admin API key configured: {'yes' if settings.admin_api_key else 'no (open in dev)'}")
    yield
    logger.info("Rubin Scout shutting down")


# SECURITY: Disable interactive docs in production unless explicitly enabled
# Docs are useful for development but expose API surface in production.
is_production = settings.app_env == "production"

app = FastAPI(
    title="Rubin Scout",
    description=(
        "Filtered, enriched transient alerts from the Vera C. Rubin Observatory and ZTF. "
        "Connects to ALeRCE and Pitt-Google alert brokers, cross-matches with SIMBAD/NED, "
        "and serves alerts through REST API."
    ),
    version="0.1.0",
    docs_url=None if is_production else "/docs",
    redoc_url=None if is_production else "/redoc",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware stack (order matters: last added = first executed)
# ---------------------------------------------------------------------------

# SECURITY: Request size limit (1 MB max body)
app.add_middleware(RequestSizeLimitMiddleware)

# SECURITY: OWASP security headers
app.add_middleware(SecurityHeadersMiddleware)

# SECURITY: CORS restricted to configured origins only
# In production, only your Vercel frontend URL is allowed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,  # No cookies needed, tightened from True
    allow_methods=["GET", "POST", "PATCH", "DELETE"],  # Tightened from ["*"]
    allow_headers=["Content-Type", "X-API-Key"],  # Tightened from ["*"]
)

# SECURITY: Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
app.include_router(alerts_router)
app.include_router(subscriptions_router)
app.include_router(gw_router)


@app.get("/", tags=["health"])
async def root():
    return {
        "name": "Rubin Scout",
        "version": "0.1.0",
        "status": "operational",
    }


@app.get("/health", tags=["health"])
async def health_check():
    """Health check for monitoring and load balancers."""
    return {"status": "healthy"}
