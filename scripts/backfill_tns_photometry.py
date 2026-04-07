"""
Backfill photometry for existing TNS objects that have no detections.

This fixes the "No light curve data available" issue for objects
that were seeded from TNS CSV (which only contains metadata).

Usage:
    cd backend
    python -m scripts.backfill_tns_photometry
"""

import asyncio
import logging
import sys
import os

# Add the backend directory to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.join(project_root, "backend")
sys.path.insert(0, backend_dir)

from app.database import async_session
from app.ingestion.tns_service import TNSIngestionService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger(__name__)


async def backfill():
    logger.info("Starting TNS photometry backfill...")
    logger.info("This will fetch light curves for all TNS objects that don't have them")
    
    service = TNSIngestionService()
    
    async with async_session() as session:
        count = await service.backfill_photometry(session)
    
    logger.info(f"✓ Backfill complete: fetched photometry for {count} objects")
    logger.info("Check your dashboard - light curves should now appear!")


if __name__ == "__main__":
    asyncio.run(backfill())