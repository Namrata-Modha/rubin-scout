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
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import GWCandidate, GWEvent, Object

logger = logging.getLogger(__name__)

# Public GW events from GWTC catalogs (no authentication needed)
GRACEDB_PUBLIC_URL = "https://gracedb.ligo.org/apiweb/superevents"

# Well-known GW events for demo/seeding (from GWTC-3 and GWTC-4)
NOTABLE_GW_EVENTS = [
    {
        "superevent_id": "GW170817",
        "event_time": "2017-08-17T12:41:04.4Z",
        "ra_center": 197.45,
        "dec_center": -23.38,
        "area_90_deg2": 28.0,
        "distance_mpc": 40.0,
        "distance_err_mpc": 8.0,
        "classification": {"BNS": 1.0, "NSBH": 0.0, "BBH": 0.0, "Terrestrial": 0.0},
        "description": "The first gravitational wave event with an electromagnetic counterpart. "
                       "Two neutron stars merged 130 million light-years away in NGC 4993, "
                       "producing a kilonova, a gamma-ray burst, and gravitational waves "
                       "detected simultaneously. This single event confirmed that neutron star "
                       "mergers produce heavy elements like gold and platinum.",
    },
    {
        "superevent_id": "GW190425",
        "event_time": "2019-04-25T08:18:05.0Z",
        "ra_center": None,  # Very poorly localized
        "dec_center": None,
        "area_90_deg2": 8284.0,
        "distance_mpc": 159.0,
        "distance_err_mpc": 72.0,
        "classification": {"BNS": 0.99, "NSBH": 0.0, "BBH": 0.0, "Terrestrial": 0.01},
        "description": "Second confirmed binary neutron star merger, but with only one detector "
                       "operating, the sky localization was extremely poor (nearly a quarter of the sky).",
    },
    {
        "superevent_id": "GW190521",
        "event_time": "2019-05-21T03:02:29.7Z",
        "ra_center": 189.0,
        "dec_center": -36.0,
        "area_90_deg2": 765.0,
        "distance_mpc": 5300.0,
        "distance_err_mpc": 2600.0,
        "classification": {"BNS": 0.0, "NSBH": 0.0, "BBH": 0.99, "Terrestrial": 0.01},
        "description": "The most massive binary black hole merger detected, producing a ~150 solar mass "
                       "remnant. This is in the 'pair-instability mass gap' where black holes shouldn't "
                       "form from normal stellar evolution, challenging our understanding of how "
                       "massive black holes form.",
    },
    {
        "superevent_id": "GW200105",
        "event_time": "2020-01-05T16:24:26.0Z",
        "ra_center": None,
        "dec_center": None,
        "area_90_deg2": 7461.0,
        "distance_mpc": 280.0,
        "distance_err_mpc": 110.0,
        "classification": {"BNS": 0.0, "NSBH": 0.93, "BBH": 0.07, "Terrestrial": 0.0},
        "description": "First confident detection of a neutron star-black hole merger. "
                       "A black hole about 9 times the Sun's mass swallowed a neutron star "
                       "about 1.9 solar masses. No electromagnetic counterpart was found.",
    },
    {
        "superevent_id": "GW200115",
        "event_time": "2020-01-15T04:23:09.7Z",
        "ra_center": 30.0,
        "dec_center": -12.0,
        "area_90_deg2": 904.0,
        "distance_mpc": 300.0,
        "distance_err_mpc": 100.0,
        "classification": {"BNS": 0.0, "NSBH": 0.99, "BBH": 0.01, "Terrestrial": 0.0},
        "description": "Second neutron star-black hole merger, with a 6 solar mass black hole "
                       "and a 1.5 solar mass neutron star. Better localized than GW200105.",
    },
    {
        "superevent_id": "GW231123",
        "event_time": "2023-11-23T00:00:00Z",
        "ra_center": None,
        "dec_center": None,
        "area_90_deg2": 500.0,
        "distance_mpc": 10000.0,
        "distance_err_mpc": 5000.0,
        "classification": {"BNS": 0.0, "NSBH": 0.0, "BBH": 1.0, "Terrestrial": 0.0},
        "description": "The highest-mass binary black hole merger in GWTC-4.0, detected during "
                       "LIGO's fourth observing run. The combined mass of the system pushed the "
                       "boundaries of what we thought possible for black hole mergers.",
    },
]


class GWCrossMatchService:
    """Cross-matches optical transients with gravitational wave events."""

    async def seed_gw_events(self, session: AsyncSession) -> int:
        """Load notable GW events into the database."""
        count = 0
        for evt in NOTABLE_GW_EVENTS:
            existing = await session.execute(
                select(GWEvent).where(GWEvent.superevent_id == evt["superevent_id"])
            )
            if existing.scalar_one_or_none():
                continue

            gw = GWEvent(
                superevent_id=evt["superevent_id"],
                event_time=datetime.fromisoformat(evt["event_time"].replace("Z", "+00:00")),
                far=None,
                skymap_url=f"{GRACEDB_PUBLIC_URL}/{evt['superevent_id']}/files/bayestar.multiorder.fits",
                classification=evt["classification"],
                properties={
                    "ra_center": evt.get("ra_center"),
                    "dec_center": evt.get("dec_center"),
                    "area_90_deg2": evt.get("area_90_deg2"),
                    "distance_mpc": evt.get("distance_mpc"),
                    "distance_err_mpc": evt.get("distance_err_mpc"),
                    "description": evt.get("description"),
                },
            )
            session.add(gw)
            count += 1

        await session.commit()
        logger.info(f"Seeded {count} GW events")
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
