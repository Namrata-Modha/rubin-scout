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
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from astropy.time import Time
from sqlalchemy import func, select, text
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
    # Keyed on the long-form names for the same reason as GW231123_135430
    # below. GWOSC published both under short names in O3_Discovery_Papers and
    # renamed them when they were folded into GWTC-3, so the short keys stopped
    # matching anything the feed serves. The short-named rows that used to
    # carry these were additionally found to be fabricated and were deleted in
    # 2026-08 — see docs/gw-events-data-quality.md.
    "GW200105_162426": (
        "First confident detection of a neutron star-black hole merger. "
        "A black hole about 9 times the Sun's mass swallowed a neutron star "
        "about 1.9 solar masses. No electromagnetic counterpart was found."
    ),
    "GW200115_042309": (
        "Second neutron star-black hole merger, with a 6 solar mass black hole "
        "and a 1.5 solar mass neutron star. Better localized than the first."
    ),
    # Keyed on the long-form name, which is the ONLY name GWOSC has ever
    # served for this event (version keys run GW231123_135430-v1..v3, first
    # published under O4_Discovery_Papers). The bare "GW231123" this was keyed
    # on until 2026-08 never appeared in the GWOSC feed, so the description was
    # attached to an ID that is never ingested and never rendered.
    "GW231123_135430": (
        "The highest-mass binary black hole merger in GWTC-4.0, detected during "
        "LIGO's fourth observing run. The combined mass of the system pushed the "
        "boundaries of what we thought possible for black hole mergers."
    ),
}


# GWOSC's `allevents/` feed mixes several tiers under a `catalog.shortName`
# field: official LVK confident detections, official LVK sub-threshold
# ("marginal") candidates, individual per-event discovery papers published
# ahead of a cumulative catalog, a third-party (non-LVK) reanalysis catalog,
# and at least one placeholder catalog GWOSC itself documents as containing
# zero astrophysical detections. `fetch_gwosc_events()` used to ingest all of
# these identically. Classified here so significance is recorded, not
# discarded, and so non-detections/non-LVK entries can be excluded outright.
#
# CONFIDENT — official LVK confident/high-significance detection catalogs.
# GWOSC's own catalog descriptions label GWTC-4.1 and GWTC-5.0 as "Confident
# events from the O4a/O4b observing run" despite the shortName dropping the
# "-confident" suffix used by older catalogs.
_CONFIDENT_CATALOGS = frozenset({
    "GWTC-1-confident", "GWTC-2", "GWTC-2.1-confident", "GWTC-3-confident",
    "GWTC-4.0", "GWTC-4.1", "GWTC-5.0",
})
# MARGINAL — official LVK sub-threshold candidate catalogs. These are real
# LVK-published products (from the GWTC catalog papers), just below the
# significance threshold for a confident detection.
_MARGINAL_CATALOGS = frozenset({
    "GWTC-1-marginal", "GWTC-2.1-marginal", "GWTC-3-marginal", "O3_IMBH_marginal",
})
# PRELIMINARY — individual per-event LVK discovery papers published ahead of
# a cumulative catalog release (e.g. GW231123, GW230529). Genuine detections,
# just not yet folded into a catalog-level confident/marginal audit.
_PRELIMINARY_CATALOGS = frozenset({
    "O1_O2-Preliminary", "O3_Discovery_Papers", "O4_Discovery_Papers",
})
# EXCLUDED — not real LVK gravitational-wave detections, so never ingested:
#   IAS-O3a            third-party (Institute for Advanced Study) reanalysis;
#                       not an LVK data product.
#   Initial_LIGO_Virgo  GWOSC's own description: "No astrophysical detections
#                       were made during this period." Covers hardware
#                       injection tests and GRB-counterpart search triggers
#                       that GWOSC files under this catalog (verified: entries
#                       named "blind_injection" and "GRB051103" both carry
#                       this tag).
#   GWTC-2.1-auxiliary  GWOSC's own description: candidates from GWTC-2 that,
#                       per the GWTC-2.1 reanalysis, do NOT satisfy the
#                       criteria for either GWTC-2.1-confident or
#                       GWTC-2.1-marginal — i.e. actively downgraded below
#                       even marginal status.
_EXCLUDED_CATALOGS = frozenset({
    "IAS-O3a", "Initial_LIGO_Virgo", "GWTC-2.1-auxiliary",
})


