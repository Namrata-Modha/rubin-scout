"""
API routes for querying and exploring alerts.

These endpoints serve the React dashboard and are also usable
directly by scientists from Jupyter notebooks or scripts.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, func, text, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import Object, Detection, ClassificationProbability

router = APIRouter(prefix="/api", tags=["alerts"])


@router.get("/alerts/recent")
async def get_recent_alerts(
    classification: Optional[str] = Query(None, description="Filter by class (SNIa, SNII, AGN, etc.)"),
    min_probability: float = Query(0.5, ge=0.0, le=1.0, description="Minimum classification confidence"),
    hours: int = Query(24, ge=1, le=87600, description="Lookback window in hours"),
    limit: int = Query(50, ge=1, le=500, description="Max results to return"),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    Get recent transient alerts, filtered and sorted by last detection.

    This is the primary endpoint for the dashboard's event table.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    query = (
        select(Object)
        .where(Object.last_detection >= cutoff)
        .where(Object.classification_probability >= min_probability)
        .order_by(desc(Object.last_detection))
    )

    if classification:
        query = query.where(Object.classification == classification)

    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    objects = result.scalars().all()

    return {
        "count": len(objects),
        "alerts": [obj.to_dict() for obj in objects],
    }


@router.get("/alerts/{oid}")
async def get_alert_detail(oid: str, db: AsyncSession = Depends(get_db)):
    """
    Full detail for a single object: metadata, light curve, cross-matches,
    and classification probabilities.
    """
    result = await db.execute(select(Object).where(Object.oid == oid))
    obj = result.scalar_one_or_none()

    if not obj:
        raise HTTPException(status_code=404, detail=f"Object {oid} not found")

    # Fetch light curve
    det_result = await db.execute(
        select(Detection)
        .where(Detection.oid == oid)
        .order_by(Detection.mjd)
    )
    detections = det_result.scalars().all()

    # Fetch classification probabilities
    prob_result = await db.execute(
        select(ClassificationProbability)
        .where(ClassificationProbability.oid == oid)
        .order_by(desc(ClassificationProbability.probability))
    )
    probabilities = prob_result.scalars().all()

    return {
        "object": obj.to_dict(),
        "light_curve": [det.to_dict() for det in detections],
        "probabilities": [
            {
                "class_name": p.class_name,
                "probability": p.probability,
                "classifier": p.classifier_name,
            }
            for p in probabilities
        ],
    }


@router.get("/alerts/conesearch/query")
async def cone_search(
    ra: float = Query(..., ge=0, le=360, description="Right Ascension in degrees"),
    dec: float = Query(..., ge=-90, le=90, description="Declination in degrees"),
    radius: float = Query(60, ge=1, le=3600, description="Search radius in arcseconds"),
    db: AsyncSession = Depends(get_db),
):
    """
    Find all objects within a radius of a sky position.

    Uses PostGIS spatial index for efficient queries. This is the standard
    astronomical query pattern for "what's near this position?"
    """
    # PostGIS ST_DWithin uses meters for geography type, convert arcsec to meters
    # 1 arcsec ~ 30.87 meters at Earth's surface (for sky coordinates, we use degrees)
    # Actually for geography, ST_DWithin uses meters. But for sky coords,
    # we use the angular distance directly.
    radius_deg = radius / 3600.0

    result = await db.execute(
        text("""
            SELECT oid, ra, dec, classification, classification_probability,
                   last_detection, n_detections, cross_match_name,
                   ST_Distance(
                       position,
                       ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography
                   ) / 30.87 as distance_arcsec
            FROM objects
            WHERE ST_DWithin(
                position,
                ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography,
                :radius_meters
            )
            ORDER BY distance_arcsec
            LIMIT 100
        """),
        {"ra": ra, "dec": dec, "radius_meters": radius * 30.87},
    )

    rows = result.fetchall()
    return {
        "count": len(rows),
        "center": {"ra": ra, "dec": dec, "radius_arcsec": radius},
        "results": [
            {
                "oid": row.oid,
                "ra": row.ra,
                "dec": row.dec,
                "classification": row.classification,
                "probability": row.classification_probability,
                "last_detection": row.last_detection.isoformat() if row.last_detection else None,
                "distance_arcsec": round(row.distance_arcsec, 2),
                "cross_match": row.cross_match_name,
            }
            for row in rows
        ],
    }


@router.get("/stats/summary")
async def get_summary_stats(
    hours: int = Query(24, ge=1, le=87600),
    db: AsyncSession = Depends(get_db),
):
    """
    Summary statistics for the dashboard header.
    Shows counts by classification, total objects, and recent activity.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Count by classification
    class_counts = await db.execute(
        select(Object.classification, func.count(Object.oid))
        .where(Object.last_detection >= cutoff)
        .group_by(Object.classification)
        .order_by(desc(func.count(Object.oid)))
    )

    # Total objects in window
    total = await db.execute(
        select(func.count(Object.oid)).where(Object.last_detection >= cutoff)
    )

    # Most recent alert
    latest = await db.execute(
        select(Object).order_by(desc(Object.last_detection)).limit(1)
    )
    latest_obj = latest.scalar_one_or_none()

    return {
        "time_window_hours": hours,
        "total_alerts": total.scalar() or 0,
        "by_classification": {
            row[0]: row[1] for row in class_counts.fetchall() if row[0]
        },
        "latest_alert": latest_obj.to_dict() if latest_obj else None,
    }


@router.get("/classifications")
async def list_classifications(db: AsyncSession = Depends(get_db)):
    """List all classification types present in the database with counts."""
    result = await db.execute(
        select(Object.classification, func.count(Object.oid))
        .where(Object.classification.isnot(None))
        .group_by(Object.classification)
        .order_by(desc(func.count(Object.oid)))
    )

    return {
        "classifications": [
            {"name": row[0], "count": row[1]}
            for row in result.fetchall()
        ]
    }
