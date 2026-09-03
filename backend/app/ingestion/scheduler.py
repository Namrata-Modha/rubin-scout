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
from app.enrichment.gw_crossmatch import (
    GWCrossMatchService,
    _fetch_gwosc_payload,
    fetch_gwosc_catalog_index,
)
from app.ingestion.alerce_service import AlerceIngestionService
from app.ingestion.chime_service import ChimeFRBIngestionService
from app.ingestion.fink_service import TRIGGER_SCHEDULER, FinkIngestionService
from app.ingestion.lsst_service import MAX_WINDOW_SPAN, LsstFinkIngestionService
from app.ingestion.tns_service import TNSIngestionService
from app.models.models import Object

settings = get_settings()
logger = logging.getLogger(__name__)

tns_service = TNSIngestionService()
alerce_service = AlerceIngestionService()
chime_service = ChimeFRBIngestionService()
fink_service = FinkIngestionService()
lsst_service = LsstFinkIngestionService()
enrichment_service = EnrichmentService()
gw_service = GWCrossMatchService()

# 15 minutes -- confirmed against real Fink LSST data (see
# docs/lsst-ingestion-recovery.md): the worst known 15-minute burst carried
# 8,342/10,000 (83%) of the per-tag/cycle cap, so this interval keeps
# steady-state cycles comfortably clear of it. Re-checked live on
# 2026-08-27 (six weeks after the 2026-07-14 storm evacuation): the
# observatory is still dormant, no new nights recorded, so the burst
# evidence behind this number is unchanged. MUST stay strictly less than
# MAX_WINDOW_SPAN below.
LSST_INGESTION_INTERVAL_SECONDS = 900

