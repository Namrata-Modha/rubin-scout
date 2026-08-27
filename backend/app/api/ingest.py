"""
API routes for data ingestion (seeding and on-demand pulls).

Security:
- All endpoints require admin API key in production
- Rate limited: 5/minute (ingestion is heavy)
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.ingestion.lsst_service import LsstFinkIngestionService
from app.ingestion.tns_service import TNSIngestionService
from app.security import limiter, require_admin_key

router = APIRouter(prefix="/api/ingest", tags=["ingestion"])
tns_service = TNSIngestionService()
lsst_service = LsstFinkIngestionService()
settings = get_settings()


@router.post("/tns/seed", dependencies=[Depends(require_admin_key)])
@limiter.limit("5/minute")
async def seed_tns(
    request: Request,
    days: int = Query(7, ge=1, le=30, description="How many days back to seed"),
    db: AsyncSession = Depends(get_db),
):
    """
    Seed the database with TNS discoveries from the last N days.
    Downloads public CSV files from TNS (no API key needed for the data).
    Requires X-API-Key header in production.
    """
    count = await tns_service.seed_recent_days(db, days=days)
    return {"status": "ok", "source": "tns_csv", "days": days, "objects_ingested": count}


@router.post("/tns/daily", dependencies=[Depends(require_admin_key)])
@limiter.limit("5/minute")
async def ingest_tns_daily(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Pull yesterday's TNS daily CSV (most recent complete file).
    This is what the scheduled ingestion runs automatically.
    """
    count = await tns_service.ingest_from_daily_csv(db)
    return {"status": "ok", "source": "tns_csv", "objects_ingested": count}


@router.post("/fink/trigger", dependencies=[Depends(require_admin_key)])
@limiter.limit("5/minute")
async def trigger_fink_ingestion(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a Fink ingestion run."""
    from app.ingestion.fink_service import FinkIngestionService
    service = FinkIngestionService()
    count = await service.ingest(db)
    return {"status": "ok", "alerts_inserted": count}


@router.post("/chime/trigger", dependencies=[Depends(require_admin_key)])
@limiter.limit("5/minute")
async def trigger_chime_ingestion(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a CHIME/FRB catalog ingestion run.

    Idempotent: the service upserts per oid, so re-running never duplicates
    rows. Use this instead of waiting for the monthly scheduled job.
    """
    from app.ingestion.chime_service import ChimeFRBIngestionService
    service = ChimeFRBIngestionService()
    count = await service.ingest(db)
    return {"status": "ok", "source": "chimefrb_catalog", "frbs_ingested": count}


@router.post("/lsst/trigger", dependencies=[Depends(require_admin_key)])
@limiter.limit("5/minute")
async def trigger_lsst_ingestion(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a Fink/LSST (Rubin) ingestion run.

    Separate Fink deployment from ZTF (api.lsst.fink-portal.org, not
    api.ztf.fink-portal.org) with a materially different alert schema and
    discovery mechanism ("tags" instead of a single classification label) —
    see app/ingestion/lsst_service.py's module docstring for the full field
    comparison. Real date-windowed, cursor-paginated (not a fixed-count
    fetch): LSST's confirmed nightly alert volume (up to ~745,000/night)
    would make a fixed n=100 silently drop the overwhelming majority of a
    night's alerts. Not yet wired into the automatic scheduler — this
    manual trigger is the only way to run it during this pass.
    """
    from app.ingestion.lsst_service import LsstFinkIngestionService
    service = LsstFinkIngestionService()
    count = await service.ingest(db)
    return {"status": "ok", "source": "fink_lsst", "alerts_inserted": count}


@router.get("/lsst/status")
@limiter.limit("60/minute")
async def lsst_ingestion_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Report LSST ingestion stall status -- a separate concern from
    GET /api/health/ping's pure process liveness, and deliberately not
    merged into it (see that endpoint's docstring). Returns 503 when
    genuinely stalled (see LsstFinkIngestionService.check_stall), 200
    otherwise, so a monitor pointed specifically at this path can alert on
    it without any risk to a liveness check pointed at /api/health/ping.
    No admin key required -- read-only, not sensitive. Why:
    docs/lsst-ingestion-recovery.md.
    """
    lsst_status = await lsst_service.check_stall(db)
    body = {"lsst_ingestion": lsst_status}
    if lsst_status.get("stalled"):
        return JSONResponse(status_code=503, content=body)
    return body


@router.post("/admin/backfill-tns-photometry", dependencies=[Depends(require_admin_key)])
@limiter.limit("5/minute")
async def backfill_tns_photometry(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Backfill photometry for TNS objects that don't have detections.
    Requires admin API key.
    """
    service = TNSIngestionService()
    count = await service.backfill_photometry(db)

    return {
        "status": "completed",
        "objects_processed": count,
        "message": f"Fetched photometry for {count} objects"
    }
