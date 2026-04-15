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

You should see checkmarks for TNS, ALeRCE, SIMBAD, and light curve fetch. No API keys needed for basic access.

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

Pull real transient data from TNS and enrich with ALeRCE, plus seed gravitational wave events:

```bash
python -m scripts.seed_database
```

This fetches:
- **TNS discoveries:** Real spectroscopic transient discoveries from recent days
- **ALeRCE enrichment:** ML classifications and light curves for objects without spectroscopic data
- **GW events:** 6 notable gravitational wave events from LIGO's catalogs with human descriptions

Takes 1-3 minutes depending on TNS availability.

## Step 5: Start the Backend

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs for the interactive Swagger UI. Try these endpoints:

- `GET /api/alerts/recent?hours=87600` -- all seeded alerts
- `GET /api/gw/events` -- gravitational wave events
- `GET /api/classifications` -- count of objects by type
- `GET /api/stats/summary?hours=87600` -- database statistics
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

Use `stop.bat` to shut everything down cleanly.

## Step 7: Explore the Data

**Try the filters:**
- Change time window (24h, 7d, 30d, All)
- Adjust confidence threshold (Min conf slider)
- Filter by classification type

**Click an alert card:**
- See full light curve if photometry is available
- View cross-match results from SIMBAD
- Check classification probabilities

**Test GW cross-matching:**
- Go to Gravitational Waves page
- Click on GW170817 (the famous neutron star merger)
- Click "Search for Optical Counterparts"
- See if any candidates appear in the time/position window

## Project Structure

```
backend/app/
  api/              Route handlers with rate limiting and input validation
  ingestion/        TNS CSV ingestion + ALeRCE polling (15-min intervals)
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
| `TNS_USER_ID` | (empty) | TNS user ID for CSV downloads (optional) |
| `TNS_USER_NAME` | (empty) | TNS username for CSV downloads (optional) |
| `TNS_BOT_ID` | (empty) | TNS bot ID for API access (optional) |
| `TNS_BOT_NAME` | (empty) | TNS bot name for API access (optional) |
| `TNS_API_KEY` | (empty) | TNS API key for bot access (optional) |
| `VITE_API_URL` | (empty) | Backend URL for frontend in production |

**TNS Credentials:** Optional for local development. The seed script works without them using publicly available TNS data. For production ingestion or API access, you'll need to register at wis-tns.org and create bot credentials.

## Common Issues

**Empty dashboard:** Default time filter is "All" (87600 hours = 10 years). If you still see nothing, run the seed script. Without data in PostgreSQL, the API returns empty results.

**"Connection refused" on frontend:** Make sure the backend is running on port 8000. Vite proxies `/api` requests there.

**TNS 404 errors in logs:** TNS daily CSV files are only available for recent days. If seeding historical data, some dates may not have published files. This is normal.

**SIMBAD timeout:** SIMBAD occasionally has downtime or rate limits. Cross-matching retries on the next ingestion cycle. Everything else works without it.

**Docker port conflict:** If port 5432 is in use, change it in `docker-compose.yml` and update `DATABASE_URL` in `.env`.

**Rate limit 429 errors:** The API allows 60 requests/minute for reads and 10/minute for writes. If you're scripting against the API, add a small delay between requests.

**ALeRCE light curves not appearing:** ALeRCE may not have light curve data for all TNS objects. This is expected - not all TNS discoveries are from ZTF, and not all ZTF detections have been processed by ALeRCE's classifiers.

## Deployment

For production deployment (Vercel + Render + Supabase), see [docs/architecture.md](architecture.md).

## Next Steps

**For developers:**
- Explore the API at http://localhost:8000/docs
- Read the architecture documentation
- Check CONTRIBUTING.md for development workflow

**For astronomers:**
- Read the science guide (docs/science-guide.md)
- Understand the classification taxonomy and confidence scores
- Learn about the GW cross-matching methodology and limitations

**For everyone:**
- Try filtering alerts by type and confidence
- Explore light curves for different transient classes
- Test the GW cross-matching on historical events