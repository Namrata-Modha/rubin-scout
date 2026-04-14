"""Image proxy endpoints for telescope cutouts."""
import logging

import httpx
from fastapi import APIRouter, HTTPException, Response

router = APIRouter(prefix="/api/images", tags=["Images"])
logger = logging.getLogger(__name__)


@router.get("/cutout")
async def get_cutout(
    ra: float,
    dec: float,
    size: int = 200,
    pixscale: float = 0.5,
    layer: str = "ls-dr10"
):
    """
    Proxy for Legacy Survey cutout images.

    Args:
        ra: Right Ascension in degrees
        dec: Declination in degrees
        size: Image size in pixels (default 200)
        pixscale: Arcseconds per pixel (default 0.5)
        layer: Survey layer (default ls-dr10)
    """
    # Validate inputs
    if not (-90 <= dec <= 90):
        raise HTTPException(400, "Declination must be between -90 and 90")
    if not (0 <= ra < 360):
        raise HTTPException(400, "Right Ascension must be between 0 and 360")
    if not (50 <= size <= 1000):
        raise HTTPException(400, "Size must be between 50 and 1000 pixels")

    url = "https://www.legacysurvey.org/viewer/cutout.jpg"
    params = {
        "ra": ra,
        "dec": dec,
        "layer": layer,
        "pixscale": pixscale,
        "width": size,
        "height": size
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()

            return Response(
                content=response.content,
                media_type="image/jpeg",
                headers={
                    "Cache-Control": "public, max-age=86400",  # Cache 24 hours
                    "X-RA": str(ra),
                    "X-Dec": str(dec)
                }
            )
    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch cutout for RA={ra}, Dec={dec}: {e}")
        raise HTTPException(503, "Failed to fetch telescope image")
