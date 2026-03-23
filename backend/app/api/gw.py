"""
API routes for gravitational wave events and cross-matching.

Security:
- Seed endpoint requires admin API key in production
- Rate limited: 60/min reads, 10/min writes
- superevent_id validated against GW naming pattern
- Search parameters bounded with sensible limits
"""

from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.enrichment.gw_crossmatch import GWCrossMatchService
from app.security import limiter, require_admin_key
from app.validation import validate_superevent_id

router = APIRouter(prefix="/api/gw", tags=["gravitational-waves"])
gw_service = GWCrossMatchService()


@router.get("/events")
@limiter.limit("60/minute")
async def list_gw_events(request: Request, db: AsyncSession = Depends(get_db)):
    """List all gravitational wave events with descriptions."""
    events = await gw_service.get_all_events(db)
    return {"count": len(events), "events": events}


@router.get("/events/{superevent_id}")
@limiter.limit("60/minute")
async def get_gw_event(request: Request, superevent_id: str, db: AsyncSession = Depends(get_db)):
    """Get a single GW event."""
    # SECURITY: Validate superevent ID format
    try:
        superevent_id = validate_superevent_id(superevent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid superevent ID format")

    events = await gw_service.get_all_events(db)
    event = next((e for e in events if e["superevent_id"] == superevent_id), None)
    if not event:
        raise HTTPException(status_code=404, detail="GW event not found")
    return event


@router.post("/events/{superevent_id}/crossmatch")
@limiter.limit("10/minute")  # Cross-matching is compute-heavy
async def run_cross_match(
    request: Request,
    superevent_id: str,
    search_radius_deg: float = Query(15.0, ge=1, le=90),
    time_window_days: float = Query(30.0, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Run cross-matching between a GW event and optical transients."""
    try:
        superevent_id = validate_superevent_id(superevent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid superevent ID format")

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
@limiter.limit("30/minute")
async def get_candidates(request: Request, superevent_id: str, db: AsyncSession = Depends(get_db)):
    """Get counterpart candidates for a GW event."""
    try:
        superevent_id = validate_superevent_id(superevent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid superevent ID format")

    try:
        candidates = await gw_service.cross_match_event(db, superevent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {
        "superevent_id": superevent_id,
        "n_candidates": len(candidates),
        "candidates": candidates,
    }


# SECURITY: Seed endpoint requires admin key in production.
# Prevents anyone from re-seeding or manipulating the database.
@router.post("/seed", dependencies=[Depends(require_admin_key)])
@limiter.limit("5/minute")
async def seed_gw_events(request: Request, db: AsyncSession = Depends(get_db)):
    """Seed GW events. Requires X-API-Key header in production."""
    count = await gw_service.seed_gw_events(db)
    return {"status": "ok", "events_seeded": count}
