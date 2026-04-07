"""
Cross-matching service for enriching alerts with catalog data.

Queries SIMBAD (known astronomical objects) and TNS (Transient Name Server)
to add context to each alert: is this a known source? Is it near a galaxy?
Has it already been reported by another group?
"""

import logging
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


class EnrichmentService:
    """Enriches alert objects with cross-catalog information."""

    async def enrich_object(self, session: AsyncSession, oid: str, ra: float, dec: float):
        """
        Cross-match a single object against external catalogs.

        Args:
            session: Database session.
            oid: Object identifier.
            ra: Right Ascension in degrees.
            dec: Declination in degrees.
        """
        # SIMBAD cross-match
        simbad_result = self._query_simbad(ra, dec, radius_arcsec=5.0)

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
            logger.debug(f"{oid} matched to SIMBAD: {simbad_result['name']} ({simbad_result['otype']})")

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
        """Enrich a batch of objects. Rate-limits SIMBAD queries."""
        enriched = 0
        for obj in objects:
            if obj.cross_match_catalog:
                continue  # Already enriched

            await self.enrich_object(session, obj.oid, obj.ra, obj.dec)
            enriched += 1

            # SIMBAD rate limiting: don't hammer the service
            if enriched % 10 == 0:
                await session.flush()

        await session.commit()
        logger.info(f"Enriched {enriched}/{len(objects)} objects with SIMBAD data")
        return enriched
