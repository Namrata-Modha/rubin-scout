# Rubin Scout

**The cosmos for curious humans.**

Rubin Scout makes real astronomical discoveries accessible. It pulls live data from telescope alert systems, translates the raw science into plain language, and lets you explore exploding stars, feeding black holes, and neutron star collisions through an interactive dashboard.

It also does something no other downstream tool does: **gravitational wave cross-matching**. When LIGO detects spacetime ripples from a cosmic collision, Rubin Scout searches the optical sky for the flash of light from the same event.

## What You Can Do

**Browse cosmic events.** The dashboard shows real transient detections from the Zwicky Transient Facility and the Vera C. Rubin Observatory, classified by ALeRCE's machine learning pipeline. Each event is translated into human language with descriptions, constellation locations, and confidence scores.

**Explore gravitational wave events.** Six real LIGO/Virgo detections (GW170817, GW190521, and more) with descriptions of what happened, how far away it was, and a button to search for optical counterparts in the alert database.

**Filter and subscribe.** Filter by event type, confidence level, and time window. Set up notification subscriptions to get alerts via Slack, email, or webhooks when new events match your criteria.

**Query the API.** Every feature is available through a REST API with full Swagger documentation, cone search (spatial queries), and pagination.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13, FastAPI, SQLAlchemy, slowapi |
| Database | PostgreSQL 17 (Supabase) with PostGIS |
| Frontend | React 18, Vite, Recharts, Tailwind CSS |
| Deployment | Vercel (frontend), Render (backend), Supabase (database) |
| Data Sources | ALeRCE, LIGO/GraceDB, SIMBAD, GWTC catalogs |

## Security

- Rate limiting on all endpoints (60/min reads, 10/min writes) via slowapi
- OWASP security headers on every response
- Admin API key required for write endpoints in production
- Strict input validation with Pydantic models and regex patterns
- Classification and filter allowlists (no arbitrary input passes through)
- Email masking in list views
- Request body size limit (1 MB)
- CORS restricted to configured origins
- Swagger docs disabled in production
- Row Level Security enabled on Supabase tables with user data

## Quick Start

```bash
git clone https://github.com/Namrata-Modha/rubin-scout.git
cd rubin-scout

# Windows: double-click start.bat
# Or manually:
cp .env.example .env
docker compose up -d db

cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload

# In another terminal:
cd frontend
npm install
npm run dev
```

Dashboard at http://localhost:5173. API docs at http://localhost:8000/docs.

See [docs/getting-started.md](docs/getting-started.md) for detailed setup instructions.

## Project Structure

```
rubin-scout/
├── backend/                 Python FastAPI backend
│   ├── app/
│   │   ├── api/             Route handlers (alerts, GW, subscriptions)
│   │   ├── ingestion/       ALeRCE data pulling and scheduling
│   │   ├── enrichment/      SIMBAD cross-matching, GW cross-matching
│   │   ├── models/          SQLAlchemy ORM models
│   │   ├── notifications/   Slack, email, webhook delivery
│   │   ├── security.py      Rate limiting, headers, admin key
│   │   └── validation.py    Input validation, allowlists, patterns
│   ├── tests/
│   └── sql/                 Database schema
├── frontend/                React + Vite dashboard
│   └── src/
│       ├── components/      SkyMap, AlertTable, LightCurveChart, etc.
│       ├── pages/           Dashboard, AlertDetail, GravitationalWaves
│       └── lib/             API client, cosmos translation layer
├── notebooks/               Jupyter exploration notebooks
├── scripts/                 CLI utilities (seed, verify, diagnose)
├── docs/                    Architecture, science guide, getting started
├── start.bat / stop.bat     Windows one-click start/stop
└── render.yaml              Render deployment config
```

## Data Sources and Attribution

- [ALeRCE Broker](https://alerce.science) for ML-classified transient alerts. Cite: Forster et al. (2021), AJ, 161, 242
- [Zwicky Transient Facility](https://www.ztf.caltech.edu) for optical survey data. Cite: Bellm et al. (2019), PASP, 131, 018002
- [LIGO/Virgo/KAGRA](https://gwosc.org) for gravitational wave data. Cite per event as specified by LVK
- [SIMBAD](https://simbad.u-strasbg.fr) for astronomical object cross-matching
- [Astropy](https://www.astropy.org) for coordinate transforms and time conversions

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License. See [LICENSE](LICENSE) for details.
