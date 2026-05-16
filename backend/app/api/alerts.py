"""
API routes for querying and exploring alerts.

Security:
- Rate limited: 60 req/min for reads
- Classification filter validated against allowlist
- OID validated against ZTF naming pattern
- All string inputs length-limited
- Parameterized queries only (no SQL injection risk)
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import astropy.units as u
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body, get_sun
from astropy.time import Time
from astropy.utils import iers
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Disable IERS-A network downloads so astropy never blocks on a remote fetch.
# The built-in IERS-B table (shipped with astropy) is accurate to ~1 arcsec,
# which is more than enough for visibility planning.
iers.conf.auto_download = False
iers.conf.auto_max_age = None

from app.database import get_db  # noqa: E402
from app.models.models import ClassificationProbability, Detection, Object  # noqa: E402
from app.security import limiter  # noqa: E402
from app.utils.observatories import OBSERVATORY_PRESETS  # noqa: E402
from app.validation import validate_classification, validate_oid  # noqa: E402

router = APIRouter(prefix="/api", tags=["alerts"])


@router.get("/alerts/recent")
@limiter.limit("60/minute")
async def get_recent_alerts(
    request: Request,  # Required by slowapi
    classification: Optional[str] = Query(
        None, max_length=20, description="Filter by class (SNIa, SNII, AGN, etc.)"
    ),
    min_probability: float = Query(0.5, ge=0.0, le=1.0),
    hours: int = Query(24, ge=1, le=87600),
    limit: int = Query(12, ge=1, le=100),  # Tightened from 500
    offset: int = Query(0, ge=0, le=10000),
    db: AsyncSession = Depends(get_db),
):
    """Get recent transient alerts, filtered and sorted by last detection."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # SECURITY: Validate classification against allowlist
    safe_classification = validate_classification(classification)

    base_query = (
        select(Object)
        .where(Object.last_detection >= cutoff)
        .where(Object.classification_probability >= min_probability)
    )

    if safe_classification:
        base_query = base_query.where(Object.classification == safe_classification)

    # Total count for pagination
    count_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total = count_result.scalar() or 0

    # Paginated results
    query = base_query.order_by(desc(Object.last_detection)).limit(limit).offset(offset)
    result = await db.execute(query)
    objects = result.scalars().all()

    return {
        "count": len(objects),
        "total": total,
        "limit": limit,
        "offset": offset,
        "alerts": [obj.to_dict() for obj in objects],
    }


def _compute_visibility(ra: float, dec: float, lat: float, lon: float, elevation: float, date_str: Optional[str]) -> dict:
    """
    Compute tonight's visibility for a sky target from an observer location.
    Runs synchronously; call via asyncio.to_thread to avoid blocking.

    Uses only built-in astropy ephemerides (no runtime data downloads).
    """
    if date_str:
        try:
            base_date = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
        except ValueError:
            base_date = datetime.now(timezone.utc)
    else:
        base_date = datetime.now(timezone.utc)

    # Build 25 hourly time steps covering tonight (midnight UTC → next midnight)
    start_of_night = base_date.replace(hour=0, minute=0, second=0, microsecond=0)
    times_utc = [start_of_night + timedelta(hours=h) for h in range(25)]
    # scale='utc' avoids the UT1 lookup that would trigger an IERS download
    # times_ap = Time([t.isoformat() for t in times_utc], scale="utc")
    times_ap = Time(
        [t.replace(tzinfo=None).isoformat() for t in times_utc],
        scale="utc"
    )

    location = EarthLocation.from_geodetic(
        lon=lon * u.deg, lat=lat * u.deg, height=elevation * u.m
    )
    altaz_frames = AltAz(obstime=times_ap, location=location)

    target = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
    target_alts = target.transform_to(altaz_frames).alt.deg

    # get_sun uses a fast analytical formula — no ephemeris download needed
    sun_alts = get_sun(times_ap).transform_to(altaz_frames).alt.deg

    # Moon: use the built-in low-precision ephemeris so no JPL data is fetched.
    # ephemeris='builtin' is always available; it's accurate to ~1 arcmin.
    moon_sep = None
    try:
        midtime = times_ap[12]
        moon = get_body("moon", midtime, location, ephemeris="builtin")
        moon_sep = round(float(target.separation(moon).deg), 1)
    except Exception as exc:
        logger.warning("Moon position calculation failed, omitting: %s", exc)

    # Astronomical dark time: sun below −18 deg
    dark_start = None
    dark_end = None
    for t, sun_alt in zip(times_utc, sun_alts):
        if float(sun_alt) < -18:
            if dark_start is None:
                dark_start = t
            dark_end = t

    # Observable: target exceeds 30 deg during dark time for at least 1 hour
    observable_hours = sum(
        1 for t, alt, sun_alt in zip(times_utc, target_alts, sun_alts)
        if float(sun_alt) < -18 and float(alt) > 30
    )

    hourly_altitudes = [
        {
            "time": t.isoformat(),
            "altitude": round(float(alt), 2),
            "sun_altitude": round(float(sun_alt), 2),
        }
        for t, alt, sun_alt in zip(times_utc, target_alts, sun_alts)
    ]

    return {
        "hourly_altitudes": hourly_altitudes,
        "dark_start": dark_start.isoformat() if dark_start else None,
        "dark_end": dark_end.isoformat() if dark_end else None,
        "moon_separation": moon_sep,
        "observable": observable_hours >= 1,
        "max_altitude": round(float(max(target_alts)), 1),
        "observable_hours": observable_hours,
    }


