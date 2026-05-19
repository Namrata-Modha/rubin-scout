"""
Gravitational Wave Cross-Matching Service.

When LIGO/Virgo/KAGRA detect a gravitational wave event, this service
finds optical transient candidates in our database that fall within
the GW skymap's credible region.

This is THE unique feature of Rubin Scout. No other downstream broker
tool does this automatically for curious humans.

Works on Windows (no healpy dependency). Uses astropy_healpix or
falls back to angular distance matching.
"""

import logging
import math
from datetime import datetime, timedelta, timezone

import httpx
from astropy.time import Time
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import GWCandidate, GWEvent, Object

logger = logging.getLogger(__name__)

# Public GW events from GWTC catalogs (no authentication needed)
GRACEDB_PUBLIC_URL = "https://gracedb.ligo.org/apiweb/superevents"

# GWOSC public catalog API
GWOSC_API_URL = "https://gwosc.org/eventapi/json/allevents/"

# Human-readable descriptions for notable events only
DESCRIPTIONS = {
    "GW170817": (
        "The first gravitational wave event with an electromagnetic counterpart. "
        "Two neutron stars merged 130 million light-years away in NGC 4993, "
        "producing a kilonova, a gamma-ray burst, and gravitational waves "
        "detected simultaneously. This single event confirmed that neutron star "
        "mergers produce heavy elements like gold and platinum."
    ),
    "GW190521": (
        "The most massive binary black hole merger detected, producing a ~150 solar mass "
        "remnant. This is in the 'pair-instability mass gap' where black holes shouldn't "
        "form from normal stellar evolution, challenging our understanding of how "
        "massive black holes form."
    ),
    "GW200105": (
        "First confident detection of a neutron star-black hole merger. "
        "A black hole about 9 times the Sun's mass swallowed a neutron star "
        "about 1.9 solar masses. No electromagnetic counterpart was found."
    ),
    "GW200115": (
        "Second neutron star-black hole merger, with a 6 solar mass black hole "
        "and a 1.5 solar mass neutron star. Better localized than GW200105."
    ),
    "GW231123": (
        "The highest-mass binary black hole merger in GWTC-4.0, detected during "
        "LIGO's fourth observing run. The combined mass of the system pushed the "
        "boundaries of what we thought possible for black hole mergers."
    ),
}


def _classify_from_masses(mass_1: float | None, mass_2: float | None) -> dict:
    """
    Infer merger type from component masses (solar masses).

    GWOSC's flat catalog does not provide a classification probability dict —
    it only exposes mass_1_source and mass_2_source.  We use the conventional
    3 M☉ boundary between neutron stars and black holes:

        mass_2 < 3:
            mass_1 > 5  →  NSBH  (black hole swallowing a neutron star)
            else        →  BNS   (both components are neutron stars)
        else            →  BBH   (both components are black holes)

    Returns a dict in the same shape as GWEvent.classification, e.g.
    {"BBH": 1.0} so downstream code can call max(cls, key=cls.get) safely.
    """
    if mass_1 is None or mass_2 is None:
        return {"BBH": 1.0}  # safe default; most GWTC events are BBH
    # Ensure mass_2 is the lighter component
    m1, m2 = max(mass_1, mass_2), min(mass_1, mass_2)
    if m2 < 3.0:
        return {"NSBH": 1.0} if m1 > 5.0 else {"BNS": 1.0}
    return {"BBH": 1.0}


