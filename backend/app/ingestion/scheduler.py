"""
Ingestion Scheduler.

Runs periodic pulls from TNS (primary) and ALeRCE (enrichment) to keep
the local database up to date. Runs as a background task within the FastAPI app.

TNS: Primary discovery feed (new transients)
ALeRCE: Enrichment layer (light curves, ML classifications)
"""

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.config import get_settings
from app.database import async_session
from app.enrichment.crossmatch import EnrichmentService
from app.enrichment.gw_crossmatch import GWCrossMatchService
from app.ingestion.alerce_service import AlerceIngestionService
from app.ingestion.chime_service import ChimeFRBIngestionService
from app.ingestion.fink_service import FinkIngestionService
from app.ingestion.tns_service import TNSIngestionService
from app.models.models import Object

settings = get_settings()
logger = logging.getLogger(__name__)

tns_service = TNSIngestionService()
alerce_service = AlerceIngestionService()
chime_service = ChimeFRBIngestionService()
fink_service = FinkIngestionService()
enrichment_service = EnrichmentService()
gw_service = GWCrossMatchService()

# Global scheduler instance
_scheduler = None


async def run_ingestion_cycle():
    """Execute one full ingestion + enrichment cycle."""
    logger.info("=" * 60)
    logger.info("Starting ingestion cycle")
    start = datetime.now(timezone.utc)

    async with async_session() as session:
        try:
            # PRIMARY SOURCE: Pull new discoveries from TNS
            logger.info("Fetching new objects from TNS...")
            tns_count = await tns_service.ingest_from_daily_csv(session)
            logger.info(f"✓ Ingested {tns_count} objects from TNS")

            # FRBs: Pull CHIME/FRB catalog detections
            logger.info("Fetching FRBs from CHIME/FRB catalog...")
            chime_count = await chime_service.ingest(session)
            logger.info(f"✓ Ingested {chime_count} FRBs from CHIME/FRB")

            # ENRICHMENT: Pull light curves and classifications from ALeRCE
            logger.info("Enriching with ALeRCE data...")
            alerce_count = await alerce_service.ingest_recent(
                session,
                lookback_days=settings.ingestion_lookback_days,
            )
            logger.info(f"✓ Enriched {alerce_count} objects with ALeRCE data")

            # ENRICHMENT: Cross-match with SIMBAD for catalog associations
            logger.info("Cross-matching with SIMBAD...")
            result = await session.execute(
                select(Object)
                .where(Object.cross_match_catalog.is_(None))
                .order_by(Object.created_at.desc())
                .limit(50)
            )
            unenriched = result.scalars().all()

            if unenriched:
                enriched = await enrichment_service.enrich_batch(session, unenriched)
                logger.info(f"✓ Enriched {enriched} objects with SIMBAD data")
            else:
                logger.info("✓ No unenriched objects found")

        except Exception as e:
            logger.error(f"✗ Ingestion cycle failed: {e}", exc_info=True)

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    logger.info(f"Ingestion cycle completed in {elapsed:.1f}s")
    logger.info("=" * 60)


async def keepalive_ping():
    """Run a minimal SELECT 1 to keep the SQLAlchemy connection pool warm."""
    from sqlalchemy import text as sa_text
    async with async_session() as session:
        try:
            await session.execute(sa_text("SELECT 1"))
        except Exception as e:
            logger.warning(f"Keep-alive ping failed: {e}")


async def refresh_gw_events():
    """Re-seed GW events from GWOSC to pick up new GWTC catalog releases."""
    logger.info("Refreshing GW events from GWOSC...")
    async with async_session() as session:
        try:
            count = await gw_service.seed_gw_events(session)
            logger.info(f"✓ GW refresh complete: {count} new events seeded")
        except Exception as e:
            logger.error(f"✗ GW event refresh failed: {e}", exc_info=True)


async def run_fink_ingestion():
    """Ingest the latest Fink/ZTF live alerts into alerts_live.

    Scheduled daily at 10:00 UTC — after the ZTF observing night closes and
    Fink has finished classifying the night's alert stream.
    """
    logger.info("Starting Fink/ZTF live alert ingestion...")
    async with async_session() as session:
        try:
            count = await fink_service.ingest(session)
            logger.info(f"✓ Fink ingestion complete: {count} alerts inserted")
        except Exception as e:
            logger.error(f"✗ Fink ingestion failed: {e}", exc_info=True)


def start_background_scheduler():
    """
    Start the background ingestion scheduler.
    Called from FastAPI app startup.
    """
    global _scheduler

    if _scheduler is not None:
        logger.warning("Scheduler already running, skipping start")
        return _scheduler

    _scheduler = AsyncIOScheduler()

    # Run ingestion cycle at configured interval
    _scheduler.add_job(
        run_ingestion_cycle,
        "interval",
        seconds=settings.ingestion_interval_seconds,
        next_run_time=datetime.now(timezone.utc),  # Run immediately on start
        id="ingestion_cycle",
        name="TNS + ALeRCE Ingestion",
        replace_existing=True,
    )

    # Fink/ZTF live alerts — daily at 10:00 UTC (after ZTF night closes)
    _scheduler.add_job(
        run_fink_ingestion,
        "cron",
        hour=10,
        minute=0,
        timezone="UTC",
        id="fink_ingestion",
        name="Fink/ZTF Live Alert Ingestion",
        replace_existing=True,
    )

    # Re-seed GW events from GWOSC weekly to pick up new catalog releases
    _scheduler.add_job(
        refresh_gw_events,
        "interval",
        weeks=1,
        next_run_time=datetime.now(timezone.utc),  # Run once on startup too
        id="gw_refresh",
        name="GWOSC GW Event Refresh",
        replace_existing=True,
    )

    # Keep-alive: run SELECT 1 every 4 minutes to keep the DB connection pool warm
    _scheduler.add_job(
        keepalive_ping,
        "interval",
        seconds=240,
        id="db_keepalive",
        name="Database Keep-alive",
        replace_existing=True,
    )

    _scheduler.start()

    logger.info("=" * 60)
    logger.info("Background ingestion scheduler started")
    logger.info(f"Interval: {settings.ingestion_interval_seconds}s ({settings.ingestion_interval_seconds / 60:.1f} minutes)")
    logger.info("Jobs scheduled:")
    logger.info("  - TNS ingestion (primary discovery)")
    logger.info("  - ALeRCE enrichment (light curves + ML)")
    logger.info("  - SIMBAD cross-matching")
    logger.info("  - Fink/ZTF live alert ingestion (daily 10:00 UTC)")
    logger.info("  - GWOSC GW event refresh (weekly)")
    logger.info("  - Database keep-alive ping (every 4 min)")
    logger.info("=" * 60)

    return _scheduler


def stop_background_scheduler():
    """
    Stop the background scheduler.
    Called from FastAPI app shutdown.
    """
    global _scheduler

    if _scheduler is not None:
        logger.info("Stopping background scheduler...")
        _scheduler.shutdown()
        _scheduler = None
        logger.info("✓ Scheduler stopped")


def main():
    """
    Standalone scheduler runner (for testing).
    In production, use start_background_scheduler() from FastAPI app.
    """
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    )

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_ingestion_cycle,
        "interval",
        seconds=settings.ingestion_interval_seconds,
        next_run_time=datetime.now(timezone.utc),
        id="ingestion_cycle",
        name="TNS + ALeRCE Ingestion",
    )

    logger.info(f"Standalone scheduler starting (interval: {settings.ingestion_interval_seconds}s)")
    scheduler.start()

    try:
        asyncio.get_event_loop().run_forever()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
