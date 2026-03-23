# Architecture

## System Overview

Rubin Scout is a downstream alert processing tool that ingests classified transient alerts from community brokers, enriches them with catalog cross-matches, and serves them through a secured REST API and interactive dashboard. Its unique feature is gravitational wave cross-matching: finding optical counterparts to LIGO detections.

```
                ┌──────────────────────────────────────────┐
                │             UPSTREAM SOURCES              │
                │                                          │
                │  ALeRCE ────┐    LIGO/GraceDB ──┐        │
                │  (API/Kafka) │    (GWTC events)  │        │
                │              │                   │        │
                │  SIMBAD ─────┤    (cross-match)  │        │
                └──────────────┼───────────────────┼────────┘
                               │                   │
                               ▼                   ▼
                ┌──────────────────────────────────────────┐
                │          BACKEND (FastAPI)                 │
                │                                          │
                │  ┌─────────────┐  ┌──────────────────┐   │
                │  │ Ingestion   │  │ GW Cross-Match   │   │
                │  │ (ALeRCE     │  │ (skymap centroid  │   │
                │  │  polling)   │  │  + time window)   │   │
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

## Security Architecture

Security is layered, not bolted on. Every request passes through multiple checks.

**Rate Limiting.** slowapi with in-memory storage. Defaults: 60 req/min for reads, 30/min for spatial queries, 10/min for writes, 5/min for seed endpoints. Returns 429 with Retry-After header.

**Input Validation.** All inputs validated before reaching the database:
- Object IDs validated against ZTF naming regex (`ZTF\d{2}[a-z]{7,10}`)
- GW superevent IDs validated against LIGO pattern (`(GW|S)\d{6}[a-z]?`)
- Classification filters checked against an allowlist of valid ALeRCE classes
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

**objects** -- One row per unique transient. Contains sky position (RA/Dec + PostGIS geography for spatial indexing), ALeRCE classification, and SIMBAD cross-match results.

**detections** -- Individual brightness measurements forming each object's light curve. Indexed by (oid, detection_time DESC) for fast light curve retrieval.

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

This is the design philosophy that differentiates Rubin Scout from ALeRCE's own explorer.

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