@router.get("/alerts/{oid}/visibility")
@limiter.limit("30/minute")
async def get_visibility(
    request: Request,
    oid: str,
    lat: float = Query(..., ge=-90, le=90, description="Observer latitude (degrees N)"),
    lon: float = Query(..., ge=-180, le=180, description="Observer longitude (degrees E)"),
    elevation: float = Query(0.0, ge=-500, le=5000, description="Observer elevation (metres)"),
    date: Optional[str] = Query(None, description="ISO date string (UTC). Defaults to tonight."),
    db: AsyncSession = Depends(get_db),
):
    """Compute tonight's visibility for a transient from a given observer location."""
    try:
        oid = validate_oid(oid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid object ID format")

    result = await db.execute(select(Object).where(Object.oid == oid))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")

    # Run astropy computation in a thread to avoid blocking the event loop
    try:
        vis = await asyncio.to_thread(
            _compute_visibility, obj.ra, obj.dec, lat, lon, elevation, date
        )
    except Exception as e:
        logger.error("Visibility computation failed for %s: %s: %s", oid, type(e).__name__, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Visibility computation failed ({type(e).__name__}): {e}",
        )

    return vis


@router.get("/alerts/{oid}")
@limiter.limit("60/minute")
async def get_alert_detail(request: Request, oid: str, db: AsyncSession = Depends(get_db)):
    """Full detail for a single object."""
    # SECURITY: Validate OID format
    try:
        oid = validate_oid(oid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid object ID format")

    result = await db.execute(select(Object).where(Object.oid == oid))
    obj = result.scalar_one_or_none()

    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")

    det_result = await db.execute(
        select(Detection).where(Detection.oid == oid).order_by(Detection.mjd)
    )
    detections = det_result.scalars().all()

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
@limiter.limit("30/minute")  # Spatial queries are heavier, lower limit
async def cone_search(
    request: Request,
    ra: float = Query(..., ge=0, le=360, description="Right Ascension in degrees"),
    dec: float = Query(..., ge=-90, le=90, description="Declination in degrees"),
    radius: float = Query(60, ge=1, le=3600, description="Search radius in arcseconds"),
    db: AsyncSession = Depends(get_db),
):
    """Find all objects within a radius of a sky position."""
    # SECURITY: Uses parameterized query, safe from SQL injection
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
@limiter.limit("30/minute")
async def get_summary_stats(
    request: Request,
    hours: int = Query(24, ge=1, le=87600),
    db: AsyncSession = Depends(get_db),
):
    """Summary statistics for the dashboard."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    class_counts = await db.execute(
        select(Object.classification, func.count(Object.oid))
        .where(Object.last_detection >= cutoff)
        .group_by(Object.classification)
        .order_by(desc(func.count(Object.oid)))
    )

    total = await db.execute(
        select(func.count(Object.oid)).where(Object.last_detection >= cutoff)
    )

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


@router.get("/observatories")
@limiter.limit("60/minute")
async def list_observatories(request: Request):
    """List built-in observatory presets for visibility planning."""
    return {"observatories": OBSERVATORY_PRESETS}


@router.get("/classifications")
@limiter.limit("30/minute")
async def list_classifications(request: Request, db: AsyncSession = Depends(get_db)):
    """List all classification types present in the database."""
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