async def fetch_gwosc_events() -> list[dict]:
    """
    Fetch all public GW events from the GWOSC catalog API.

    The API response shape is:
        {"events": {"GW...-v1": {commonName, GPS, far, luminosity_distance,
                                  mass_1_source, mass_2_source, ...}, ...}}

    Deduplicates by commonName, keeping the highest version number.
    Returns a list of dicts ready to be upserted into the GWEvent table.
    Returns an empty list if GWOSC is unreachable.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(GWOSC_API_URL)
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.error("Failed to fetch GWOSC events: %s", e)
        return []

    raw_events: dict = data.get("events", {})
    if not raw_events:
        logger.warning("GWOSC response contained no 'events' key or empty events dict")
        return []

    # Deduplicate by commonName, keeping the highest version number.
    # Each key is "{commonName}-v{N}"; each value is a flat event dict.
    events_by_name: dict[str, dict] = {}
    for _version_key, evt in raw_events.items():
        common_name = evt.get("commonName")
        if not common_name:
            continue
        existing = events_by_name.get(common_name)
        if existing is None or evt.get("version", 0) > existing.get("version", 0):
            events_by_name[common_name] = evt

    result = []
    skipped = 0
    for common_name, evt in events_by_name.items():
        gps = evt.get("GPS")
        if gps is None:
            skipped += 1
            continue

        try:
            event_time = Time(float(gps), format="gps").to_datetime(timezone=timezone.utc)
        except Exception as e:
            logger.warning("Could not convert GPS %s for %s: %s", gps, common_name, e)
            skipped += 1
            continue

        far = evt.get("far")
        dist = evt.get("luminosity_distance")

        # Classification inferred from masses — p_astro is a single float in
        # the flat catalog, not a classification dict, so we cannot use it.
        classification = _classify_from_masses(
            evt.get("mass_1_source"), evt.get("mass_2_source")
        )

        properties = {
            # Flat catalog provides no sky localisation
            "ra_center": None,
            "dec_center": None,
            "area_90_deg2": None,
            "distance_mpc": float(dist) if dist is not None else None,
            "distance_err_mpc": None,
            "description": DESCRIPTIONS.get(common_name),
        }

        result.append({
            "superevent_id": common_name,
            "event_time": event_time,
            "far": float(far) if far is not None else None,
            "classification": classification,
            "properties": properties,
        })

    logger.info(
        "Fetched %d GW events from GWOSC (%d raw entries, %d skipped)",
        len(result), len(raw_events), skipped,
    )
    return result


class GWCrossMatchService:
    """Cross-matches optical transients with gravitational wave events."""

    async def seed_gw_events(self, session: AsyncSession) -> int:
        """Load GW events from GWOSC into the database. Upserts all events."""
        events = await fetch_gwosc_events()
        if not events:
            logger.warning("No events returned from GWOSC; skipping seed")
            return 0

        count = 0
        for evt in events:
            superevent_id = evt["superevent_id"]
            existing = await session.execute(
                select(GWEvent).where(GWEvent.superevent_id == superevent_id)
            )
            if existing.scalar_one_or_none():
                continue

            gw = GWEvent(
                superevent_id=superevent_id,
                event_time=evt["event_time"],
                far=evt["far"],
                skymap_url=f"{GRACEDB_PUBLIC_URL}/{superevent_id}/files/bayestar.multiorder.fits",
                classification=evt["classification"],
                properties=evt["properties"],
            )
            session.add(gw)
            count += 1

        await session.commit()
        logger.info(f"Seeded {count} new GW events from GWOSC")
        return count

    async def cross_match_event(
        self,
        session: AsyncSession,
        superevent_id: str,
        search_radius_deg: float = 15.0,
        time_window_days: float = 30.0,
    ) -> list[dict]:
        """
        Find optical transients that could be counterparts to a GW event.

        Uses angular distance matching from the skymap centroid.
        For well-localized events (like GW170817 with 28 deg2),
        this finds candidates within the credible region.

        Args:
            session: Database session.
            superevent_id: The GW event ID (e.g., "GW170817").
            search_radius_deg: Angular search radius in degrees.
            time_window_days: How many days after the GW event to search.

        Returns:
            List of candidate counterpart objects with distance info.
        """
        # Get the GW event
        result = await session.execute(
            select(GWEvent).where(GWEvent.superevent_id == superevent_id)
        )
        gw_event = result.scalar_one_or_none()
        if not gw_event:
            raise ValueError(f"GW event {superevent_id} not found")

        props = gw_event.properties or {}
        ra_center = props.get("ra_center")
        dec_center = props.get("dec_center")

        if ra_center is None or dec_center is None:
            # Poorly localized event, search entire database within time window
            logger.warning(f"{superevent_id} has no localization, searching by time only")
            return await self._search_by_time_only(session, gw_event, time_window_days)

        # Use the 90% credible area to set search radius if available
        area_90 = props.get("area_90_deg2", 0)
        if area_90 > 0:
            # Approximate the credible region as a circle
            # Area = pi * r^2, so r = sqrt(area / pi)
            effective_radius = min(math.sqrt(area_90 / math.pi), search_radius_deg)
        else:
            effective_radius = search_radius_deg

        # Time window: search for transients detected around the GW event time
        event_time = gw_event.event_time
        time_start = event_time - timedelta(days=7)  # 7 days before (pre-existing transients)
        time_end = event_time + timedelta(days=time_window_days)  # N days after

        # Angular distance query using PostGIS
        candidates_result = await session.execute(
            text("""
                SELECT oid, ra, dec, classification, classification_probability,
                       first_detection, last_detection, n_detections,
                       cross_match_name, broker_source,
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
                AND last_detection >= :time_start
                AND first_detection <= :time_end
                ORDER BY distance_arcsec
            """),
            {
                "ra": ra_center,
                "dec": dec_center,
                "radius_meters": effective_radius * 3600 * 30.87,  # deg -> arcsec -> meters
                "time_start": time_start,
                "time_end": time_end,
            },
        )

        candidates = []
        for row in candidates_result.fetchall():
            distance_deg = row.distance_arcsec / 3600.0

            candidate = {
                "oid": row.oid,
                "ra": row.ra,
                "dec": row.dec,
                "classification": row.classification,
                "probability": row.classification_probability,
                "n_detections": row.n_detections,
                "distance_deg": round(distance_deg, 3),
                "distance_arcsec": round(row.distance_arcsec, 1),
                "cross_match": row.cross_match_name,
                "in_90_region": distance_deg <= effective_radius,
            }
            candidates.append(candidate)

            # Store the candidate association
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            stmt = pg_insert(GWCandidate).values(
                superevent_id=superevent_id,
                oid=row.oid,
                distance_to_peak_arcsec=row.distance_arcsec,
            ).on_conflict_do_nothing()
            await session.execute(stmt)

        await session.commit()
        logger.info(f"Found {len(candidates)} candidates for {superevent_id}")
        return candidates

    async def _search_by_time_only(
        self, session: AsyncSession, gw_event: GWEvent, time_window_days: float
    ) -> list[dict]:
        """For poorly localized events, search by time window only."""
        event_time = gw_event.event_time
        time_start = event_time - timedelta(days=7)
        time_end = event_time + timedelta(days=time_window_days)

        result = await session.execute(
            select(Object)
            .where(Object.last_detection >= time_start)
            .where(Object.first_detection <= time_end)
            .where(Object.classification.in_(["SNIa", "SNIbc", "SNII", "TDE", "KN", "CV/Nova"]))
            .order_by(Object.classification_probability.desc())
            .limit(50)
        )

        return [
            {
                "oid": obj.oid,
                "ra": obj.ra,
                "dec": obj.dec,
                "classification": obj.classification,
                "probability": obj.classification_probability,
                "n_detections": obj.n_detections,
                "distance_deg": None,
                "distance_arcsec": None,
                "cross_match": obj.cross_match_name,
                "in_90_region": None,  # Unknown without localization
            }
            for obj in result.scalars().all()
        ]

    async def get_all_events(self, session: AsyncSession) -> list[dict]:
        """Get all GW events with their properties."""
        result = await session.execute(
            select(GWEvent).order_by(GWEvent.event_time.desc())
        )
        events = result.scalars().all()

        output = []
        for evt in events:
            props = evt.properties or {}
            cls = evt.classification or {}

            # Determine the dominant classification
            dominant_type = max(cls, key=cls.get) if cls else "Unknown"

            type_labels = {
                "BNS": "Binary Neutron Star",
                "NSBH": "Neutron Star-Black Hole",
                "BBH": "Binary Black Hole",
                "Terrestrial": "False Alarm",
            }

            type_emoji = {
                "BNS": "🔔",
                "NSBH": "🕳️",
                "BBH": "⚫",
                "Terrestrial": "❌",
            }

            # Count candidates
            cand_result = await session.execute(
                select(GWCandidate)
                .where(GWCandidate.superevent_id == evt.superevent_id)
            )
            n_candidates = len(cand_result.scalars().all())

            output.append({
                "superevent_id": evt.superevent_id,
                "event_time": evt.event_time.isoformat() if evt.event_time else None,
                "type": type_labels.get(dominant_type, dominant_type),
                "type_key": dominant_type,
                "emoji": type_emoji.get(dominant_type, "🌊"),
                "classification": cls,
                "ra_center": props.get("ra_center"),
                "dec_center": props.get("dec_center"),
                "area_90_deg2": props.get("area_90_deg2"),
                "distance_mpc": props.get("distance_mpc"),
                "distance_err_mpc": props.get("distance_err_mpc"),
                "description": props.get("description", ""),
                "n_candidates": n_candidates,
            })

        return output
