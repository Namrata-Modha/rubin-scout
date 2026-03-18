"""
Rubin Scout — FastAPI Application.

Serves enriched transient alerts from Rubin/ZTF through a REST API
with support for cone searches, time-range queries, classification
filters, and real-time WebSocket streams.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.api.alerts import router as alerts_router
from app.api.subscriptions import router as subscriptions_router

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
    logger.info(f"ALeRCE API: {settings.alerce_api_url}")
    yield
    logger.info("Rubin Scout shutting down")


app = FastAPI(
    title="Rubin Scout",
    description=(
        "Filtered, enriched transient alerts from the Vera C. Rubin Observatory and ZTF. "
        "Connects to ALeRCE and Pitt-Google alert brokers, cross-matches with SIMBAD/NED, "
        "and serves alerts through REST API and real-time WebSocket."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(alerts_router)
app.include_router(subscriptions_router)


@app.get("/", tags=["health"])
async def root():
    return {
        "name": "Rubin Scout",
        "version": "0.1.0",
        "status": "operational",
        "docs": "/docs",
    }


@app.get("/health", tags=["health"])
async def health_check():
    """Health check endpoint for monitoring and load balancers."""
    return {"status": "healthy"}
