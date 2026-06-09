"""
Live alert stream endpoints — backed by the alerts_live table.

Routes:
  GET /api/alerts/live                       Paginated live alerts with optional classification filter
  GET /api/alerts/live/classifications       Distinct classification values + row counts
  GET /api/live-alerts/live/{external_id}    Single alert detail with parsed raw_payload fields
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import AlertLive, AlertSource
from app.security import limiter

router = APIRouter(prefix="/api/alerts", tags=["live-alerts"])
detail_router = APIRouter(prefix="/api/live-alerts", tags=["live-alerts"])


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


# ---------------------------------------------------------------------------
# Detail endpoint — separate router so the URL is /api/live-alerts/live/{id}
# ---------------------------------------------------------------------------

def _extract_payload_fields(payload: dict) -> dict:
    """Pull the documented Fink fields out of raw_payload into a typed dict.

    All fields are optional — the payload shape can vary between Fink classes
    and schema revisions, so every access uses .get() with a None default.
    """
    p = payload or {}
    return {
        "coords": {
            "ra":  p.get("i:ra"),
            "dec": p.get("i:dec"),
            "jd":  p.get("i:jd"),
        },
        "photometry": {
            "magpsf":     p.get("i:magpsf"),
            "sigmapsf":   p.get("i:sigmapsf"),
            "magzpsci":   p.get("i:magzpsci"),
            "diffmaglim":  p.get("i:diffmaglim"),
            "rb":          p.get("i:rb"),
            "drb":         p.get("i:drb"),
        },
        "classification_scores": {
            "snn_sn_vs_all":    p.get("d:snn_sn_vs_all"),
            "snn_snia_vs_nonia": p.get("d:snn_snia_vs_nonia"),
            "rf_kn_vs_nonkn":   p.get("d:rf_kn_vs_nonkn"),
            "slsn_score":       p.get("d:slsn_score"),
        },
        "context": {
            "constellation": p.get("v:constellation"),
            "firstdate":     p.get("v:firstdate"),
            "lastdate":      p.get("v:lastdate"),
            "lapse":         p.get("v:lapse"),
            "classification": p.get("v:classification"),
        },
        "crossmatch": {
            "cdsxmatch":               p.get("d:cdsxmatch"),
            "tns":                     p.get("d:tns") or None,
            "vsx":                     p.get("d:vsx") or None,
            "mangrove_2MASS_name":     p.get("d:mangrove_2MASS_name") or None,
            "mangrove_HyperLEDA_name": p.get("d:mangrove_HyperLEDA_name") or None,
            "mangrove_lum_dist":       p.get("d:mangrove_lum_dist"),
        },
        "host": {
            "classtar":  p.get("i:classtar"),
            "distnr":    p.get("i:distnr"),
            "magnr":     p.get("i:magnr"),
            "ndethist":  p.get("i:ndethist"),
            "nmtchps":   p.get("i:nmtchps"),
        },
        "object_id": p.get("i:objectId"),
    }


@detail_router.get("/live/{external_id}")
@limiter.limit("60/minute")
async def get_live_alert_detail(
    external_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Fetch a single alerts_live row and return parsed raw_payload fields."""
    result = await db.execute(
        select(AlertLive).where(AlertLive.external_id == external_id)
    )
    row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Live alert {external_id!r} not found")

    payload_fields = _extract_payload_fields(row.raw_payload or {})

    return {
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
        **payload_fields,
    }
