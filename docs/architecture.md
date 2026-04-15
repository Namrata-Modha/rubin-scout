# Architecture

## System Overview

Rubin Scout is a downstream alert processing tool that ingests transient discoveries from the IAU Transient Name Server (TNS), enriches them with machine learning classifications from ALeRCE and catalog cross-matches from SIMBAD, and serves them through a secured REST API and interactive dashboard. Its unique feature is gravitational wave cross-matching: finding optical counterparts to LIGO detections.

```
                ┌──────────────────────────────────────────┐
                │             UPSTREAM SOURCES              │
                │                                          │
                │  TNS ────────┐    LIGO/GWTC ──┐          │
                │  (daily CSV)  │    (GW events) │          │
                │              │                 │          │
                │  ALeRCE ─────┤    (enrich ML)  │          │
                │  (REST API)  │                 │          │
                │              │                 │          │
                │  SIMBAD ─────┘    (cross-match)│          │
                └──────────────┼──────────────────┼──────────┘
                               │                 │
                               ▼                 ▼
                ┌──────────────────────────────────────────┐
                │          BACKEND (FastAPI)                 │
                │                                          │
                │  ┌─────────────┐  ┌──────────────────┐   │
                │  │ Ingestion   │  │ GW Cross-Match   │   │
                │  │ TNS → primary│  │ (skymap centroid  │   │
                │  │ ALeRCE → ML  │  │  + time window)   │   │
                │  │ SIMBAD → xmatch│ │                  │   │
                │  └──────┬──────┘  └────────┬─────────┘   │
                │         │                  │             │
                │  ┌──────┴──────────────────┴─────────┐   │
                │  │       Security Layer               │   │
                │  │  Rate limiting (slowapi)           │   │
                │  │  Input validation (Pydantic)       │   │
                │  │  Admin API key (write endpoints)   │   │
                │  │  OWASP security headers            │   │
                │  │  Request size limiting              │   │
                │  └──────┬────────────────────────────┘   │
                │         │                                │
                │  ┌──────┴──────┐                         │
                │  │ PostgreSQL  │                         │
                │  │ + PostGIS   │                         │
                │  │ (Supabase)  │                         │
                │  └─────────────┘                         │
                └──────────────┬───────────────────────────┘
                               │
                ┌──────────────┴───────────────────────────┐
                │          FRONTEND (React)                  │
                │                                          │
                │  Dashboard ─── GW Events ─── Alert Detail │
                │  Sky Map      Cross-Match    Light Curve   │
                │  Card Grid    Candidates     Probabilities │
                │                                          │
                │  cosmos.js (translation layer)            │
                │  "SNIa" → "Exploding white dwarf 💥"      │
                └──────────────────────────────────────────┘
```

## Data Flow (Detailed)

**Primary Source: TNS Daily CSV**

Every 15 minutes, the ingestion scheduler fetches the previous day's TNS CSV file (updated daily at midnight UTC). TNS provides spectroscopically confirmed transient discoveries with precise sky coordinates, discovery dates, redshifts, and human-verified classifications.

**Enrichment Layer 1: ALeRCE ML Classifications**

For objects without spectroscopic classifications or to add probability distributions, the system queries ALeRCE's REST API for machine learning classifications. ALeRCE runs two classifiers on ZTF alerts:
- **Stamp classifier** (fast, image-based)
- **Light curve classifier** (slower, uses full photometric history)

The system stores the full probability vector, not just the top prediction, allowing users to assess classification confidence.

**Enrichment Layer 2: Light Curves**

For each object, the system attempts to fetch light curve data (photometry) from both TNS (via API) and ALeRCE. Light curves are stored as individual detections in a time-series table for efficient retrieval.

**Enrichment Layer 3: SIMBAD Cross-Matching**

Every unenriched object is cross-matched against SIMBAD within 5 arcseconds to identify known astronomical associations (galaxies, AGN, known variables, etc.).

**Storage**

All data flows into PostgreSQL with PostGIS for spatial indexing. The `objects` table contains one row per transient with position, classification, and cross-match results. The `detections` table contains individual brightness measurements forming each object's light curve.

## Security Architecture

Security is layered, not bolted on. Every request passes through multiple checks.

**Rate Limiting.** slowapi with in-memory storage. Defaults: 60 req/min for reads, 30/min for spatial queries, 10/min for writes, 5/min for seed endpoints. Returns 429 with Retry-After header.

**Input Validation.** All inputs validated before reaching the database:
- Object IDs validated against TNS/ZTF naming patterns
- GW superevent IDs validated against LIGO pattern (`(GW|S)\d{6}[a-z]?`)
- Classification filters checked against an allowlist of valid classes
- Subscription emails validated with regex, webhook URLs format-checked
- `filter_config` JSON keys validated against an allowlist
- All strings have length limits

**Admin API Key.** Write endpoints (create/update/delete subscriptions, seed GW events) require an `X-API-Key` header in production. In development, all endpoints are open. The key is stored as `ADMIN_API_KEY` environment variable and compared using constant-time `secrets.compare_digest` to prevent timing attacks.

