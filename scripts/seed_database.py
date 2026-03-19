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
from app.enrichment.gw_crossmatch import GWCrossMatchService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger(__name__)


async def seed():
    logger.info("Seeding database with real ALeRCE alerts...")

    service = AlerceIngestionService()

    async with async_session() as session:
        total = await service.ingest_recent(
            session,
            lookback_days=7,  # Pull a full week of data
            target_classes=["SNIa", "SNII", "AGN", "TDE"],
        )

    logger.info(f"Seed complete: {total} objects ingested")

    # Seed GW events
    logger.info("Seeding gravitational wave events...")
    gw_service = GWCrossMatchService()
    async with async_session() as session:
        gw_count = await gw_service.seed_gw_events(session)
    logger.info(f"Seeded {gw_count} GW events")

    logger.info("Run the backend to see them: uvicorn app.main:app --reload")


if __name__ == "__main__":
    asyncio.run(seed())
