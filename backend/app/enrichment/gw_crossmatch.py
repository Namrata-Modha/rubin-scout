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
from datetime import timedelta, timezone

import httpx
from astropy.time import Time
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import GWCandidate, GWEvent, Object

logger = logging.getLogger(__name__)


class LocalizationUnavailableError(Exception):
    """Raised when a GW event has no usable sky localization.

    A gravitational-wave cross-match is only scientifically meaningful with a
    spatial (skymap) term.  When ``properties.ra_center`` / ``dec_center`` are
    absent we must fail explicitly rather than return a spatially unfiltered
    candidate list, so the API can surface a distinct 4xx to the caller.
    """


# GWOSC public catalog API
GWOSC_API_URL = "https://gwosc.org/eventapi/json/allevents/"

# skymap_url is stored as None: the flat v1 GWOSC catalog fetched here carries
# no skymap, and the GraceDB apiweb URL this code once built (GWOSC commonName
# mis-used as an S-prefixed superevent id) always 404s. Real skymaps live in the
# GWOSC v2 API and per-catalog Zenodo tarballs; ingesting them is out of scope
# for this module. Full details, verified URLs and DOIs: docs/gw-skymaps.md.

# The known-broken skymap URL pattern this module used to write (GWOSC
# commonName mis-used as a GraceDB superevent id). Rows still carrying it should
# be cleared to None on refresh; any other (locally ingested) URL is preserved.
_BROKEN_SKYMAP_PREFIX = "https://gracedb.ligo.org/apiweb/superevents/"


