"""
API routes for data ingestion (seeding and on-demand pulls).

Security:
- All endpoints require admin API key in production
- Rate limited: 5/minute (ingestion is heavy)
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.ingestion.tns_service import TNSIngestionService
from app.security import limiter, require_admin_key

router = APIRouter(prefix="/api/ingest", tags=["ingestion"])
tns_service = TNSIngestionService()


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