assert LSST_INGESTION_INTERVAL_SECONDS < MAX_WINDOW_SPAN.total_seconds(), (
    "LSST_INGESTION_INTERVAL_SECONDS must stay strictly less than "
    "lsst_service.MAX_WINDOW_SPAN -- otherwise every steady-state cycle's "
    "natural window would routinely hit the catch-up cap, defeating its "
    "purpose. If you're intentionally changing the cadence, update "
    "MAX_WINDOW_SPAN in lsst_service.py to stay larger too."
)

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

            # NOTE: CHIME/FRB is deliberately NOT ingested here. Catalog 1 is a
            # static 2021 data release, so pulling it every cycle re-downloaded
            # the full VizieR VOTable ~96x/day for data that never changes. It
            # now runs on its own monthly cron job (run_chime_ingestion) plus an
            # idempotent manual trigger (POST /api/ingest/chime/trigger).

            # ENRICHMENT: Pull light curves and classifications from ALeRCE
            logger.info("Enriching with ALeRCE data...")
            alerce_count = await alerce_service.ingest_recent(
                session,
                lookback_days=settings.ingestion_lookback_days,
            )
            logger.info(f"✓ Enriched {alerce_count} objects with ALeRCE data")

            # ENRICHMENT: Cross-match with SIMBAD for catalog associations.
            # FRBs are excluded: CHIME/FRB positions are ~arcminute-scale, so a
            # 5-arcsec SIMBAD cone search (~180x tighter than the uncertainty)
            # would only ever surface chance coincidences dressed up as
            # associations. See EnrichmentService.enrich_batch for the matching
            # defensive guard.
            logger.info("Cross-matching with SIMBAD...")
            result = await session.execute(
                select(Object)
                .where(Object.cross_match_catalog.is_(None))
                .where(Object.classification.is_distinct_from("FRB"))
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
    """Re-seed GW events from GWOSC, then reconcile retired superevent IDs.

    Two halves of one weekly pass, sharing a single fetch of the GWOSC feed:

      1. ``seed_gw_events`` upserts everything GWOSC still publishes, so new
         GWTC catalog releases land.
      2. ``reconcile_retired_events`` handles the other direction -- an ID
         GWOSC has STOPPED publishing (renamed or withdrawn). Upsert alone
         cannot see those: it only ever touches IDs present in the feed, so a
         retired row would otherwise sit in ``gw_events`` forever alongside its
         successor.

    Reconciliation is deliberately not its own cron. It is only meaningful
    against a freshly seeded table -- a successor has to already exist locally
    before a retired row can be pointed at it.
    """
    logger.info("Refreshing GW events from GWOSC...")
    async with async_session() as session:
        try:
            payload = await _fetch_gwosc_payload()
            count = await gw_service.seed_gw_events(session, payload=payload)
            logger.info(f"✓ GW refresh complete: {count} new events seeded")
        except Exception as e:
            logger.error(f"✗ GW event refresh failed: {e}", exc_info=True)
            return

        try:
            index = await fetch_gwosc_catalog_index(payload=payload)
            report = await gw_service.reconcile_retired_events(session, index=index)
            if report["skipped_reason"]:
                logger.warning(
                    "⚠ GW retirement reconciliation skipped: %s",
                    report["skipped_reason"],
                )
            else:
                logger.info(
                    "✓ GW reconciliation complete: %d retired with successor, "
                    "%d retired needing human review, %d un-retired",
                    len(report["retired"]),
                    len(report["retired_unresolved"]),
                    len(report["unretired"]),
                )
        except Exception as e:
            # Seeding already succeeded and was committed; a reconciliation
            # failure must not make the whole refresh look like a no-op.
            logger.error(
                f"✗ GW retirement reconciliation failed: {e}", exc_info=True
            )


async def run_chime_ingestion():
    """Ingest the static CHIME/FRB Catalog 1 from VizieR.

    Catalog 1 is a one-off 2021 data release, so this runs on a monthly cron
    rather than every ingestion cycle. ``ChimeFRBIngestionService.ingest`` is
    idempotent (per-oid upsert), so the monthly run and the manual trigger are
    both safe to re-run at any time.
    """
    logger.info("Starting CHIME/FRB catalog ingestion...")
    async with async_session() as session:
        try:
            count = await chime_service.ingest(session)
            logger.info(f"✓ CHIME/FRB ingestion complete: {count} FRBs upserted")
        except Exception as e:
            logger.error(f"✗ CHIME/FRB ingestion failed: {e}", exc_info=True)


async def run_fink_ingestion():
    """Ingest the latest Fink/ZTF live alerts into alerts_live.

    Scheduled daily at 10:00 UTC — after the ZTF observing night closes and
    Fink has finished classifying the night's alert stream.
    """
    logger.info("Starting Fink/ZTF live alert ingestion...")
    async with async_session() as session:
        try:
            count = await fink_service.ingest(session, trigger_source=TRIGGER_SCHEDULER)
            logger.info(f"✓ Fink ingestion complete: {count} alerts inserted")
        except Exception as e:
            logger.error(f"✗ Fink ingestion failed: {e}", exc_info=True)


async def run_lsst_ingestion():
    """Ingest the latest Fink/LSST (Rubin) live alerts into alerts_live.

    Scheduled every LSST_INGESTION_INTERVAL_SECONDS, not daily like ZTF and
    not inside the shared TNS/ALeRCE cycle -- see that constant's docstring
    and docs/lsst-ingestion-recovery.md for why: LSST's volume is bursty
    and an order of magnitude larger than either. A cycle that finds
    nothing new (e.g. during the observatory's ongoing downtime) completes
    normally via LsstFinkIngestionService.ingest's own exhausted-on-empty
    handling -- this is not treated as an error here or by check_stall().
    """
    logger.info("Starting Fink/LSST (Rubin) live alert ingestion...")
    async with async_session() as session:
        try:
            count = await lsst_service.ingest(session)
            logger.info(f"✓ Fink/LSST ingestion complete: {count} alerts inserted")
        except Exception as e:
            logger.error(f"✗ Fink/LSST ingestion failed: {e}", exc_info=True)


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

    # Fink/LSST (Rubin) live alerts -- see LSST_INGESTION_INTERVAL_SECONDS's
    # docstring for why this interval, not ZTF's daily cron or the shared
    # TNS/ALeRCE cycle. Runs once immediately on start too, so a restart
    # doesn't wait a full interval before catching up.
    _scheduler.add_job(
        run_lsst_ingestion,
        "interval",
        seconds=LSST_INGESTION_INTERVAL_SECONDS,
        next_run_time=datetime.now(timezone.utc),
        id="lsst_ingestion",
        name="Fink/LSST (Rubin) Live Alert Ingestion",
        replace_existing=True,
    )

    # CHIME/FRB Catalog 1 — static 2021 release, refresh monthly (1st, 03:00 UTC).
    # An idempotent manual trigger is also exposed at POST /api/ingest/chime/trigger.
    _scheduler.add_job(
        run_chime_ingestion,
        "cron",
        day=1,
        hour=3,
        minute=0,
        timezone="UTC",
        id="chime_ingestion",
        name="CHIME/FRB Catalog Ingestion",
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
    logger.info(
        f"  - Fink/LSST (Rubin) live alert ingestion "
        f"(every {LSST_INGESTION_INTERVAL_SECONDS}s / "
        f"{LSST_INGESTION_INTERVAL_SECONDS / 60:.1f} min)"
    )
    logger.info("  - CHIME/FRB catalog ingestion (monthly, 1st 03:00 UTC)")
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
