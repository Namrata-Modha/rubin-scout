# Getting Started

Get Rubin Scout running locally with real astronomical data in about 15 minutes.

## Prerequisites

- Python 3.11+ (tested on 3.13)
- Node.js 18+
- Docker Desktop (for PostgreSQL)
- Git

## Step 1: Clone and Configure

```bash
git clone https://github.com/Namrata-Modha/rubin-scout.git
cd rubin-scout
cp .env.example .env
```

No changes needed in `.env` for local development. The defaults connect to a local Docker PostgreSQL instance and run in development mode (no API key required for write endpoints).

## Step 2: Verify Your Connection

Confirm you can reach the upstream data sources before touching Docker or databases:

```bash
cd backend
pip install -r requirements.txt
cd ..
python -m scripts.verify_connection
```

You should see checkmarks for ALeRCE, SIMBAD, and the light curve fetch. No API keys needed.

## Step 3: Start the Database

```bash
docker compose up -d db
```

This starts PostgreSQL 17 with PostGIS. The `init.sql` script creates all tables, indexes, and helper functions automatically.

Verify it's running:

```bash
docker compose logs db | tail -5
# Look for "database system is ready to accept connections"
```

## Step 4: Seed the Database

Pull real transient data from ALeRCE and seed gravitational wave events:

```bash
python -m scripts.seed_database
```

This fetches ~75 real objects (supernovae, AGN) with full light curves, plus 6 notable gravitational wave events from LIGO's catalogs. Takes 1-3 minutes.

## Step 5: Start the Backend

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs for the interactive Swagger UI. Try these endpoints:

- `GET /api/alerts/recent?hours=87600` -- all seeded alerts
- `GET /api/gw/events` -- gravitational wave events
- `GET /api/classifications` -- count of objects by type
- `POST /api/gw/events/GW231123/crossmatch` -- find optical counterparts to a GW event

## Step 6: Start the Frontend

In a new terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. You should see the dashboard with event cards, sky map, and filters.

Navigate to http://localhost:5173/gravitational-waves to explore GW events and run cross-matching.

## Windows Shortcut

Instead of running three terminals manually, just double-click `start.bat` in the project root. It starts Docker, the backend, and the frontend, then opens your browser.

Use `stop.bat` to shut everything down.

## Step 7: Seed More Data (Optional)

The default seed pulls 25 objects per class. To get more data, run the diagnostic script to check what's available:

```bash
python scripts\diagnose_alerce.py
```

## Project Structure

```
backend/app/
  api/              Route handlers with rate limiting and input validation
  ingestion/        ALeRCE polling (15-min intervals) and Kafka consumer (planned)
  enrichment/       SIMBAD cross-matching, GW skymap cross-matching
  models/           7 SQLAlchemy ORM models
  notifications/    Slack webhooks, email digests, generic webhooks
  security.py       Rate limiter, security headers, admin API key
  validation.py     Input schemas, regex patterns, allowlists

frontend/src/
  components/       SkyMap (Mollweide projection), AlertTable (card grid),
                    LightCurveChart (Recharts), ClassBadge, StatsBar
  pages/            Dashboard, AlertDetail, GravitationalWaves
  lib/              API client (api.js), cosmos translation layer (cosmos.js)
```

## Environment Variables

Key settings in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | local Docker | PostgreSQL connection string |
| `APP_ENV` | development | Set to `production` for deployed version |
| `CORS_ORIGINS` | localhost | Comma-separated allowed origins |
| `ADMIN_API_KEY` | (empty) | Required in production for write endpoints |
| `VITE_API_URL` | (empty) | Backend URL for frontend in production |

## Common Issues

**Empty dashboard:** Default time filter is "All" (87600 hours). If you still see nothing, run the seed script. Without data in PostgreSQL, the API returns empty results.

**"Connection refused" on frontend:** Make sure the backend is running on port 8000. Vite proxies `/api` requests there.

**SIMBAD timeout:** SIMBAD occasionally has downtime. Cross-matching retries on the next ingestion cycle. Everything else works without it.

**Docker port conflict:** If port 5432 is in use, change it in `docker-compose.yml` and update `DATABASE_URL` in `.env`.

**Rate limit 429 errors:** The API allows 60 requests/minute for reads and 10/minute for writes. If you're scripting against the API, add a small delay between requests.

## Deployment

For production deployment (Vercel + Render + Supabase), see [docs/architecture.md](architecture.md).
