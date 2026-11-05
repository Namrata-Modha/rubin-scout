"""
Rubin Scout FastAPI Application.
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.api import alerts, alerts_live, ask, gw, ingest, subscriptions
from app.api.alerts_live import detail_router as alerts_live_detail_router
from app.api.images import router as images_router
from app.config import get_settings
from app.ingestion.scheduler import start_background_scheduler, stop_background_scheduler
from app.security import (
    SecurityHeadersMiddleware,
    client_ip,
    limiter,
    rate_limit_exceeded_handler,
    require_admin_key,
)

settings = get_settings()
logger = logging.getLogger(__name__)

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
)


def warn_if_forwarded_headers_trusted() -> bool:
    """Warn when uvicorn may be taking the client address from a client header.

    Render's Python runtime documents FORWARDED_ALLOW_IPS=*. uvicorn reads that
    variable and, when it is "*", takes the LEFT-most X-Forwarded-For entry as
    the client address -- a value the client chooses. Start with
    --no-proxy-headers (see render.yaml) and let app.security.client_ip decide.
    """
    risky = os.environ.get("FORWARDED_ALLOW_IPS", "").strip() == "*"
    if risky:
        logger.warning(
            "FORWARDED_ALLOW_IPS=* is set. Unless uvicorn runs with --no-proxy-headers, "
            "client addresses (and per-IP rate limits) can be spoofed with an "
            "X-Forwarded-For header."
        )
    return risky


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("Starting Rubin Scout backend...")

    warn_if_forwarded_headers_trusted()

    # Start background ingestion scheduler
    if settings.app_env != "test":
        start_background_scheduler()

    # Warm up RAG chain (non-fatal — RAG failure must not block app startup)
    try:
        from rag.chain import warm_up
        warm_up()
    except Exception as exc:
        logger.warning("RAG warm_up import failed (non-fatal): %s", exc)

    yield

    # Shutdown
    logger.info("Shutting down Rubin Scout backend...")
    if settings.app_env != "test":
        stop_background_scheduler()


app = FastAPI(
    title="Rubin Scout",
    description="Filtered, enriched transient alerts from Rubin Observatory",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers
app.add_middleware(SecurityHeadersMiddleware)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Routes
app.include_router(alerts_live.router)
app.include_router(alerts_live_detail_router)
app.include_router(alerts.router)
app.include_router(gw.router)
app.include_router(ingest.router)
app.include_router(subscriptions.router)
app.include_router(images_router)
app.include_router(ask.router)

@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "name": "Rubin Scout",
        "version": "0.1.0",
        "status": "operational",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/api/health/client-ip")
async def client_ip_diagnostic(request: Request, _: bool = Depends(require_admin_key)):
    """Admin-only: what address the rate limiter would key this request under.

    Use it once after deploying to see what the host really sends. Call it with
    a spoofed ``X-Forwarded-For: 9.9.9.9``: ``resolved_ip`` must be your real
    address, never 9.9.9.9. Safe to delete once TRUSTED_PROXY_HOPS is settled.
    """
    return {
        "resolved_ip": client_ip(request),
        "trusted_proxy_hops": get_settings().trusted_proxy_hops,
        "socket_peer": request.client.host if request.client else None,
        "x_forwarded_for": request.headers.get("x-forwarded-for"),
    }


@app.get("/api/health/ping")
async def ping():
    """
    Lightweight keep-alive endpoint for external cron pings.
    No auth, no rate limit, no database query.
    Prevents Supabase free tier from sleeping.

    Pure liveness -- always 200 if the process can respond, deliberately
    never influenced by any application-level signal (e.g. LSST ingestion
    stall status, see GET /api/ingest/lsst/status instead). This is the
    endpoint most likely to double as a platform-level liveness/restart
    check (Render or similar), so it must never fail for a reason that
    restarting the process wouldn't fix. Why: docs/lsst-ingestion-
    recovery.md.
    """
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
