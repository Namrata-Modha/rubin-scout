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
import math
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
from app.enrichment.crossmatch import EnrichmentService  # noqa: E402
from app.models.models import ClassificationProbability, Detection, GWEvent, Object  # noqa: E402
from app.security import limiter  # noqa: E402
from app.utils.observatories import OBSERVATORY_PRESETS  # noqa: E402
from app.validation import validate_classification, validate_oid  # noqa: E402

# MJD epoch (1858-11-17 00:00:00 UTC) — used for MJD↔datetime conversion
# without triggering astropy IERS or UT1 lookups.
_MJD_EPOCH = datetime(1858, 11, 17, tzinfo=timezone.utc)

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


# ---------------------------------------------------------------------------
# ILMT: Devasthal follow-up planning endpoint
# ---------------------------------------------------------------------------

# Transient types that bump priority
_PRIORITY_CLASSES = {"KN", "TDE", "SLSN", "FRB"}

# SIMBAD object types that indicate a well-catalogued, stable source
_KNOWN_STABLE_TYPES = {
    "Star", "s", "*", "**", "V*", "RR*", "EB*",   # stellar
    "Galaxy", "GiG", "BiC", "ClG", "GrG",          # galaxies / clusters
    "QSO", "AGN", "Sy1", "Sy2",                    # active nuclei (non-transient)
    "PN", "HII", "Neb",                            # nebulae
}


def _mjd_to_datetime(mjd: float) -> datetime:
    """Convert Modified Julian Date to a timezone-aware UTC datetime."""
    return _MJD_EPOCH + timedelta(days=mjd)


def _datetime_to_mjd(dt: datetime) -> float:
    """Convert a UTC datetime to Modified Julian Date."""
    return (dt - _MJD_EPOCH).total_seconds() / 86400.0


