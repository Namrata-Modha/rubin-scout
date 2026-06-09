"""
Live alert stream endpoints — backed by the alerts_live table.

Routes:
  GET /api/alerts/live                  Paginated live alerts with optional classification filter
  GET /api/alerts/live/classifications  Distinct classification values + row counts
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import AlertLive, AlertSource
from app.security import limiter

router = APIRouter(prefix="/api/alerts", tags=["live-alerts"])


@router.get("/live")
@limiter.limit("60/minute")
async def get_live_alerts(
    request: Request,
    limit: int = Query(50, ge=1, le=200, description="Rows per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    classification: Optional[str] = Query(None, description="Filter by Fink classification label"),
    db: AsyncSession = Depends(get_db),
):
    """Return paginated rows from alerts_live, newest first."""
    stmt = select(AlertLive).order_by(AlertLive.ingested_at.desc())
    count_stmt = select(func.count()).select_from(AlertLive)

    if classification:
        stmt = stmt.where(AlertLive.classification == classification)
        count_stmt = count_stmt.where(AlertLive.classification == classification)

    stmt = stmt.limit(limit).offset(offset)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    result = await db.execute(stmt)
    rows = result.scalars().all()

    alerts = [
        {
            "id": row.id,
            "external_id": row.external_id,
            "ra": row.ra,
            "dec": row.dec,
            "alert_type": row.alert_type,
            "classification": row.classification,
            "classification_score": row.classification_score,
            "jd": row.jd,
            "detected_at": row.detected_at.isoformat() if row.detected_at else None,
            "ingested_at": row.ingested_at.isoformat() if row.ingested_at else None,
            "oid": row.oid,
        }
        for row in rows
    ]

    return {"alerts": alerts, "total": total, "limit": limit, "offset": offset}


@router.get("/live/classifications")
@limiter.limit("60/minute")
async def get_live_classifications(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return distinct classification labels in alerts_live with row counts."""
    stmt = (
        select(AlertLive.classification, func.count().label("count"))
        .where(AlertLive.classification.isnot(None))
        .group_by(AlertLive.classification)
        .order_by(func.count().desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    classifications = [
        {"classification": row.classification, "count": row.count}
        for row in rows
    ]
    return {"classifications": classifications, "total": sum(r["count"] for r in classifications)}
