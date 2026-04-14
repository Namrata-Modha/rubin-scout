"""
Rubin Scout FastAPI Application.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.api import alerts, gw, ingest, subscriptions
from app.api.images import router as images_router
from app.config import get_settings
from app.ingestion.scheduler import start_background_scheduler, stop_background_scheduler
from app.security import (
    SecurityHeadersMiddleware,
    limiter,
    rate_limit_exceeded_handler,
)

settings = get_settings()
logger = logging.getLogger(__name__)

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("Starting Rubin Scout backend...")

    # Start background ingestion scheduler
    if settings.app_env != "test":
        start_background_scheduler()

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
app.include_router(alerts.router)
app.include_router(gw.router)
app.include_router(ingest.router)
app.include_router(subscriptions.router)
app.include_router(images_router)

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
