"""Image proxy endpoints for telescope cutouts."""
import asyncio
import logging

import httpx
from fastapi import APIRouter, HTTPException, Response

router = APIRouter(prefix="/api/images", tags=["Images"])
logger = logging.getLogger(__name__)

_RETRY_ATTEMPTS = 3
_RETRY_DELAYS = [1.0, 2.0]  # seconds between attempt 1→2 and 2→3


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

    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                response = await client.get(url, params=params)
                if response.status_code == 429 and attempt < _RETRY_ATTEMPTS - 1:
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(
                        "legacysurvey.org returned 429 for RA=%s Dec=%s "
                        "(attempt %d/%d), retrying in %.1fs",
                        ra, dec, attempt + 1, _RETRY_ATTEMPTS, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                return Response(
                    content=response.content,
                    media_type="image/jpeg",
                    headers={
                        "Cache-Control": "public, max-age=86400",
                        "X-RA": str(ra),
                        "X-Dec": str(dec),
                    },
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                break

    logger.error("Failed to fetch cutout for RA=%s, Dec=%s: %s", ra, dec, last_exc)
    raise HTTPException(503, "Failed to fetch telescope image")
