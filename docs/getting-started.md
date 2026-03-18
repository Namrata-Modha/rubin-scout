# Getting Started

This guide gets you from zero to a running Rubin Scout instance with real astronomical data in about 15 minutes.

## Prerequisites

- Python 3.11+
- Node.js 18+
- Docker and Docker Compose (for PostgreSQL)
- Git

## Step 1: Clone and Configure

```bash
git clone https://github.com/namratam-dev/rubin-scout.git
cd rubin-scout
cp .env.example .env
```

No changes needed in `.env` for local development. The defaults work out of the box.

## Step 2: Verify Your Connection

Before touching Docker or databases, confirm you can reach ALeRCE:

```bash
cd backend
pip install -r requirements.txt
cd ..
python -m scripts.verify_connection
```

You should see checkmarks for ALeRCE, light curve fetch, and SIMBAD. If anything fails, check your internet connection. No API keys are needed.

## Step 3: Start the Database

```bash
docker-compose up -d db
```

This starts PostgreSQL 16 with TimescaleDB and PostGIS extensions. The `init.sql` script runs automatically and creates all tables.

Verify it's running:
```bash
docker-compose logs db | tail -5
```

## Step 4: Seed the Database

Pull a week of real supernova and AGN data from ALeRCE:

```bash
cd backend
python -m scripts.seed_database
```

This takes 1-3 minutes depending on how much data ALeRCE has from the past week. You'll see progress logs for each class of transient.

## Step 5: Start the Backend

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs in your browser. You should see the Swagger UI with all endpoints. Try clicking "GET /api/alerts/recent" and hitting "Execute" to see real alert data.

## Step 6: Start the Frontend

In a new terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. You should see the Rubin Scout dashboard with a filterable table of transient alerts, stats bar, and classification filters.

Click any object ID to see its full light curve and classification probabilities.

## Step 7: Start the Ingestion Worker (Optional)

To keep pulling new alerts automatically:

```bash
cd backend
python -m app.ingestion.scheduler
```

This polls ALeRCE every 15 minutes (configurable in `.env`) and adds new objects to your database.

## Project Structure

```
rubin-scout/
├── backend/              Python FastAPI backend
│   ├── app/
│   │   ├── api/          REST endpoint handlers
│   │   ├── ingestion/    ALeRCE data pulling
│   │   ├── enrichment/   SIMBAD cross-matching
│   │   ├── models/       SQLAlchemy ORM models
│   │   └── notifications/  Slack/email alerts
│   ├── tests/            Pytest test suite
│   └── sql/              Database schema
├── frontend/             React + Vite dashboard
│   └── src/
│       ├── components/   Reusable UI components
│       ├── pages/        Dashboard and detail views
│       └── lib/          API client utilities
├── notebooks/            Jupyter exploration notebooks
├── scripts/              CLI utilities
└── docs/                 Documentation
```

## Common Issues

**"Connection refused" on the frontend:** Make sure the backend is running on port 8000. The Vite dev server proxies `/api` requests to `localhost:8000`.

**Empty dashboard:** Run the seed script first. Without data in PostgreSQL, the API returns empty results.

**SIMBAD timeout:** SIMBAD occasionally has downtime. Cross-matching will retry on the next ingestion cycle. The rest of the pipeline works without it.

**Docker port conflict:** If port 5432 is already in use, change it in `docker-compose.yml` and update `DATABASE_URL` in `.env`.
