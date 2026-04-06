"""
Seed the database with a batch of real alerts from ALeRCE.

This pulls a sample of recent transients across multiple classes
so you have real data to work with immediately. Run once after
setting up the database.

Usage:
    cd backend
    python -m scripts.seed_database
"""

import asyncio
import logging
import sys
import os

# Add the backend directory to Python path so we can import 'app'
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.join(project_root, "backend")
sys.path.insert(0, backend_dir)

from app.database import async_session
from app.ingestion.alerce_service import AlerceIngestionService
from app.ingestion.tns_service import TNSIngestionService
from app.enrichment.gw_crossmatch import GWCrossMatchService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger(__name__)


async def seed():
    logger.info("Seeding database with real astronomical discoveries...")

    # PRIMARY: Seed from TNS (last 7 days of discoveries, no API key needed)
    logger.info("=== Phase 1: TNS discoveries (primary source) ===")
    tns_service = TNSIngestionService()
    async with async_session() as session:
        tns_count = await tns_service.seed_recent_days(session, days=7)
    logger.info(f"TNS seed complete: {tns_count} objects from last 7 days")

    # ENRICHMENT: Seed from ALeRCE (classified objects with light curves)
    logger.info("=== Phase 2: ALeRCE enrichment (light curves + ML) ===")
    service = AlerceIngestionService()
    async with async_session() as session:
        alerce_count = await service.ingest_recent(
            session,
            lookback_days=7,
            target_classes=["SNIa", "SNII", "AGN", "TDE"],
        )
    logger.info(f"ALeRCE seed complete: {alerce_count} objects")

    # GW EVENTS: Seed gravitational wave events
    logger.info("=== Phase 3: Gravitational wave events ===")
    gw_service = GWCrossMatchService()
    async with async_session() as session:
        gw_count = await gw_service.seed_gw_events(session)
    logger.info(f"Seeded {gw_count} GW events")

    total = tns_count + alerce_count
    logger.info(f"=== Seed complete: {total} total objects + {gw_count} GW events ===")
    logger.info("Run the backend to see them: uvicorn app.main:app --reload")


if __name__ == "__main__":
    asyncio.run(seed())