**Security Headers.** Every response includes: X-Content-Type-Options: nosniff, X-Frame-Options: DENY, X-XSS-Protection: 1; mode=block, Referrer-Policy: strict-origin-when-cross-origin, Permissions-Policy restricting camera/mic/geolocation.

**CORS.** Restricted to configured origins only. Methods limited to GET/POST/PATCH/DELETE. Headers limited to Content-Type and X-API-Key. Credentials disabled.

**Request Size.** 1 MB body limit prevents oversized payloads.

**Database.** Supabase with Row Level Security enabled on `subscriptions` and `ingestion_log` tables. All queries use SQLAlchemy ORM or parameterized `text()` queries (no string concatenation).

## Database Design

Seven tables, designed around two core entities: **Objects** (astronomical sources) and **Detections** (brightness measurements).

**objects** -- One row per unique transient. Contains sky position (RA/Dec + PostGIS geography for spatial indexing), classification (from TNS spectroscopy or ALeRCE ML), and SIMBAD cross-match results.

**detections** -- Individual brightness measurements forming each object's light curve. Indexed by (oid, detection_time DESC) for fast light curve retrieval. Includes `rb` (Real/Bogus) score for quality filtering.

**classification_probabilities** -- Full probability vector from ALeRCE's classifiers, not just the top prediction. Unique constraint on (oid, classifier_name, class_name).

**gw_events** -- LIGO/Virgo gravitational wave events with classification probabilities (BNS, NSBH, BBH, Terrestrial), skymap URLs, and human descriptions.

**gw_candidates** -- Optical counterpart candidates linked to GW events via cross-matching.

**subscriptions** -- Notification filter configs stored as JSONB. RLS enabled.

**ingestion_log** -- Tracks ingestion runs for monitoring and deduplication. RLS enabled.

## Gravitational Wave Cross-Matching

The unique feature. When a user clicks "Search for optical counterparts" on a GW event:

1. Load the event's sky position (RA/Dec centroid) and 90% credible area
2. Estimate the effective search radius from the credible area (r = sqrt(area/pi))
3. Define a time window (7 days before to 30 days after the GW event)
4. Query PostGIS for all objects within the search radius AND time window
5. Store candidate associations in `gw_candidates` table
6. Return results with angular distances and "in 90% region" flags

For poorly localized events (no centroid available), falls back to time-window-only search filtered by transient classifications most likely to be GW counterparts (SNe, TDE, KN, novae).

## Cosmos Translation Layer

The frontend's `cosmos.js` module transforms raw astronomy data into accessible language:

- Classification codes mapped to names, emoji, one-line summaries, and full descriptions
- RA/Dec coordinates mapped to approximate constellation names
- Timestamps formatted as relative time ("3 days ago") and human dates
- Each alert gets a generated summary sentence combining type, location, and observation history

This is the design philosophy that differentiates Rubin Scout from technical broker interfaces.

## Deployment Stack

**Local development:** Docker Compose for PostgreSQL. Backend and frontend run directly with hot-reload. `start.bat` / `stop.bat` for one-click Windows operation.

**Production:**
- **Supabase** (US East) -- PostgreSQL 17 with PostGIS. Free tier, no expiry.
- **Render** -- Free Python web service for FastAPI backend. Auto-deploys from GitHub.
- **Vercel** -- Free frontend hosting. Auto-deploys from GitHub.

Environment variables for production:
- `DATABASE_URL` -- Supabase connection string
- `APP_ENV` -- `production` (disables Swagger docs, enforces admin key)
- `CORS_ORIGINS` -- Vercel frontend URL
- `ADMIN_API_KEY` -- Generated token for write endpoints
- `VITE_API_URL` -- Render backend URL (frontend env var)
- `TNS_USER_ID`, `TNS_USER_NAME` -- TNS credentials for CSV downloads
- `TNS_BOT_ID`, `TNS_BOT_NAME`, `TNS_API_KEY` -- TNS bot credentials for API access (optional)

## API Design

FastAPI auto-generates OpenAPI docs at `/docs` (development only).

Key endpoints:
- `GET /api/alerts/recent` -- Paginated alerts with classification, time, and probability filters
- `GET /api/alerts/{oid}` -- Full object detail with light curve and probabilities
- `GET /api/alerts/conesearch/query` -- Spatial search by RA/Dec/radius
- `GET /api/gw/events` -- All gravitational wave events
- `POST /api/gw/events/{id}/crossmatch` -- Run optical counterpart search
- `POST /api/subscriptions` -- Create notification subscription (admin key in prod)

All coordinates use ICRS (J2000) in degrees. Time parameters accept hour-based lookback windows (max 87600 = 10 years). Pagination uses limit/offset.

## Future: Real-Time Kafka Streaming

Currently the system polls TNS daily and ALeRCE every 15 minutes via REST APIs. The roadmap includes:
- Kafka consumer for ALeRCE's real-time classified alert stream
- Pitt-Google Pub/Sub for LSST alerts when full survey operations begin
- Sub-second latency for new transient discovery to dashboard display

The infrastructure (message queue handling, async workers) is designed for this upgrade. The current polling approach works well for the scale of TNS discoveries (10-100 per day) and provides a stable foundation before adding real-time complexity.