# Architecture

## System Overview

Rubin Scout is a downstream alert processing tool that sits between community alert brokers (ALeRCE, Pitt-Google) and end-user scientists. It ingests, enriches, filters, and serves transient astronomical alerts.

```
                ┌──────────────────────────────────────────┐
                │             UPSTREAM BROKERS              │
                │                                          │
                │  ALeRCE ────┐    Pitt-Google ──┐         │
                │  (API/Kafka) │    (Pub/Sub)     │         │
                └──────────────┼─────────────────┼─────────┘
                               │                 │
                               ▼                 ▼
                ┌──────────────────────────────────────────┐
                │          INGESTION LAYER                  │
                │                                          │
                │  Polling Worker  │  Kafka Consumer        │
                │  (MVP, 15min)   │  (real-time, Week 5)   │
                └────────────────┬─────────────────────────┘
                                 │
                                 ▼
                ┌──────────────────────────────────────────┐
                │          ENRICHMENT LAYER                 │
                │                                          │
                │  SIMBAD cross-match (5" radius)          │
                │  NED cross-match (planned)               │
                │  TNS check (planned)                     │
                │  GW skymap cross-match (HEALPix)         │
                └────────────────┬─────────────────────────┘
                                 │
                                 ▼
                ┌──────────────────────────────────────────┐
                │            DATA LAYER                     │
                │                                          │
                │  PostgreSQL 16                            │
                │  + TimescaleDB (time-series hypertables)  │
                │  + PostGIS (spatial cone search)          │
                └────────────────┬─────────────────────────┘
                                 │
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
              ┌──────────┐ ┌──────────┐ ┌──────────┐
              │ FastAPI   │ │ Notifs   │ │ React    │
              │ REST API  │ │ Slack/   │ │ Dashboard│
              │ + WebSocket│ │ Email   │ │          │
              └──────────┘ └──────────┘ └──────────┘
```

## Database Design

The schema is designed around two core entities: **Objects** (astronomical sources) and **Detections** (individual brightness measurements).

**objects** is the primary table. One row per unique transient source, identified by ALeRCE's `oid`. Contains sky position (RA/Dec as float + PostGIS geography for spatial indexing), classification from ALeRCE, and cross-match results from SIMBAD.

**detections** is a TimescaleDB hypertable partitioned by `detection_time`. Stores the full light curve (brightness over time) for each object across multiple photometric bands. Hypertable partitioning enables efficient time-range queries across millions of rows.

**classification_probabilities** stores the full probability vector from ALeRCE's classifiers, not just the top prediction. This lets scientists evaluate classification confidence.

**subscriptions** stores user-defined notification filters as JSONB, enabling flexible boolean filter combinations without schema changes.

**gw_events** and **gw_candidates** support gravitational wave multi-messenger cross-matching.

## API Design

FastAPI auto-generates OpenAPI documentation at `/docs`. Key design decisions:

- All coordinates use ICRS (J2000) in degrees, the astronomical standard.
- Time parameters accept both ISO 8601 strings and hour-based lookback windows.
- Cone search uses PostGIS ST_DWithin for proper great-circle distance on the celestial sphere.
- Pagination uses limit/offset for simplicity. Cursor-based pagination is a planned improvement for high-volume use.
- Classification probabilities are always included in detail responses so scientists can make informed decisions about follow-up priority.

## Frontend Architecture

React with Vite. Key components:

- **SkyMap** renders a Mollweide all-sky projection using HTML Canvas, with alerts plotted as colored dots by classification. Supports hover tooltips and click-to-navigate.
- **LightCurveChart** uses Recharts ScatterChart with error bars and multi-band color coding.
- **AlertTable** is the primary data view with sortable columns and filter integration.
- **StatsBar** shows summary statistics pulled from the `/stats/summary` endpoint.

The frontend proxies `/api` requests to the FastAPI backend via Vite's dev server proxy. In production, both are served behind a reverse proxy (nginx or Vercel).

## Deployment

**Local development:** Docker Compose runs PostgreSQL with TimescaleDB. Backend and frontend run directly with hot-reload.

**Production:** EC2 instance (t3.small) for the backend + ingestion worker. RDS for PostgreSQL. Vercel for the React frontend. The ingestion worker and FastAPI server run as separate processes on the same EC2 instance.

**Scaling considerations:** The current architecture handles ZTF-volume alerts easily (100K-1M/night). For full LSST volume (10M/night), the Kafka consumer would need horizontal scaling (multiple consumer group instances) and the database would benefit from more aggressive TimescaleDB compression policies on historical data.
