"""
Cross-matching service for enriching alerts with catalog data.

Queries SIMBAD (known astronomical objects) and TNS (Transient Name Server)
to add context to each alert: is this a known source? Is it near a galaxy?
Has it already been reported by another group?
"""

import asyncio
import logging
import time
from typing import Optional

import astropy.units as u
from astropy.coordinates import SkyCoord
from astroquery.simbad import Simbad
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Object

logger = logging.getLogger(__name__)

# Configure SIMBAD to return the fields we need
try:
    Simbad.add_votable_fields("otype", "distance_result")
except Exception:
    pass  # Newer astroquery versions include these by default
Simbad.TIMEOUT = 10

# Politeness delay between successive SIMBAD cone searches. SIMBAD asks clients
# not to hammer the service; one query per second is well within their limits
# while keeping a 50-object batch bounded (~50s of wall clock, none of it
# blocking the event loop because the query itself runs in a worker thread).
SIMBAD_RATE_LIMIT_SECONDS = 1.0

# Hard ceilings, because astroquery gives us none that work. In 0.4.x
# ``Simbad.TIMEOUT`` is a dead attribute (the live one is ``Simbad.timeout``,
# default 1080 s, a server-side execution limit), and the pyvo capabilities
# request that precedes every query has no connect timeout at all, so an
# unreachable SIMBAD blocks until the OS gives up (~127 s on Linux). Fifty
# objects at ~135 s each is ~1h52m, which is how one cycle ran almost two hours.
SIMBAD_CALL_TIMEOUT_SECONDS = 15.0
# Whole-step budget: the step ends cleanly when it is spent, and the objects
# not reached are simply picked up by the next cycle.
SIMBAD_STEP_BUDGET_SECONDS = 300.0
# wait_for abandons the await but cannot kill the worker thread, which keeps
# blocking until its socket gives up. When SIMBAD is down, stop after this many
# timeouts in a row instead of leaking a thread per remaining object.
SIMBAD_MAX_CONSECUTIVE_TIMEOUTS = 3


class EnrichmentService:
    """Enriches alert objects with cross-catalog information."""

    async def enrich_object(self, session: AsyncSession, oid: str, ra: float, dec: float) -> bool:
        """
        Cross-match a single object against external catalogs.

        Args:
            session: Database session.
            oid: Object identifier.
            ra: Right Ascension in degrees.
            dec: Declination in degrees.

        Returns:
            True if SIMBAD answered (match or no match), False if the call
            exceeded SIMBAD_CALL_TIMEOUT_SECONDS.
        """
        # SIMBAD cross-match. Simbad.query_region is a synchronous, network-
        # blocking call; run it in a worker thread so the asyncio event loop
        # (and the whole API) stays responsive during ingestion.
        try:
            simbad_result = await asyncio.wait_for(
                asyncio.to_thread(self._query_simbad, ra, dec, 5.0),
                timeout=SIMBAD_CALL_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                f"SIMBAD query for {oid} exceeded {SIMBAD_CALL_TIMEOUT_SECONDS:.0f}s; skipping"
            )
            return False

        if simbad_result:
            await session.execute(
                update(Object)
                .where(Object.oid == oid)
                .values(
                    cross_match_catalog="SIMBAD",
                    cross_match_name=simbad_result["name"],
                    cross_match_type=simbad_result["otype"],
                    cross_match_distance_arcsec=simbad_result["distance_arcsec"],
                )
            )
            # Commit per object so the write transaction lasts milliseconds
            # rather than spanning every later SIMBAD call.
            await session.commit()
            logger.debug(f"{oid} matched to SIMBAD: {simbad_result['name']} ({simbad_result['otype']})")
        return True

    def _query_simbad(self, ra: float, dec: float, radius_arcsec: float = 5.0) -> Optional[dict]:
        """
        Query SIMBAD for objects near a given sky position.

        Args:
            ra: Right Ascension in degrees.
            dec: Declination in degrees.
            radius_arcsec: Search radius in arcseconds.

        Returns:
            Dict with name, otype, distance_arcsec or None if no match.
        """
        try:
            coord = SkyCoord(ra=ra, dec=dec, unit=(u.degree, u.degree), frame="icrs")
            result = Simbad.query_region(coord, radius=radius_arcsec * u.arcsec)

            if result is None or len(result) == 0:
                return None

            # Take the closest match
            # Column names vary by astroquery version (MAIN_ID vs main_id)
            id_col = 'main_id' if 'main_id' in result.colnames else 'MAIN_ID'
            type_col = 'otype' if 'otype' in result.colnames else 'OTYPE'
            dist_col = next(
                (c for c in result.colnames if 'dist' in c.lower() or 'DISTANCE' in c),
                None
            )
            row = result[0]
            return {
                "name": str(row[id_col]),
                "otype": str(row[type_col]) if type_col in result.colnames else "unknown",
                "distance_arcsec": float(row[dist_col]) if dist_col else 0.0,
            }

        except Exception as e:
            logger.warning(f"SIMBAD query failed for ({ra}, {dec}): {e}")
            return None

    async def enrich_batch(self, session: AsyncSession, objects: list[Object]):
        """Enrich a batch of objects against SIMBAD.

        Rate limiting: a real ``asyncio.sleep`` of ``SIMBAD_RATE_LIMIT_SECONDS``
        is awaited after each SIMBAD query so the service is not hammered. The
        sleep is non-blocking, so the event loop remains free for API traffic.

        FRBs are skipped: CHIME/FRB positions carry ~arcminute uncertainty, far
        larger than the 5-arcsec SIMBAD cone, so any match would be a chance
        coincidence. This mirrors the exclusion in the scheduler query and
        guards callers that reach this method by another path.

        Bounded: each call is capped at SIMBAD_CALL_TIMEOUT_SECONDS, the step
        as a whole at SIMBAD_STEP_BUDGET_SECONDS, and it gives up early after
        SIMBAD_MAX_CONSECUTIVE_TIMEOUTS timeouts in a row. No database
        transaction is held open while waiting on SIMBAD.
        """
        # Snapshot plain values, then end the caller's read transaction. The
        # SELECT that produced `objects` left one open ("idle in transaction"),
        # and it would otherwise stay open for the whole network-bound loop,
        # blocking any DDL queued behind it.
        pending = [
            (obj.oid, obj.ra, obj.dec)
            for obj in objects
            if not obj.cross_match_catalog and obj.classification != "FRB"
        ]
        await session.commit()

        enriched = 0
        timeouts = 0
        deadline = time.monotonic() + SIMBAD_STEP_BUDGET_SECONDS
        for i, (oid, ra, dec) in enumerate(pending):
            if time.monotonic() >= deadline:
                logger.warning(
                    f"SIMBAD step budget ({SIMBAD_STEP_BUDGET_SECONDS:.0f}s) spent; "
                    f"ending with {len(pending) - i} objects left for the next cycle"
                )
                break

            if await self.enrich_object(session, oid, ra, dec):
                enriched += 1
                timeouts = 0
            else:
                timeouts += 1
                if timeouts >= SIMBAD_MAX_CONSECUTIVE_TIMEOUTS:
                    logger.warning(
                        f"SIMBAD timed out {timeouts} times in a row; assuming it is "
                        f"unreachable and ending the step"
                    )
                    break
                continue

            # SIMBAD rate limiting: space out successive queries.
            await asyncio.sleep(SIMBAD_RATE_LIMIT_SECONDS)

        logger.info(f"Enriched {enriched}/{len(objects)} objects with SIMBAD data")
        return enriched
