"""Image proxy endpoints for telescope cutouts."""
import logging

import httpx
from fastapi import APIRouter, HTTPException, Response
from tenacity import AsyncRetrying, RetryError, retry_if_exception, stop_after_attempt, wait_exponential

router = APIRouter(prefix="/api/images", tags=["Images"])
logger = logging.getLogger(__name__)


def _is_429(exc: BaseException) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429


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
        "height": size,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=1, max=10),
                retry=retry_if_exception(_is_429),
                before_sleep=lambda rs: logger.warning(
                    "legacysurvey.org returned 429 for RA=%s Dec=%s, retrying (attempt %d/3)",
                    ra, dec, rs.attempt_number,
                ),
            ):
                with attempt:
                    response = await client.get(url, params=params)
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
    except (httpx.HTTPError, RetryError) as exc:
        logger.error("Failed to fetch cutout for RA=%s, Dec=%s: %s", ra, dec, exc)
        raise HTTPException(503, "Failed to fetch telescope image")