# The significance values that can actually appear on a stored GWEvent: the
# four tiers _classify_significance can return for an ingested row ("excluded"
# events are dropped at ingest and never reach the database — see
# _EXCLUDED_CATALOGS — so it is deliberately not a valid filter value), plus
# "unclassified" for rows ingested before significance tracking existed
# (get_all_events/get_significance_counts report that as the fallback when
# properties has no "significance" key). Query-param validation for
# GET /api/gw/events reuses this exact set rather than duplicating it.
SIGNIFICANCE_TIERS = frozenset({
    "confident", "marginal", "preliminary", "unknown", "unclassified",
})


def _classify_significance(catalog_tag: str | None) -> str:
    """Map a GWOSC `catalog.shortName` tag to a coarse significance tier.

    Returns one of "confident", "marginal", "preliminary", "excluded", or
    "unknown" for any tag not in the tables above — ingested but flagged
    rather than silently miscounted as confident, so a future GWOSC catalog
    name doesn't slip through uncategorized.
    """
    if catalog_tag in _CONFIDENT_CATALOGS:
        return "confident"
    if catalog_tag in _MARGINAL_CATALOGS:
        return "marginal"
    if catalog_tag in _PRELIMINARY_CATALOGS:
        return "preliminary"
    if catalog_tag in _EXCLUDED_CATALOGS:
        return "excluded"
    return "unknown"


def _select_ingestable_version(versions: list[dict]) -> dict:
    """Pick which version of one GWOSC event to ingest.

    GWOSC publishes an event repeatedly as catalogs are released, keyed
    "{name}-v{N}". Two different signals live in that list, and the order they
    are applied in matters:

      * The catalog tag is a PROVENANCE signal — is this entry an LVK data
        product we accept at all? A third-party reanalysis (IAS-O3a) or a
        documented non-detection (Initial_LIGO_Virgo, GWTC-2.1-auxiliary) is
        not, at any version. See _EXCLUDED_CATALOGS.
      * The version number is a RECENCY signal WITHIN that accepted set — a
        later LVK catalog supersedes an earlier one's parameters and verdict.

    Selecting on recency first and checking provenance afterwards lets a
    third-party reanalysis published at a higher version number veto a real
    LVK detection: the event is dropped entirely even though a perfectly good
    confident version exists lower down. That was a real bug — 33 confident
    GWTC-2/GWTC-2.1 events (including GW190412, whose v5 is an IAS-O3a
    reanalysis over a v4 GWTC-2.1-confident) never reached the database.

    So: filter by provenance, then take the latest of what survives. Falling
    back to the raw newest when EVERY version is excluded keeps this function
    total; the caller then classifies that entry "excluded" and drops it, and
    still logs a representative catalog tag.

    Note this rule can only ever ADD events, never change which version an
    already-ingested event resolves to: if the newest version overall is not
    excluded, it is also the newest non-excluded one, so the pick is
    identical.
    """
    eligible = [
        v for v in versions
        if _classify_significance(v.get("catalog.shortName")) != "excluded"
    ]
    return max(eligible or versions, key=lambda v: v.get("version", 0))


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


