"""
Live alert stream endpoints — backed by the alerts_live table.

Routes:
  GET /api/alerts/live                       Paginated live alerts with optional classification filter
  GET /api/alerts/live/classifications       Distinct classification values + row counts
  GET /api/live-alerts/live/{external_id}    Single alert detail with parsed raw_payload fields
"""

import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import AlertLive
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
    alert_type: Optional[str] = Query(
        None,
        description="Filter by survey/pipeline, e.g. ztf_fink or lsst_fink",
    ),
    db: AsyncSession = Depends(get_db),
):
    """Return paginated rows from alerts_live, most recently detected first.

    `alert_type` filters server-side (same pattern as GW's `significance`
    query param) rather than client-side, so pagination totals stay correct
    when combined with `classification` -- ZTF and LSST/Rubin alerts share
    this table but use two incompatible classification vocabularies (see
    lsst_service.py's module docstring), so a caller narrowing to one survey
    needs the count/offset to reflect that survey alone, not the full table.
    """
    stmt = select(AlertLive).order_by(AlertLive.detected_at.desc())
    count_stmt = select(func.count()).select_from(AlertLive)

    if classification:
        stmt = stmt.where(AlertLive.classification == classification)
        count_stmt = count_stmt.where(AlertLive.classification == classification)

    if alert_type:
        stmt = stmt.where(AlertLive.alert_type == alert_type)
        count_stmt = count_stmt.where(AlertLive.alert_type == alert_type)

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
    """Return distinct (alert_type, classification) pairs in alerts_live with
    row counts.

    Grouped by alert_type as well as classification: ZTF and LSST/Rubin
    alerts share this column but use two incompatible vocabularies (Fink's
    fixed ZTF class strings vs. LSST's matched tag names -- see
    lsst_service.py's module docstring). Without alert_type in each row, a
    caller has no way to tell the two apart other than pattern-matching the
    label text itself, which duplicates backend knowledge in the frontend.
    """
    stmt = (
        select(
            AlertLive.alert_type,
            AlertLive.classification,
            func.count().label("count"),
        )
        .where(AlertLive.classification.isnot(None))
        .group_by(AlertLive.alert_type, AlertLive.classification)
        .order_by(func.count().desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    classifications = [
        {
            "classification": row.classification,
            "alert_type": row.alert_type,
            "count": row.count,
        }
        for row in rows
    ]
    return {"classifications": classifications, "total": sum(r["count"] for r in classifications)}


# ---------------------------------------------------------------------------
# Detail endpoint — separate router so the URL is /api/live-alerts/live/{id}
# ---------------------------------------------------------------------------

# LSST calibrates flux in nanojansky, which fixes the AB zeropoint at 31.4:
#     AB mag = 31.4 - 2.5 * log10(flux / nJy)
# That is what lets an LSST alert be described in the same "lower number =
# brighter" terms the ZTF pages already use, instead of inventing a second
# brightness vocabulary for the same platform.
_LSST_AB_ZEROPOINT = 31.4

# Fink writes -1.0 into a classifier score it did not run, so a negative
# score means "not scored", not "scored zero". Those are omitted rather than
# rendered as a 0% bar.
_NOT_SCORED = -1.0


def _flux_to_ab_magnitude(flux_njy: float | None) -> float | None:
    """AB magnitude for a positive nanojansky flux, else None.

    Difference-image flux is routinely NEGATIVE -- the source is fainter than
    the template -- and a negative flux has no magnitude at all. Returning
    None rather than letting log10 produce NaN is what keeps the caller
    honest about that.
    """
    if flux_njy is None or flux_njy <= 0:
        return None
    return _LSST_AB_ZEROPOINT - 2.5 * math.log10(flux_njy)


def _lsst_band_label(band: str | None) -> str | None:
    """Human label for an LSST filter letter."""
    return {
        "u": "u (ultraviolet)", "g": "g (green)", "r": "r (red)",
        "i": "i (near-infrared)", "z": "z (infrared)", "y": "y (infrared)",
    }.get(band, band)


def _snr_explanation(snr: float | None) -> str | None:
    """Plain-English reading of a signal-to-noise ratio."""
    if snr is None:
        return None
    if snr >= 20:
        return "Overwhelmingly significant — a real change, not noise."
    if snr >= 10:
        return "A strong, confident detection."
    if snr >= 5:
        return "A solid detection, above the usual reporting threshold."
    return "A marginal detection — treat with caution."


def _extract_lsst_fields(p: dict) -> dict:
    """Semantic fields for one Rubin/LSST alert.

    Returns names the page can render directly -- brightness.magnitude,
    quality.reliability -- never raw Fink keys, so the flux conversion and
    the not-scored rules live here once where they are testable rather than
    being re-derived in JSX.

    Sections with no LSST analogue are OMITTED, not emitted empty: the ZTF
    page's kilonova and SLSN bars have no counterpart in this schema, and
    rendering them as permanent dashes is exactly the bug this replaces.
    """
    flux = p.get("r:psfFlux")
    magnitude = _flux_to_ab_magnitude(flux)
    mjd = p.get("r:midpointMjdTai")

    brightness: dict = {
        "flux_njy": flux,
        "flux_err_njy": p.get("r:psfFluxErr"),
        "band": _lsst_band_label(p.get("r:band")),
        "snr": p.get("r:snr"),
        "snr_explanation": _snr_explanation(p.get("r:snr")),
        "magnitude": magnitude,
    }
    if magnitude is not None:
        brightness["trend"] = "brightening"
        brightness["explanation"] = (
            "Brighter than the reference image. Magnitude runs backwards — "
            "a lower number means a brighter source."
        )
    elif flux is not None:
        brightness["trend"] = "fading"
        brightness["explanation"] = (
            "Fading — currently dimmer than the reference image. Rubin "
            "measures the difference between the two, which can be negative, "
            "and a negative difference has no magnitude."
        )

    # Only scores Fink actually ran. clf_cats_score is reported WITHOUT a
    # class label: f:clf_cats_class is a bare integer and this repo has no
    # documented CATS taxonomy to turn it into a name, so inventing one would
    # be worse than omitting it.
    score_defs = [
        ("cats", "f:clf_cats_score", "CATS classifier confidence",
         "Fink's CATS model, trained for Rubin. Its confidence in its own "
         "top class; the class index itself is not published in a form this "
         "platform can name."),
        ("snn_sn_vs_others", "f:clf_snnSnVsOthers_score",
         "SuperNNova: supernova vs other",
         "The same SuperNNova family used on ZTF. How likely this is a "
         "supernova rather than something else."),
        ("early_snia", "f:clf_earlySNIa_score", "Early Type Ia",
         "How likely this is a Type Ia caught early."),
    ]
    scores = []
    for key, field, label, hint in score_defs:
        value = p.get(field)
        if value is None or value <= _NOT_SCORED:
            continue
        scores.append({"key": key, "label": label, "hint": hint, "value": value})

    # Cross-match is a set of per-catalog f:xm_* fields, nothing like ZTF's
    # single d:cdsxmatch string. Only populated ones are returned.
    xm_defs = [
        ("simbad_otype", "f:xm_simbad_otype", "SIMBAD type"),
        ("tns_name", "f:xm_tns_fullname", "TNS name"),
        ("gaia_class", "f:xm_gaia_class", "Gaia DR3"),
        ("legacy_class", "f:xm_ls_class", "Legacy Survey DR8"),
        ("vsx_type", "f:xm_vsx_type", "AAVSO VSX"),
    ]
    crossmatch = []
    for key, field, label in xm_defs:
        value = p.get(field)
        # "Fail" is Fink's own marker for a cross-match that did not resolve.
        if value in (None, "", "Fail"):
            continue
        crossmatch.append({"key": key, "label": label, "value": value})

    fields: dict = {
        "survey": "lsst_fink",
        "object_id": str(p["r:diaObjectId"]) if p.get("r:diaObjectId") else None,
        "coords": {
            "ra": p.get("r:ra"),
            "dec": p.get("r:dec"),
            # LSST carries no JD; this is the exact MJD identity, not an
            # approximation.
            "jd": (mjd + 2400000.5) if mjd is not None else None,
            "mjd": mjd,
        },
        "brightness": brightness,
    }

    reliability = p.get("r:reliability")
    if reliability is not None:
        fields["quality"] = {
            "reliability": reliability,
            "reliability_version": p.get("r:reliabilityVersion"),
            "hint": (
                "Rubin's own real-vs-artefact score. A different algorithm "
                "and scale from ZTF's Real/Bogus, so it is deliberately not "
                "given a verdict label."
            ),
        }
    if scores:
        fields["classifier_scores"] = scores
    if crossmatch:
        fields["crossmatch"] = crossmatch
    return fields


def _extract_ztf_fields(p: dict) -> dict:
    """Pull the documented Fink/ZTF fields out of raw_payload into a typed dict.

    All fields are optional — the payload shape can vary between Fink classes
    and schema revisions, so every access uses .get() with a None default.
    """
    return {
        "survey": "ztf_fink",
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


def _extract_payload_fields(payload: dict, alert_type: str | None = None) -> dict:
    """Parse one alert's raw_payload into fields the detail page can render.

    Dispatches on `alert_type` because ZTF and Rubin/LSST share this table
    but not their schema: flux photometry instead of magnitudes,
    r:reliability instead of d:drb, per-catalog f:xm_* fields instead of a
    single d:cdsxmatch. One layout with per-field conditionals would leave
    most of an LSST page permanently blank, so each survey gets its own
    extraction and its own body.

    Anything that is not an LSST alert is parsed as ZTF, preserving the
    behaviour every existing caller already depends on.
    """
    p = payload or {}
    if alert_type == "lsst_fink":
        return _extract_lsst_fields(p)
    return _extract_ztf_fields(p)


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

    payload_fields = _extract_payload_fields(row.raw_payload or {}, row.alert_type)

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
