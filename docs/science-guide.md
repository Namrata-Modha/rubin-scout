# Science Guide

This document explains how Rubin Scout processes astronomical data, what cross-matching it performs, and how to interpret the results. It is written for astronomers who want to evaluate the tool's scientific utility.

## Data Sources

**ALeRCE Broker** is the primary upstream source. ALeRCE ingests alerts from the Zwicky Transient Facility (ZTF) and, as of 2026, the Vera C. Rubin Observatory's LSST alert stream. ALeRCE applies two machine learning classifiers to each alert:

- A stamp-based classifier that uses the 63x63 pixel cutout images for rapid classification into five top-level classes (supernova, active galactic nuclei, variable star, asteroid, bogus).
- A light-curve-based classifier that uses the full multi-band photometric history for refined sub-classification (SNIa, SNII, SNIbc, SLSN, AGN, TDE, etc.).

Rubin Scout queries the ALeRCE API for objects classified as transients with a configurable minimum confidence threshold (default: 0.5). We deliberately exclude variable star and asteroid classifications to focus the tool on extragalactic transients and high-energy events.

**Pitt-Google Broker** serves as a secondary source, providing the same ZTF/LSST alerts through Google Cloud Pub/Sub. This gives redundancy and enables comparison between broker classifications.

## What Rubin Scout Adds

Rubin Scout is a downstream tool. It does not reclassify alerts. Its value comes from three things:

**Cross-matching.** For each ingested object, we query SIMBAD within 5 arcseconds of the alert position. This identifies whether the transient is coincident with a known astronomical object (galaxy, star, AGN, etc.). The SIMBAD object type (OTYPE) is stored and displayed. Future versions will add NED cross-matching (for extragalactic distance estimates) and TNS cross-matching (to check if the transient has already been reported).

**Filtering and notification.** Scientists can define subscription filters (classification type, minimum confidence, sky region, brightness threshold) and receive notifications via Slack, email, or webhook when new objects match. This is the core use case: reducing the 7-million-alert-per-night firehose to the specific events relevant to a research program.

**Multi-messenger cross-matching.** When a gravitational wave event is detected by LIGO/Virgo/KAGRA, Rubin Scout can cross-match the GW skymap (HEALPix format) with optical transients in its database. This identifies candidate electromagnetic counterparts within the GW credible region, which is essential for kilonova searches and multi-messenger follow-up coordination.

## Classification Taxonomy

Rubin Scout tracks the following ALeRCE light-curve classifier classes:

| Class | Description | Typical Timescale |
|-------|-------------|------------------|
| SNIa | Type Ia supernova (thermonuclear) | Weeks to months |
| SNII | Type II supernova (core collapse, hydrogen-rich) | Weeks to months |
| SNIbc | Type Ib/c supernova (stripped-envelope core collapse) | Weeks to months |
| SLSN | Superluminous supernova | Months |
| TDE | Tidal disruption event (star disrupted by SMBH) | Months to years |
| KN | Kilonova (neutron star merger) | Days |
| AGN | Active galactic nucleus | Variable (stochastic) |
| Blazar | Blazar (AGN with jet pointed at Earth) | Variable |
| QSO | Quasar | Variable |
| CV/Nova | Cataclysmic variable or nova | Days to weeks |

Classification probabilities from ALeRCE are stored for all classes, not just the top prediction. The dashboard and API expose the full probability vector so scientists can assess classification confidence.

## Spatial Queries

The cone search endpoint uses PostGIS with a geography-type column for proper great-circle distance calculation on the celestial sphere. Coordinates are stored in ICRS (J2000) as expected by the astronomical community. The search radius is specified in arcseconds.

## Time Conventions

All timestamps are stored as timezone-aware UTC. The Modified Julian Date (MJD) is preserved alongside the converted timestamp for compatibility with astronomical tools. The MJD-to-UTC conversion uses Astropy's Time class.

## Limitations and Caveats

- Rubin Scout relies entirely on ALeRCE's classifications. We do not perform independent photometric classification. If ALeRCE misclassifies an object, our tool will propagate that error.
- SIMBAD cross-matching uses a fixed 5-arcsecond radius, which may produce spurious matches in crowded fields (galactic plane, dense cluster cores). The match distance is always reported.
- The ingestion pipeline polls ALeRCE every 15 minutes by default. This is not real-time. The Kafka consumer (when enabled) reduces latency to seconds.
- Light curve data is stored as-is from ALeRCE. We do not apply additional photometric corrections, host-galaxy subtraction, or extinction corrections.
- Multi-messenger GW cross-matching currently uses a simple HEALPix pixel-in-credible-region check. It does not account for distance (luminosity distance posterior) or host galaxy probability.

## Data Attribution

If you use Rubin Scout in published work, please cite:

- The ALeRCE broker: Forster et al. (2021), AJ, 161, 242
- The Zwicky Transient Facility: Bellm et al. (2019), PASP, 131, 018002
- The LIGO/Virgo/KAGRA collaboration for any GW data used
- Astropy for coordinate and time conversions

Rubin Scout itself is open-source software (MIT License) and can be cited via its GitHub repository.