async def _fetch_gwosc_payload() -> dict | None:
    """GET the GWOSC allevents feed once. Returns the parsed JSON, or None.

    Split out so a single HTTP round trip can feed both `fetch_gwosc_events`
    (what we ingest) and `fetch_gwosc_catalog_index` (what GWOSC still knows
    about, which is a strictly larger set -- see GwoscCatalogIndex).
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(GWOSC_API_URL)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error("Failed to fetch GWOSC events: %s", e)
        return None


async def fetch_gwosc_events(payload: dict | None = None) -> list[dict]:
    """
    Fetch all public GW events from the GWOSC catalog API.

    The API response shape is:
        {"events": {"GW...-v1": {commonName, GPS, far, luminosity_distance,
                                  mass_1_source, mass_2_source, ...}, ...}}

    Deduplicates by commonName via `_select_ingestable_version()` — the
    highest version among catalogs we accept, NOT the highest version
    outright (see there for why the ordering matters).
    Classifies each event's `catalog.shortName` into a significance tier via
    `_classify_significance()`, storing the result in `properties.significance`
    (and the raw tag in `properties.catalog`). Events classified "excluded" —
    non-LVK third-party catalogs and GWOSC's documented non-detection
    placeholders — are dropped here and never reach the returned list.
    Returns a list of dicts ready to be upserted into the GWEvent table.
    Returns an empty list if GWOSC is unreachable.

    Pass `payload` to reuse an already-fetched feed (see
    `_fetch_gwosc_payload`) so one HTTP round trip can serve both this and
    `fetch_gwosc_catalog_index`; omit it and this fetches its own.
    """
    data = payload if payload is not None else await _fetch_gwosc_payload()
    if data is None:
        return []

    raw_events: dict = data.get("events", {})
    if not raw_events:
        logger.warning("GWOSC response contained no 'events' key or empty events dict")
        return []

    # Group every version of an event under its current commonName, then pick
    # one per name. Each key is "{commonName}-v{N}"; each value is a flat
    # event dict.
    versions_by_name: dict[str, list[dict]] = {}
    for _version_key, evt in raw_events.items():
        common_name = evt.get("commonName")
        if not common_name:
            continue
        versions_by_name.setdefault(common_name, []).append(evt)

    events_by_name = {
        name: _select_ingestable_version(versions)
        for name, versions in versions_by_name.items()
    }

    result = []
    skipped = 0
    excluded_by_tag: dict[str | None, int] = {}
    for common_name, evt in events_by_name.items():
        catalog_tag = evt.get("catalog.shortName")
        significance = _classify_significance(catalog_tag)

        if significance == "excluded":
            excluded_by_tag[catalog_tag] = excluded_by_tag.get(catalog_tag, 0) + 1
            continue

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
            # Significance tier ("confident" / "marginal" / "preliminary" /
            # "unknown") and the raw GWOSC catalog tag it was derived from.
            # See _classify_significance for the tier definitions.
            "significance": significance,
            "catalog": catalog_tag,
        }

        result.append({
            "superevent_id": common_name,
            "event_time": event_time,
            "far": float(far) if far is not None else None,
            "classification": classification,
            "properties": properties,
        })

    n_excluded = sum(excluded_by_tag.values())
    if n_excluded:
        logger.info(
            "Excluded %d non-detection/non-LVK entries by catalog tag: %s",
            n_excluded, excluded_by_tag,
        )
    logger.info(
        "Fetched %d GW events from GWOSC (%d raw entries, %d skipped, %d excluded)",
        len(result), len(raw_events), skipped, n_excluded,
    )
    return result


# GWOSC keys each entry in the allevents feed as "{name}-v{N}", where {name}
# is the name the event carried AT THAT VERSION, while the entry's
# "commonName" field always holds its CURRENT name. When the two disagree,
# GWOSC is telling us the event was renamed. That is the only authoritative
# rename record the public API exposes -- there is no per-event changelog
# endpoint (/eventapi/json/event/{name}/ 404s for a retired name and returns
# only the latest version for a live one).
_VERSION_KEY_RE = re.compile(r"^(?P<name>.+)-v(?P<version>\d+)$")


@dataclass(frozen=True)
class GwoscCatalogIndex:
    """What GWOSC currently knows about, for orphan detection.

    ``known_names`` is every commonName in the live feed -- DELIBERATELY
    including events `fetch_gwosc_events` drops by catalog tag (IAS-O3a,
    Initial_LIGO_Virgo, GWTC-2.1-auxiliary; see _EXCLUDED_CATALOGS). A row we
    choose not to re-ingest is NOT an orphan: GWOSC still serves it, we just
    don't want it. Diffing the database against the *ingestable* list instead
    would mis-flag all ~43 of those documented rows as retired.

    ``renames`` maps a historical name to its current commonName, harvested
    from the feed's version keys. This is the ONLY successor evidence
    reconciliation acts on. Time-proximity matching is explicitly not used:
    across all 433 events in the live feed, GWOSC's GPS time never moves more
    than 0.1 s between versions, so a multi-hour gap between a stored
    event_time and a same-day candidate is evidence AGAINST a rename, not for
    one. See docs/gw-events-data-quality.md.
    """

    known_names: frozenset[str]
    renames: dict[str, str]

    @property
    def is_empty(self) -> bool:
        """True when the feed gave us nothing -- reconciliation must no-op."""
        return not self.known_names


async def fetch_gwosc_catalog_index(payload: dict | None = None) -> GwoscCatalogIndex:
    """Build a GwoscCatalogIndex from the GWOSC allevents feed.

    Returns an empty index if GWOSC is unreachable or the response carries no
    events; callers MUST treat that as "no information", never as "everything
    was retired".
    """
    data = payload if payload is not None else await _fetch_gwosc_payload()
    if data is None:
        return GwoscCatalogIndex(frozenset(), {})

    raw_events: dict = data.get("events", {})
    if not raw_events:
        logger.warning("GWOSC response carried no events; catalog index is empty")
        return GwoscCatalogIndex(frozenset(), {})

    known: set[str] = set()
    # historical name -> set of current names. A historical name that resolves
    # to more than one current name is ambiguous and is dropped rather than
    # guessed at.
    candidates: dict[str, set[str]] = {}

    for version_key, evt in raw_events.items():
        current = evt.get("commonName")
        if not current:
            continue
        known.add(current)
        match = _VERSION_KEY_RE.match(version_key)
        if match is None:
            continue
        historical = match.group("name")
        if historical != current:
            candidates.setdefault(historical, set()).add(current)

    renames = {}
    for historical, targets in candidates.items():
        if historical in known:
            # The old name is still a live event in its own right, so this is
            # not a retirement -- don't shadow a real event with a rename.
            continue
        if len(targets) == 1:
            renames[historical] = next(iter(targets))
        else:
            logger.warning(
                "GWOSC name %s maps to multiple current names %s; not treating as a rename",
                historical, sorted(targets),
            )

    logger.info(
        "GWOSC catalog index: %d known names, %d documented renames",
        len(known), len(renames),
    )
    return GwoscCatalogIndex(frozenset(known), renames)


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


# Reconciliation refuses to retire more than this fraction of gw_events in a
# single pass. A healthy GWOSC release renames a handful of events at most; a
# diff larger than this means the feed is truncated, not that the catalog was
# rewritten. (For scale: the 13 orphans found in production in 2026-08 were
# 2.9% of 444 rows.)
MAX_RETIREMENT_FRACTION = 0.2

# ...but the fraction alone is meaningless on a small table, where a single
# legitimate retirement can exceed it. Retiring no more than this many rows is
# always plausible regardless of table size, so the fraction guard only applies
# above this floor.
MIN_RETIREMENT_ABSOLUTE = 5


class GWCrossMatchService:
    """Cross-matches optical transients with gravitational wave events."""

    async def seed_gw_events(
        self, session: AsyncSession, payload: dict | None = None
    ) -> int:
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
        events = await fetch_gwosc_events(payload)
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

    async def _relink_candidates(
        self, session: AsyncSession, old_id: str, new_id: str
    ) -> tuple[int, int]:
        """Move gw_candidates rows from a retired event onto its successor.

        `gw_candidates` has UNIQUE(superevent_id, oid), so an oid already
        attached to the successor cannot simply be re-pointed. In that case the
        retired duplicate is dropped and the successor row kept -- the two
        describe the same (event, object) pair, and the successor is the one
        keyed to an ID that still resolves.

        Returns (relinked, deduped).
        """
        orphaned = (
            await session.execute(
                select(GWCandidate).where(GWCandidate.superevent_id == old_id)
            )
        ).scalars().all()
        if not orphaned:
            return 0, 0

        taken = set(
            (
                await session.execute(
                    select(GWCandidate.oid).where(GWCandidate.superevent_id == new_id)
                )
            ).scalars().all()
        )

        relinked = deduped = 0
        for candidate in orphaned:
            if candidate.oid in taken:
                await session.delete(candidate)
                deduped += 1
            else:
                candidate.superevent_id = new_id
                taken.add(candidate.oid)
                relinked += 1
        return relinked, deduped

    async def reconcile_retired_events(
        self,
        session: AsyncSession,
        index: GwoscCatalogIndex | None = None,
    ) -> dict:
        """Soft-retire gw_events rows that GWOSC no longer serves.

        A row is an orphan when its `superevent_id` is absent from the live
        GWOSC feed ENTIRELY -- not merely absent from what we choose to ingest.
        Rows dropped by catalog tag (IAS-O3a and friends) are still served by
        GWOSC and are deliberately left alone here.

        An orphan is flagged `retired_at` and never deleted. `superseded_by` is
        filled in ONLY when GWOSC version history documents the rename (see
        GwoscCatalogIndex.renames); when it does, any `gw_candidates` attached
        to the retired ID are relinked onto the successor so locally computed
        work survives the rename.

        An orphan with no documented successor keeps `superseded_by` NULL. That
        is the "a human must decide" state, and it is reported separately.
        Reconciliation does NOT infer a successor from event_time proximity:
        GWOSC trigger times are stable to 0.1 s across catalog releases, so an
        hours-wide gap argues against a rename rather than for one.

        A previously retired row that reappears in GWOSC is un-retired.

        Returns a report dict. Safe to run repeatedly (idempotent).
        """
        report: dict = {
            "checked": 0,
            "retired": [],
            "retired_unresolved": [],
            "unretired": [],
            "candidates_relinked": 0,
            "candidates_deduped": 0,
            "skipped_reason": None,
        }

        if index is None:
            index = await fetch_gwosc_catalog_index()

        if index.is_empty:
            # No information is not the same as "everything was retired".
            logger.warning(
                "GWOSC catalog index is empty; skipping retirement reconciliation"
            )
            report["skipped_reason"] = "gwosc_unavailable"
            return report

        rows = (await session.execute(select(GWEvent))).scalars().all()
        report["checked"] = len(rows)
        known_ids = {row.superevent_id for row in rows}

        missing = [r for r in rows if r.superevent_id not in index.known_names]
        newly_missing = {r.superevent_id for r in missing if r.retired_at is None}

        # Safety valve: a truncated or partially populated GWOSC response would
        # make most of the table look retired. Refuse to write in that case
        # rather than mass-retiring real events. Small absolute counts are
        # always allowed -- see MIN_RETIREMENT_ABSOLUTE.
        if (
            len(newly_missing) > MIN_RETIREMENT_ABSOLUTE
            and len(newly_missing) > len(rows) * MAX_RETIREMENT_FRACTION
        ):
            logger.error(
                "Refusing to retire %d/%d gw_events in one pass (>%.0f%%); "
                "GWOSC feed looks truncated",
                len(newly_missing), len(rows), MAX_RETIREMENT_FRACTION * 100,
            )
            report["skipped_reason"] = "implausible_diff"
            return report

        now = datetime.now(timezone.utc)

        for row in missing:
            if row.retired_at is None:
                row.retired_at = now

            # Resolve a successor for newly retired rows, and re-try rows
            # retired earlier without one -- GWOSC may have published the
            # rename since the previous pass.
            if row.superseded_by is None:
                successor = index.renames.get(row.superevent_id)
                # The successor must already exist locally: superseded_by is a
                # foreign key onto gw_events.superevent_id.
                if successor and successor in known_ids:
                    row.superseded_by = successor
                    relinked, deduped = await self._relink_candidates(
                        session, row.superevent_id, successor
                    )
                    report["candidates_relinked"] += relinked
                    report["candidates_deduped"] += deduped
                elif successor:
                    logger.warning(
                        "%s was renamed to %s, but %s is not in gw_events; "
                        "leaving unresolved until it is ingested",
                        row.superevent_id, successor, successor,
                    )

            # A hand-written DESCRIPTIONS entry is keyed on superevent_id, so a
            # rename silently strands it: the key stops matching anything the
            # feed serves and the description is never rendered again. That is
            # exactly how DESCRIPTIONS["GW231123"] died. Nothing else surfaces
            # it, so say so loudly at the moment the ID is retired.
            if row.superevent_id in DESCRIPTIONS:
                logger.warning(
                    "DESCRIPTIONS[%r] is now keyed on a retired superevent_id "
                    "and will never be served; re-key it to %s",
                    row.superevent_id,
                    row.superseded_by or "its successor once one is identified",
                )

            if row.superevent_id in newly_missing:
                if row.superseded_by:
                    report["retired"].append(
                        {
                            "superevent_id": row.superevent_id,
                            "superseded_by": row.superseded_by,
                        }
                    )
                else:
                    report["retired_unresolved"].append(row.superevent_id)

        # A row GWOSC serves again is no longer retired.
        for row in rows:
            if row.retired_at is not None and row.superevent_id in index.known_names:
                row.retired_at = None
                row.superseded_by = None
                report["unretired"].append(row.superevent_id)

        await session.commit()

        logger.info(
            "GW retirement reconciliation: %d checked, %d newly retired with a "
            "documented successor, %d newly retired WITHOUT one (human review "
            "required: %s), %d un-retired, %d candidates relinked, %d deduped",
            report["checked"],
            len(report["retired"]),
            len(report["retired_unresolved"]),
            ", ".join(report["retired_unresolved"]) or "none",
            len(report["unretired"]),
            report["candidates_relinked"],
            report["candidates_deduped"],
        )
        return report

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

    async def get_all_events(
        self,
        session: AsyncSession,
        significance: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Get GW events with their properties, optionally filtered and paginated.

        Returns ``(events, total)``. ``total`` is the COUNT(*) of rows
        matching ``significance`` (before pagination); ``events`` is the
        requested page, in event_time-descending order — or every matching
        row when ``limit`` is None (used by the single-event route, which
        needs to search across everything, not one page of it). Both the
        COUNT query and the page query apply the identical WHERE clause, so
        ``total`` and ``events`` are always consistent with each other.

        Pagination is real SQL LIMIT/OFFSET, not an in-memory slice: only the
        rows in the requested page are fetched, and the per-event candidate
        count below only runs for those rows — not the full filtered set.

        When `significance` is given, filters at the SQL level to events whose
        properties.significance matches. `significance="unclassified"` matches
        rows with no "significance" key (or a NULL properties column):
        Postgres's ->> operator propagates NULL through both a missing key and
        a NULL left-hand side, so a single IS NULL check covers both, matching
        the same fallback convention used below and in get_significance_counts.
        Callers are responsible for validating `significance` is one of
        SIGNIFICANCE_TIERS before calling this — see GET /api/gw/events.
        """
        where_clause = None
        if significance is not None:
            sig_column = GWEvent.properties["significance"].astext
            where_clause = (
                sig_column.is_(None)
                if significance == "unclassified"
                else sig_column == significance
            )

        count_query = select(func.count()).select_from(GWEvent)
        if where_clause is not None:
            count_query = count_query.where(where_clause)
        total = (await session.execute(count_query)).scalar_one()

        query = select(GWEvent).order_by(GWEvent.event_time.desc())
        if where_clause is not None:
            query = query.where(where_clause)
        if limit is not None:
            query = query.limit(limit).offset(offset)

        result = await session.execute(query)
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
                # Significance tier ("confident" / "marginal" / "preliminary" /
                # "unknown"), matching the aggregate reported by
                # get_significance_counts/GET /api/gw/stats. Rows ingested
                # before significance tracking existed have no key in
                # properties; report "unclassified" rather than defaulting to
                # any real tier, same convention as get_significance_counts.
                "significance": props.get("significance", "unclassified"),
                # Raw GWOSC catalog tag this event was classified from (e.g.
                # "GWTC-5.0", "GWTC-1-marginal"). None for pre-significance rows.
                "catalog": props.get("catalog"),
                # Soft-retirement state, written by reconcile_retired_events.
                # `retired_at` non-null means GWOSC no longer serves this ID.
                # `superseded_by` names the replacement when GWOSC documented
                # the rename, and stays null when it did not -- a retired row
                # with no successor is awaiting a human decision, not resolved.
                # Retired rows are still listed rather than filtered out: the
                # row is the only record the retired ID ever existed, and
                # hiding it would silently change /api/gw/events behaviour.
                "retired_at": evt.retired_at.isoformat() if evt.retired_at else None,
                "superseded_by": evt.superseded_by,
            })

        return output, total

    async def get_significance_counts(self, session: AsyncSession) -> dict[str, int]:
        """Count ingested GW events by significance tier, queried live.

        Reads `properties.significance` off every row in `gw_events` (a value
        written by `fetch_gwosc_events`/`_classify_significance` at ingest
        time — see there for tier definitions). Rows ingested before
        significance tracking existed have no `significance` key and are
        reported under "unclassified" so they are visible rather than silently
        miscounted.

        This is the queryable source of truth for "how many confident GW
        events are ingested" — use this instead of a hardcoded number in the
        README or paper; it reflects whatever is actually in the database
        right now.
        """
        result = await session.execute(select(GWEvent.properties))
        counts: dict[str, int] = {}
        for (props,) in result.all():
            tier = (props or {}).get("significance", "unclassified")
            counts[tier] = counts.get(tier, 0) + 1
        return counts