def _gw_sky_separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle separation in degrees between two sky positions."""
    r1, d1 = math.radians(ra1), math.radians(dec1)
    r2, d2 = math.radians(ra2), math.radians(dec2)
    cos_sep = (math.sin(d1) * math.sin(d2) +
               math.cos(d1) * math.cos(d2) * math.cos(r1 - r2))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


def _build_recommendation(
    ztf_history: list[dict],
    simbad: Optional[dict],
    gw_coincidence: list[dict],
    visibility: dict,
) -> tuple[str, str]:
    """
    Derive a recommendation code and plain-English reason.

    Returns:
        (recommendation, recommendation_reason)
    """
    has_new_activity = any(r["new_activity"] for r in ztf_history)
    has_priority_class = any(
        r.get("classification") in _PRIORITY_CLASSES for r in ztf_history
    )
    is_observable = visibility.get("observable", False)
    has_gw = bool(gw_coincidence)

    # --- PRIORITY_FOLLOWUP ---
    if has_gw:
        gw_ids = ", ".join(g["superevent_id"] for g in gw_coincidence)
        return (
            "PRIORITY_FOLLOWUP",
            f"Sky position is coincident with gravitational wave event(s) {gw_ids} "
            "within the 30-day post-merger window. Multi-messenger follow-up is strongly recommended.",
        )

    if has_new_activity and has_priority_class:
        cls = next(r["classification"] for r in ztf_history if r.get("classification") in _PRIORITY_CLASSES)
        obs_note = "Target is observable from Devasthal tonight." if is_observable else "Note: target is NOT observable from Devasthal tonight."
        return (
            "PRIORITY_FOLLOWUP",
            f"ZTF reports new activity after the query epoch for a {cls} candidate. "
            f"{obs_note} Prompt spectroscopic follow-up advised.",
        )

    if has_new_activity and is_observable:
        return (
            "PRIORITY_FOLLOWUP",
            "New ZTF detections post-date the query epoch and the target is observable "
            "from Devasthal tonight. Schedule ILMT/ARIES observations.",
        )

    # --- LIKELY_KNOWN ---
    if simbad and simbad.get("type") in _KNOWN_STABLE_TYPES:
        pre_only = all(r["pre_existing"] and not r["new_activity"] for r in ztf_history) if ztf_history else True
        if pre_only:
            return (
                "LIKELY_KNOWN",
                f"SIMBAD identifies this position as {simbad['name']} "
                f"(type: {simbad['type']}, separation: {simbad['distance_arcsec']:.1f}\"). "
                "All ZTF detections pre-date the query epoch — this is likely a catalogued source, not a new transient.",
            )

    if ztf_history and all(r["pre_existing"] and not r["new_activity"] for r in ztf_history):
        return (
            "LIKELY_KNOWN",
            "All ZTF detections pre-date the query epoch with no recent activity. "
            "This source was already active before the ILMT observation window.",
        )

    # --- NEEDS_MORE_DATA ---
    if not ztf_history and not simbad:
        return (
            "NEEDS_MORE_DATA",
            "No ZTF history and no SIMBAD counterpart within the search radius. "
            "The position is uncharacterised — additional multi-band imaging is needed to classify.",
        )

    if not is_observable:
        return (
            "NEEDS_MORE_DATA",
            "ZTF history is present but the target is not observable from Devasthal tonight. "
            "Re-evaluate when the target rises above 30° during dark time.",
        )

    return (
        "NEEDS_MORE_DATA",
        "Insufficient evidence to recommend priority follow-up. "
        "Monitor for new detections and check classification probability.",
    )


@router.get("/ilmt/followup")
@limiter.limit("30/minute")
async def ilmt_followup(
    request: Request,
    ra: float = Query(..., ge=0.0, le=360.0, description="Right Ascension (degrees, J2000)"),
    dec: float = Query(..., ge=-90.0, le=90.0, description="Declination (degrees, J2000)"),
    mjd: float = Query(..., ge=40000.0, le=80000.0, description="Modified Julian Date of ILMT observation"),
    radius_arcsec: float = Query(5.0, ge=0.5, le=300.0, description="Cone-search radius in arcseconds"),
    observatory_key: str = Query(
        "devasthal", max_length=50,
        description="Observatory preset key from /api/observatories. "
                    "Pass 'custom' and supply obs_lat/obs_lon/obs_elevation for a custom location. "
                    "Defaults to 'devasthal' (ARIES/ILMT).",
    ),
    obs_lat: Optional[float] = Query(None, ge=-90.0, le=90.0, description="Custom observer latitude (degrees N)"),
    obs_lon: Optional[float] = Query(None, ge=-180.0, le=180.0, description="Custom observer longitude (degrees E)"),
    obs_elevation: float = Query(0.0, ge=-500.0, le=5000.0, description="Custom observer elevation (metres)"),
    db: AsyncSession = Depends(get_db),
):
    """
    ILMT follow-up planning for a sky position observed at a given epoch.

    Returns ZTF transient history, SIMBAD cross-match, gravitational wave
    coincidences, visibility for the requested observatory tonight, and a
    follow-up recommendation for the ARIES/ILMT team.

    The observatory defaults to Devasthal (ARIES/ILMT). Pass observatory_key
    to use a different preset from /api/observatories, or observatory_key=custom
    together with obs_lat, obs_lon, obs_elevation for a custom site.
    """
    query_dt = _mjd_to_datetime(mjd)
    radius_meters = radius_arcsec * 30.87  # 1 arcsec ≈ 30.87 m on the geoid

    # Resolve the observatory for visibility computation.
    # Default is "devasthal" (set by the Query default above).
    if observatory_key == "custom" and obs_lat is not None and obs_lon is not None:
        vis_lat, vis_lon, vis_elevation = obs_lat, obs_lon, obs_elevation
        vis_observatory_name = "Custom location"
    elif observatory_key in OBSERVATORY_PRESETS:
        _obs = OBSERVATORY_PRESETS[observatory_key]
        vis_lat, vis_lon, vis_elevation = _obs["lat"], _obs["lon"], _obs["elevation_m"]
        vis_observatory_name = _obs["name"]
    else:
        # Unknown key — fall back to Devasthal gracefully
        _obs = OBSERVATORY_PRESETS["devasthal"]
        vis_lat, vis_lon, vis_elevation = _obs["lat"], _obs["lon"], _obs["elevation_m"]
        vis_observatory_name = _obs["name"]

    # ------------------------------------------------------------------
    # 1. ZTF history — spatial cone search on the objects table
    # ------------------------------------------------------------------
    ztf_result = await db.execute(
        text("""
            SELECT oid, ra, dec, classification, classification_probability,
                   first_detection, last_detection, n_detections, alert_url,
                   ST_Distance(
                       position,
                       ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography
                   ) / 30.87 AS distance_arcsec
            FROM objects
            WHERE ST_DWithin(
                position,
                ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography,
                :radius_meters
            )
            ORDER BY distance_arcsec
            LIMIT 50
        """),
        {"ra": ra, "dec": dec, "radius_meters": radius_meters},
    )
    ztf_rows = ztf_result.fetchall()

    ztf_history = []
    for row in ztf_rows:
        first_det_mjd = _datetime_to_mjd(row.first_detection) if row.first_detection else None
        last_det_mjd = _datetime_to_mjd(row.last_detection) if row.last_detection else None
        pre_existing = (first_det_mjd is not None) and (first_det_mjd < mjd)
        new_activity = (last_det_mjd is not None) and (last_det_mjd > mjd)
        oid = row.oid
        ztf_history.append({
            "oid": oid,
            "ra": row.ra,
            "dec": row.dec,
            "distance_arcsec": round(row.distance_arcsec, 2),
            "classification": row.classification,
            "classification_probability": row.classification_probability,
            "first_detection": row.first_detection.isoformat() if row.first_detection else None,
            "last_detection": row.last_detection.isoformat() if row.last_detection else None,
            "n_detections": row.n_detections,
            "pre_existing": pre_existing,
            "new_activity": new_activity,
            "alerce_url": f"https://alerce.online/object/{oid}",
        })

    # ------------------------------------------------------------------
    # 2 & 4. SIMBAD cross-match + Devasthal visibility — run concurrently
    # ------------------------------------------------------------------
    enrichment = EnrichmentService()

    simbad_task = asyncio.to_thread(
        enrichment._query_simbad, ra, dec, max(radius_arcsec, 10.0)
    )
    visibility_task = asyncio.to_thread(
        _compute_visibility,
        ra, dec,
        vis_lat, vis_lon, vis_elevation,
        None,  # tonight
    )

    simbad_raw, vis_full = await asyncio.gather(simbad_task, visibility_task)

    # Reformat SIMBAD result
    simbad = None
    if simbad_raw:
        simbad = {
            "name": simbad_raw.get("name"),
            "type": simbad_raw.get("otype"),
            "distance_arcsec": simbad_raw.get("distance_arcsec"),
        }

    # Strip the verbose hourly array from visibility — callers only need the summary
    visibility_devasthal = {
        "observatory_name": vis_observatory_name,
        "observable": vis_full.get("observable"),
        "max_altitude": vis_full.get("max_altitude"),
        "dark_start": vis_full.get("dark_start"),
        "dark_end": vis_full.get("dark_end"),
        "moon_separation": vis_full.get("moon_separation"),
        "observable_hours": vis_full.get("observable_hours"),
    }

    # ------------------------------------------------------------------
    # 3. GW coincidence — events within 30 days before the query epoch
    # ------------------------------------------------------------------
    gw_window_start = query_dt - timedelta(days=30)
    gw_result = await db.execute(
        select(GWEvent).where(
            GWEvent.event_time >= gw_window_start,
            GWEvent.event_time <= query_dt,
        )
    )
    gw_events_nearby = gw_result.scalars().all()

    gw_coincidence = []
    for gw in gw_events_nearby:
        props = gw.properties or {}
        ra_center = props.get("ra_center")
        dec_center = props.get("dec_center")
        area_90 = props.get("area_90_deg2")

        within_region = False
        separation_deg = None
        credible_radius_deg = None

        if ra_center is not None and dec_center is not None and area_90:
            # Approximate 90% credible region as a disk of equivalent area
            credible_radius_deg = math.sqrt(float(area_90) / math.pi)
            separation_deg = _gw_sky_separation_deg(ra, dec, float(ra_center), float(dec_center))
            within_region = separation_deg <= credible_radius_deg
        else:
            # No skymap — include all temporally coincident events flagged as unlocalized
            within_region = True

        if within_region:
            gw_coincidence.append({
                "superevent_id": gw.superevent_id,
                "event_time": gw.event_time.isoformat() if gw.event_time else None,
                "far": gw.far,
                "classification": gw.classification or {},
                "separation_deg": round(separation_deg, 2) if separation_deg is not None else None,
                "credible_radius_deg": round(credible_radius_deg, 2) if credible_radius_deg is not None else None,
                "localized": ra_center is not None,
            })

    # ------------------------------------------------------------------
    # 5. Recommendation engine
    # ------------------------------------------------------------------
    recommendation, recommendation_reason = _build_recommendation(
        ztf_history, simbad, gw_coincidence, visibility_devasthal
    )

    return {
        "query": {
            "ra": ra,
            "dec": dec,
            "mjd": mjd,
            "radius_arcsec": radius_arcsec,
        },
        "ztf_history": ztf_history,
        "simbad": simbad,
        "gw_coincidence": gw_coincidence,
        "visibility_devasthal": visibility_devasthal,
        "recommendation": recommendation,
        "recommendation_reason": recommendation_reason,
    }
