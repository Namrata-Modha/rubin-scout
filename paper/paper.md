---
title: 'Rubin Scout: A Lightweight Multi-Messenger Alert Aggregator for Downstream Transient Follow-Up Planning'
tags:
  - Python
  - astronomy
  - transients
  - gravitational waves
  - fast radio bursts
  - multi-messenger astronomy
  - alert brokers
  - ZTF
  - LSST
authors:
  - name: Namrata Sharad Modha
    orcid: 0009-0008-3713-2660
    affiliation: 1
affiliations:
  - name: Independent Researcher, London, Ontario, Canada
    index: 1
date: 23 May 2026
bibliography: paper.bib
---

# Summary

Rubin Scout is an open-source multi-messenger astronomical alert aggregation platform that consolidates transient detections from multiple sky surveys and gravitational wave catalogs into a single searchable database with a REST API and interactive web dashboard. It ingests spectroscopically classified transients from the IAU Transient Name Server (TNS), machine-learning classifications and photometry from the ALeRCE broker, gravitational wave events from the Gravitational Wave Open Science Center (GWOSC) catalog [@LVK2025], and Fast Radio Burst detections from the CHIME/FRB Catalog 1 [@CHIMEFRB2021]. Each alert is enriched with cross-matches against the SIMBAD astronomical database [@Wenger2000] and optical cutout imagery from the Legacy Survey. The platform exposes a programmatic REST API including a dedicated follow-up planning endpoint designed for integration with downstream observational pipelines. The source code is available at https://github.com/Namrata-Modha/rubin-scout and the live platform is accessible at https://rubin-scout.vercel.app.

# Statement of Need

The science operations of the Vera C. Rubin Observatory and its Legacy Survey of Space and Time (LSST), which began issuing scientific alerts in February 2026 and are expected to reach approximately seven million alerts per night during full survey operations [@Ivezic2019], represent a step change in the volume of transient events requiring rapid characterization. Research teams operating follow-up facilities face the challenge of efficiently triaging this alert stream against multi-messenger context to identify the most scientifically valuable targets before telescope time is committed.

When a transient is detected, an astronomer typically needs to answer several questions before confirming a spectroscopic follow-up observation: Has this object been detected before by ZTF? Is it a known catalog source in SIMBAD? Did a gravitational wave event occur in this sky region recently? Is the target observable from the facility tonight? Currently, each of these questions requires a separate lookup across separate platforms, introducing latency and manual coordination overhead that is unsustainable at LSST alert rates.

Rubin Scout addresses this workflow gap by providing a unified downstream aggregation layer that consolidates all four lookups into a single programmatic API call, returning a structured recommendation with plain English justification. The platform is designed to be self-hostable on commodity infrastructure, requiring no specialized cluster management or large cloud budgets.

# State of the Field

The primary alert broker ecosystem for ZTF and LSST comprises several mature platforms: ALeRCE [@Forster2021], Fink [@Moller2021], and Lasair [@Smith2019]. Each connects directly to the ZTF Kafka alert stream, processing up to one million raw alerts per night with sophisticated machine-learning classification pipelines. These platforms are optimized for high-throughput stream processing and serve as the first point of contact for the alert data.

Rubin Scout occupies a deliberately different position in this ecosystem. Rather than competing with primary brokers by connecting to raw Kafka streams — which requires sustained cloud infrastructure, dedicated engineering capacity, and approved broker status from the Rubin Observatory — Rubin Scout acts as a downstream consumer of the APIs that primary brokers already expose. This architectural choice reflects a specific use case: research teams at smaller facilities who need consolidated multi-messenger context for a manageable number of follow-up candidates per night, not the infrastructure to process millions of raw alerts.

The key distinctions from existing platforms are:

**Multi-messenger consolidation in one interface:** Neither ALeRCE, Fink, nor Lasair natively combines optical transient alerts with gravitational wave events from the GWTC catalog and Fast Radio Bursts from CHIME/FRB in a single unified database, with a spatial cross-matching architecture for GW events (pending sky-localization ingestion) and matching logic that respects each messenger's positional precision, including deliberately excluding FRBs from tight optical-scale cross-matching given their much larger (~arcminute) localization uncertainty.