def _is_broken_skymap_url(url: str | None) -> bool:
    """True if `url` is the legacy guaranteed-404 GraceDB pattern (or None)."""
    return url is None or url.startswith(_BROKEN_SKYMAP_PREFIX)


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
            event_time = Time(float(gps), format="gps").utc.to_datetime(timezone=timezone.utc)
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

        mass_1 = evt.get("mass_1_source")
        mass_2 = evt.get("mass_2_source")
        properties = {
            # Flat catalog provides no sky localisation
            "ra_center": None,
            "dec_center": None,
            "area_90_deg2": None,
            "distance_mpc": float(dist) if dist is not None else None,
            "distance_err_mpc": None,
            "mass_1_solar": float(mass_1) if mass_1 is not None else None,
            "mass_2_solar": float(mass_2) if mass_2 is not None else None,
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


_TYPE_NOUN = {
    "BBH": "binary black hole merger",
    "BNS": "binary neutron star merger",
    "NSBH": "neutron star–black hole merger",
    "Terrestrial": "likely terrestrial event (false alarm)",
}

_COMPONENT_NOUN = {
    "BBH": ("black hole", "black hole"),
    "BNS": ("neutron star", "neutron star"),
    "NSBH": ("black hole", "neutron star"),
}


def _auto_description(dominant_type: str, props: dict) -> str:
    """
    Generate a plain-English description for GW events that lack a hand-written
    entry in DESCRIPTIONS.  Uses mass and distance from stored properties when
    available; falls back to generic phrasing.
    """
    noun = _TYPE_NOUN.get(dominant_type, "gravitational wave event")

    m1 = props.get("mass_1_solar")
    m2 = props.get("mass_2_solar")
    dist = props.get("distance_mpc")

    # Article for the merger noun
    article = "An" if noun[0] in "aeiou" else "A"

    if m1 is not None and m2 is not None:
        # Sort so heavier component is listed first
        heavy, light = (m1, m2) if m1 >= m2 else (m2, m1)
        nouns = _COMPONENT_NOUN.get(dominant_type, ("object", "object"))
        mass_phrase = (
            f"Two {nouns[0]}s of {heavy:.1f} and {light:.1f} solar masses merged"
            if nouns[0] == nouns[1]
            else f"A {heavy:.1f} solar-mass {nouns[0]} merged with a {light:.1f} solar-mass {nouns[1]}"
        )
        if dist is not None:
            return f"{article} {noun}. {mass_phrase} {dist:.0f} Mpc away."
        return f"{article} {noun}. {mass_phrase}."

    if dist is not None:
        return f"{article} {noun} detected {dist:.0f} Mpc away during a LIGO/Virgo/KAGRA observing run."

    return f"{article} {noun} detected during a LIGO/Virgo/KAGRA observing run."


class GWCrossMatchService:
    """Cross-matches optical transients with gravitational wave events."""

    async def seed_gw_events(self, session: AsyncSession) -> int:
        """Load GW events from GWOSC into the database, upserting existing rows.

        For a new ``superevent_id`` a fresh row is inserted.  For an existing
        row the refreshable fields are updated so the weekly refresh actually
        picks up revised GWOSC values:

          Overwritten from GWOSC : ``far``, ``classification``
          ``skymap_url`` : overwritten only when GWOSC supplies a value, or to
              clear a legacy broken GraceDB URL; a real URL written locally by a
              future skymap-ingestion job is preserved.
          Merged into ``properties`` : every non-None value from the new
              payload is written; an existing non-None value is NEVER clobbered
              by an incoming None.  This refreshes catalog-derived fields
              (distance, masses, description) while PRESERVING any locally
              computed localisation (``ra_center`` / ``dec_center`` /
              ``area_90_deg2``) that a future skymap-ingestion job may have
              written — those are always None in the flat GWOSC catalog.
          Preserved untouched : ``event_time``, ``created_at``.

        Returns the number of rows inserted OR updated.
        """
        events = await fetch_gwosc_events()
        if not events:
            logger.warning("No events returned from GWOSC; skipping seed")
            return 0

        inserted = 0
        updated = 0
        for evt in events:
            superevent_id = evt["superevent_id"]
            result = await session.execute(
                select(GWEvent).where(GWEvent.superevent_id == superevent_id)
            )
            existing = result.scalar_one_or_none()

            if existing is None:
                gw = GWEvent(
                    superevent_id=superevent_id,
                    event_time=evt["event_time"],
                    far=evt["far"],
                    # No verified public skymap URL exists for this catalog;
                    # store None (see docs/gw-skymaps.md).
                    skymap_url=None,
                    classification=evt["classification"],
                    properties=evt["properties"],
                )
                session.add(gw)
                inserted += 1
            else:
                existing.far = evt["far"]
                existing.classification = evt["classification"]
                # skymap_url follows the same "don't clobber good data" rule as
                # properties. The flat GWOSC catalog carries no skymap, so the
                # incoming value is None. Only overwrite when GWOSC actually
                # provides a URL, OR to clear a legacy broken GraceDB URL. A real
                # URL written locally by skymap ingestion survives.
                incoming_skymap = evt.get("skymap_url")
                if incoming_skymap is not None:
                    existing.skymap_url = incoming_skymap
                elif _is_broken_skymap_url(existing.skymap_url):
                    existing.skymap_url = None
                # Merge properties: refresh with incoming non-None values but
                # never overwrite an existing non-None value with None, so
                # locally computed localisation survives the weekly refresh.
                merged = dict(existing.properties or {})
                for key, value in (evt["properties"] or {}).items():
                    if value is not None or key not in merged:
                        merged[key] = value
                existing.properties = merged
                updated += 1

        await session.commit()
        logger.info(
            "Seeded GW events from GWOSC: %d inserted, %d updated", inserted, updated
        )
        return inserted + updated

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
            # A cross-match with no spatial term is scientifically invalid: it
            # would return the highest-probability transients anywhere on the
            # sky and mislabel them as counterparts.  Fail explicitly instead.
            logger.warning(
                "%s has no sky localization; refusing to cross-match", superevent_id
            )
            raise LocalizationUnavailableError(
                f"Sky localization is not yet available for {superevent_id}"
            )

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

    async def get_stored_candidates(
        self, session: AsyncSession, superevent_id: str
    ) -> list[dict]:
        """Read previously persisted cross-match candidates. Never writes.

        Returns the ``GWCandidate`` rows for an event joined to ``objects``,
        ordered by distance to the skymap peak. This is the read side of the
        GET route: it never computes, inserts or commits. Candidates are
        produced only by ``cross_match_event`` from the POST route.

        Raises ValueError if the event does not exist (mapped to 404).
        """
        result = await session.execute(
            select(GWEvent).where(GWEvent.superevent_id == superevent_id)
        )
        if result.scalar_one_or_none() is None:
            raise ValueError(f"GW event {superevent_id} not found")

        rows = await session.execute(
            select(GWCandidate, Object)
            .join(Object, GWCandidate.oid == Object.oid)
            .where(GWCandidate.superevent_id == superevent_id)
            .order_by(GWCandidate.distance_to_peak_arcsec)
        )

        candidates = []
        for cand, obj in rows.all():
            dist_arcsec = cand.distance_to_peak_arcsec
            candidates.append({
                "oid": obj.oid,
                "ra": obj.ra,
                "dec": obj.dec,
                "classification": obj.classification,
                "probability": obj.classification_probability,
                "n_detections": obj.n_detections,
                "distance_deg": round(dist_arcsec / 3600.0, 3) if dist_arcsec is not None else None,
                "distance_arcsec": round(dist_arcsec, 1) if dist_arcsec is not None else None,
                "cross_match": obj.cross_match_name,
                "probability_in_skymap": cand.probability_in_skymap,
            })
        return candidates

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
                "description": props.get("description") or _auto_description(dominant_type, props),
                "n_candidates": n_candidates,
            })

        return output
