# Science Guide

How Rubin Scout processes astronomical data, what cross-matching it performs, and how to interpret the results. Written for astronomers evaluating the tool's scientific utility, and for curious non-experts who want to understand what's happening under the hood.

## What Rubin Scout Is (and Isn't)

Rubin Scout is a **downstream visualization and cross-matching tool**. It does not classify alerts, run photometric pipelines, or process raw telescope data. It consumes pre-classified alerts from community brokers (primarily ALeRCE) and adds value through catalog cross-matching, gravitational wave spatial cross-matching, human-accessible translations, and notification subscriptions.

Think of it as the layer between "a telescope detected something" and "a human understands what it means."

## Data Flow

1. **ALeRCE** ingests raw alerts from ZTF (and soon Rubin/LSST), applies stamp-based and light-curve-based ML classifiers, and publishes classified objects via REST API and Kafka streams.
2. **Rubin Scout's ingestion worker** polls ALeRCE every 15 minutes for new transients matching target classes (SNIa, SNII, SNIbc, SLSN, TDE, KN, AGN, Blazar, QSO, CV/Nova).
3. **Enrichment** queries SIMBAD within 5 arcseconds of each alert position to identify known associations.
4. **Storage** in PostgreSQL with PostGIS for spatial indexing.
5. **API and dashboard** serve the enriched data with human-friendly translations.

## Classification Taxonomy

Rubin Scout tracks the following ALeRCE light-curve classifier classes:

| Class | Full Name | What It Is | Typical Timescale |
|-------|-----------|-----------|------------------|
| SNIa | Type Ia Supernova | White dwarf thermonuclear detonation | Weeks to months |
| SNII | Type II Supernova | Massive star core collapse (hydrogen-rich) | Weeks to months |
| SNIbc | Type Ib/c Supernova | Stripped-envelope core collapse | Weeks to months |
| SLSN | Superluminous Supernova | Extremely energetic explosion (10-100x normal SN) | Months |
| TDE | Tidal Disruption Event | Star torn apart by supermassive black hole | Months to years |
| KN | Kilonova | Neutron star merger (GW counterpart) | Days |
| AGN | Active Galactic Nucleus | Supermassive black hole accretion | Variable (stochastic) |
| Blazar | Blazar | AGN with jet pointed at Earth | Variable |
| QSO | Quasar | Luminous AGN at cosmological distance | Variable |
| CV/Nova | Nova | Thermonuclear flash on white dwarf surface | Days to weeks |

The dashboard displays the full probability vector, not just the top prediction. This lets users assess classification confidence and identify ambiguous cases.

## Gravitational Wave Cross-Matching

This is Rubin Scout's unique scientific feature.

**How it works:** When a user selects a GW event, the system queries the database for optical transients that fall within the event's credible sky region and a configurable time window.

**Spatial matching:** For well-localized events (like GW170817 with 28 deg2 at 90% credibility), the system approximates the credible region as a circle with radius r = sqrt(area_90 / pi) and uses PostGIS ST_DWithin for efficient spatial queries from the skymap centroid.

**Time window:** Searches from 7 days before the GW event (to catch pre-existing transients that might be coincident) to 30 days after (configurable up to 365 days).

**Limitations of the current approach:**
- Uses skymap centroid + circular approximation rather than the full HEALPix probability map. This is a simplification. The actual GW skymap is an irregular probability distribution, often banana-shaped or multi-modal. The circular approximation works well for compact skymaps (GW170817) but is less accurate for large, irregular ones.
- Does not incorporate luminosity distance information from the GW posterior. A future version could filter candidates by host galaxy redshift versus the GW distance estimate.
- Does not account for the probability density within the credible region. All candidates within the region are treated equally.

**Pre-loaded events:** Six notable GW events from GWTC catalogs are included with human descriptions: GW170817 (first BNS with EM counterpart), GW190425 (second BNS), GW190521 (most massive BBH), GW200105 and GW200115 (first NSBH detections), and GW231123 (highest-mass BBH in GWTC-4.0).

## SIMBAD Cross-Matching

For each ingested object, Rubin Scout queries SIMBAD within 5 arcseconds. This identifies whether the transient position coincides with a known astronomical object (galaxy, star, AGN, etc.).

**What the match distance means:** 5 arcseconds is a generous radius for point-source matching at ZTF's ~2 arcsecond resolution. In crowded fields (galactic plane, dense clusters), spurious matches are possible. The match distance is always stored and displayed.

**Column name handling:** SIMBAD recently changed their API column names (MAIN_ID to main_id, etc.). Rubin Scout handles both formats.

## Coordinate Systems

- All sky positions stored in **ICRS (J2000)**, the standard astronomical reference frame
- RA in degrees (0 to 360), Dec in degrees (-90 to +90)
- PostGIS geography column for proper great-circle distance calculations
- Cone search radius specified in arcseconds

## Time Conventions

- All timestamps stored as timezone-aware UTC
- Modified Julian Date (MJD) preserved alongside converted timestamps for compatibility with astronomical tools
- MJD-to-UTC conversion uses Astropy's Time class
- Dashboard displays relative time ("3 days ago") for readability

## The Cosmos Translation Layer

Rubin Scout's `cosmos.js` module maps every classification to:
- A human-readable name and one-line summary
- An emoji identifier
- A full paragraph explaining what the event type is and why it matters
- An approximate constellation name derived from RA/Dec coordinates

This is not dumbing down the science. It's making the same information accessible to a broader audience. The raw data (RA/Dec, MJD, magnitude, classifier probabilities) is always available through the API and detail pages.

## Limitations and Caveats

1. **Classification accuracy depends on ALeRCE.** Rubin Scout does not reclassify. If ALeRCE misclassifies an object, that error propagates.

2. **SIMBAD matching is positional only.** A 5-arcsecond match doesn't guarantee physical association. It means "there's a known object near this position."

3. **GW cross-matching is approximate.** The circular skymap approximation is a simplification of the real probability distribution. See the GW section above for details.

4. **Light curves are stored as-is.** No host-galaxy subtraction, extinction correction, or photometric recalibration is applied.

5. **Constellation mapping is approximate.** Uses simplified rectangular boundaries, not the official IAU boundaries. Good for "it's in the direction of Orion" level context, not for precise boundary determination.

6. **Ingestion is polling-based (15-minute intervals).** Not real-time. The planned Kafka consumer will reduce latency to seconds.

## Data Attribution

If you use Rubin Scout in published work, please cite:

- **ALeRCE:** Forster et al. (2021), AJ, 161, 242
- **ZTF:** Bellm et al. (2019), PASP, 131, 018002
- **LIGO/Virgo/KAGRA:** Per event as specified by the LVK collaboration
- **SIMBAD:** Wenger et al. (2000), A&AS, 143, 9
- **Astropy:** Astropy Collaboration et al. (2022), ApJ, 935, 167

Rubin Scout is open-source software (MIT License) and can be cited via its GitHub repository: https://github.com/Namrata-Modha/rubin-scout