**Programmatic follow-up planning API:** The `/api/ilmt/followup` endpoint provides a single call that returns ZTF prior history, SIMBAD catalog association, GW temporal and spatial coincidence, and observatory visibility simultaneously. No existing public broker API provides this consolidated response for follow-up planning workflows.

**Self-hostable commodity deployment:** The full stack runs on free-tier cloud services (Vercel, Render, Supabase), making it accessible to research groups without dedicated infrastructure budgets.

The TOM Toolkit [@Street2018] provides a Django-based framework for building observation management systems but requires local deployment and does not provide multi-messenger alert ingestion out of the box. Rubin Scout complements rather than replaces TOM-based workflows by providing the alert aggregation layer that feeds target selection.

# Software Design

## Architectural Choices and Trade-offs

Rubin Scout is built as a two-tier application: a Python FastAPI [@FastAPI] backend with asynchronous SQLAlchemy database access, and a React frontend. The backend ingestion pipeline runs on APScheduler with a 15-minute cycle, consuming external APIs rather than connecting to raw telescope data streams.

**Downstream aggregation over stream processing:** The decision to consume broker APIs rather than connect directly to Kafka streams was deliberate. Direct Kafka stream processing requires sustained connection management, alert deduplication at scale, and approved broker status. For the use case Rubin Scout targets — consolidated follow-up planning for a set of candidate objects — the broker APIs already provide cleaned, classified, and deduplicated data. Consuming these APIs introduces a 15-minute latency relative to raw stream processing, which is acceptable for spectroscopic follow-up planning where telescope scheduling operates on hour timescales.

**PostGIS ST_DWithin over HEALPix:** Gravitational wave sky localization maps are natively represented as HEALPix probability distributions [@LVK2025]. Full HEALPix matching requires the `healpy` library, which has known installation issues on Windows development environments and adds significant complexity to deployment. Rubin Scout approximates the 90% credible region as a circle of equivalent area using $r = \sqrt{A_{90}/\pi}$ and uses PostGIS `ST_DWithin` for spatial matching once an event's sky localization is known. This approximation introduces geometric error for non-circular skymaps, common in two-detector events, but is exact for a circular region and adequate for candidate shortlisting. Sky-localization ingestion — parsing per-event skymaps into a centre and 90% credible area — is not yet implemented; the cross-match endpoint returns an explicit error (HTTP 422) until localization data is available for an event. The spatial-matching logic is implemented and unit-tested, but not yet exercised against real localization data in production.

**IERS-B table over live IERS download:** Observatory visibility computations use the IERS-B table shipped with astropy rather than downloading current IERS data at runtime. This ensures reliable operation in serverless deployment environments where outbound connections to IERS servers may be rate-limited or unavailable, at the cost of small timing errors in astronomical dark-window calculations for dates far in the future.

**Asynchronous concurrent lookups:** The `/api/ilmt/followup` endpoint executes SIMBAD cross-matching and visibility computation concurrently using `asyncio.gather`, reducing end-to-end response latency compared to sequential execution.

# Research Impact

Rubin Scout has demonstrated readiness for integration into active research workflows. The ILMT Follow-Up Planner endpoint (`/api/ilmt/followup`) was designed specifically around the operational workflow of the International Liquid Mirror Telescope at Devasthal Observatory, India [@Surdej2025]. The ILMT conducts a nightly survey of a 22 arcminute strip centered at declination $+29°21'41''$ and accumulates pre-trigger baseline imaging of this strip every night without re-pointing. The endpoint consolidates the four lookups required before committing ILMT time to a candidate — ZTF prior history, SIMBAD catalog association, GW temporal coincidence — with spatial coincidence when sky localization is available — and Devasthal visibility — into a single API call returning a structured `PRIORITY_FOLLOWUP`, `NEEDS_MORE_DATA`, or `LIKELY_KNOWN` recommendation.

