"""
API routes for gravitational wave events and multi-messenger cross-matching.

This is Rubin Scout's unique feature: connecting LIGO gravitational wave
detections with optical transients from ZTF/Rubin.
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.enrichment.gw_crossmatch import GWCrossMatchService

router = APIRouter(prefix="/api/gw", tags=["gravitational-waves"])
gw_service = GWCrossMatchService()


@router.get("/events")
async def list_gw_events(db: AsyncSession = Depends(get_db)):
    """
    List all gravitational wave events with human-friendly descriptions.
    Includes merger type, distance, sky area, and candidate count.
    """
    events = await gw_service.get_all_events(db)
    return {"count": len(events), "events": events}


@router.get("/events/{superevent_id}")
async def get_gw_event(superevent_id: str, db: AsyncSession = Depends(get_db)):
    """Get a single GW event with its description and candidates."""
    events = await gw_service.get_all_events(db)
    event = next((e for e in events if e["superevent_id"] == superevent_id), None)
    if not event:
        raise HTTPException(status_code=404, detail=f"GW event {superevent_id} not found")
    return event


@router.post("/events/{superevent_id}/crossmatch")
async def run_cross_match(
    superevent_id: str,
    search_radius_deg: float = Query(15.0, ge=1, le=90, description="Search radius in degrees"),
    time_window_days: float = Query(30.0, ge=1, le=365, description="Days after event to search"),
    db: AsyncSession = Depends(get_db),
):
    """
    Run cross-matching between a GW event and optical transients.

    Finds all transients in the database that fall within the GW event's
    credible sky region and time window. This is how astronomers find
    the optical counterpart to a gravitational wave detection.
    """
    try:
        candidates = await gw_service.cross_match_event(
            db, superevent_id, search_radius_deg, time_window_days
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "superevent_id": superevent_id,
        "search_radius_deg": search_radius_deg,
        "time_window_days": time_window_days,
        "n_candidates": len(candidates),
        "candidates": candidates,
    }


@router.get("/events/{superevent_id}/candidates")
async def get_candidates(superevent_id: str, db: AsyncSession = Depends(get_db)):
    """Get previously found counterpart candidates for a GW event."""
    try:
        candidates = await gw_service.cross_match_event(db, superevent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "superevent_id": superevent_id,
        "n_candidates": len(candidates),
        "candidates": candidates,
    }


@router.post("/seed")
async def seed_gw_events(db: AsyncSession = Depends(get_db)):
    """Seed the database with notable GW events from GWTC catalogs."""
    count = await gw_service.seed_gw_events(db)
    return {"status": "ok", "events_seeded": count}
