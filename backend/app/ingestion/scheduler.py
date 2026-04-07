"""
Ingestion Scheduler.

Runs periodic pulls from ALeRCE to keep the local database up to date.
This is the MVP approach; the Kafka consumer (Week 5) will replace this
for real-time ingestion.

Run with: python -m app.ingestion.scheduler
"""

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.config import get_settings
from app.database import async_session
from app.enrichment.crossmatch import EnrichmentService
from app.ingestion.alerce_service import AlerceIngestionService
from app.ingestion.tns_service import TNSIngestionService
from app.models.models import Object

settings = get_settings()
logger = logging.getLogger(__name__)

tns_service = TNSIngestionService()
ingestion_service = AlerceIngestionService()
enrichment_service = EnrichmentService()


async def run_ingestion_cycle():
    """Execute one full ingestion + enrichment cycle."""
    logger.info("Starting ingestion cycle")
    start = datetime.now(timezone.utc)

    async with async_session() as session:
        try:
            # PRIMARY SOURCE: Pull new discoveries from TNS daily CSV
            tns_count = await tns_service.ingest_from_daily_csv(session)
            logger.info(f"Ingested {tns_count} objects from TNS")

            # ENRICHMENT: Pull light curves and classifications from ALeRCE
            alerce_count = await ingestion_service.ingest_recent(
                session,
                lookback_days=settings.ingestion_lookback_days,
            )
            logger.info(f"Ingested {alerce_count} objects from ALeRCE")

            # ENRICHMENT: Cross-match with SIMBAD for catalog associations
            result = await session.execute(
                select(Object)
                .where(Object.cross_match_catalog.is_(None))
                .order_by(Object.created_at.desc())
                .limit(50)
            )
            unenriched = result.scalars().all()

            if unenriched:
                enriched = await enrichment_service.enrich_batch(session, unenriched)
                logger.info(f"Enriched {enriched} objects with SIMBAD data")

        except Exception as e:
            logger.error(f"Ingestion cycle failed: {e}", exc_info=True)

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    logger.info(f"Ingestion cycle completed in {elapsed:.1f}s")


def main():
    """Start the scheduler."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    )

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_ingestion_cycle,
        "interval",
        seconds=settings.ingestion_interval_seconds,
        next_run_time=datetime.now(timezone.utc),  # Run immediately on start
    )

    logger.info(f"Ingestion scheduler starting (interval: {settings.ingestion_interval_seconds}s)")
    scheduler.start()

    try:
        asyncio.get_event_loop().run_forever()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