The platform currently ingests 360 confidently-detected gravitational wave events from GWTC-1 through the current GWOSC catalog, as of 23 July 2026, of 388 total ingested including marginal and preliminary candidates (see GET /api/gw/stats), 536 Fast Radio Burst detections from CHIME/FRB Catalog 1 [@CHIMEFRB2021] (474 one-off events and 62 bursts from 18 repeating sources), and grows its optical transient database daily from TNS and ALeRCE. Seven observatory presets are built in, covering facilities in India, Canada, Hawaii, Chile, the Canary Islands, and California, with support for custom coordinates.

The platform's public REST API at `https://rubin-scout-api.onrender.com` is accessible without authentication, enabling direct programmatic integration from observational pipelines such as PyLMT without requiring any infrastructure setup by the consuming team.

# Implementation

## Data Sources and Ingestion

**IAU Transient Name Server (TNS):** Rubin Scout ingests the daily public CSV release from the TNS, which provides spectroscopically classified transients from the global astronomical community. TNS classifications are mapped to a normalized taxonomy (SNIa, SNII, SNIbc, SLSN, TDE, AGN, KN, CV/Nova) for consistency with ALeRCE output.

**ALeRCE Broker:** The ALeRCE Python client [@Forster2021] is used to retrieve machine-learning classifications and ZTF light curves for recent transients. Light curve data includes per-epoch photometry in the ZTF g, r, and i bands with measurement uncertainties. ALeRCE's `lc_classifier` probabilistic classifications are stored alongside TNS spectroscopic classifications, allowing the platform to surface objects with strong ML classification confidence even before spectroscopic confirmation.

**GWOSC / GWTC:** Gravitational wave events are ingested from the GWOSC public catalog API, which provides 360 confidently-detected gravitational wave events from GWTC-1 through the current GWOSC catalog, as of 23 July 2026, of 388 total ingested including marginal and preliminary candidates (see GET /api/gw/stats). GPS timestamps are converted to UTC using `astropy.time.Time` with `format='gps'` [@Astropy2022]. Merger type classification is inferred from component masses using the conventional 3 solar mass boundary between neutron stars and black holes.

**CHIME/FRB Catalog 1:** Fast Radio Burst detections are ingested from the CHIME/FRB Catalog 1 [@CHIMEFRB2021] via the CDS VizieR astronomical data service in VOTable format, parsed using `astropy.io.votable`. The catalog contains 536 FRB detections (474 one-off events and 62 bursts from 18 repeating sources) including dispersion measures, sky positions, and detection epochs.

**SIMBAD:** Each newly ingested object is cross-matched against SIMBAD [@Wenger2000] within a 5 arcsecond cone radius using `astroquery.simbad` [@Ginsburg2019], providing catalog associations and object type classifications for known sources.

**Legacy Survey:** Optical cutout images are constructed from the Legacy Survey cutout service using each object's sky coordinates, providing visual context for alert detail views.

# Testing

Rubin Scout is backed by a growing automated test suite (over 100 unit tests as of this writing), enforced by continuous integration. Tests are implemented using pytest [@pytest] and can be run with:

```bash
cd backend && pytest tests/ -v
```

# AI Usage Disclosure

Portions of this software and its documentation were developed with assistance from large language model tools. All AI-generated code, documentation, and paper text were reviewed, validated, and edited by the human author. The author takes full responsibility for the accuracy of all scientific claims, implementation details, and bibliographic references in this paper. All citations were independently verified against their source publications by the author prior to submission.

# Acknowledgements

The authors thank the ALeRCE collaboration for providing public API access to ZTF machine-learning classifications and light curves. TNS data is provided by the IAU Transient Name Server. Gravitational wave catalog data is provided by the GWOSC. CHIME/FRB Catalog 1 data is accessed via the CDS VizieR service. SIMBAD cross-matching uses the CDS SIMBAD database. This research made use of Astropy, a community-developed core Python package for Astronomy [@Astropy2022]. Legacy Survey imaging data is provided by the DESI Legacy Imaging Surveys.

# References
