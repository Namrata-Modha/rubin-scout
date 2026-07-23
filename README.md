# Rubin Scout

**Open-source multi-messenger astronomical alert platform.**

Rubin Scout aggregates transient detections from multiple sky surveys and gravitational wave catalogs into a single searchable database. It enriches each event with ML classifications, photometry, SIMBAD cross-matches, and observatory visibility windows, and exposes everything through a REST API and interactive dashboard.

Rubin Scout is designed for students, citizen scientists, and researchers new to time-domain and multi-messenger astronomy who want a single environment for exploring transient alerts, gravitational wave events, and optical follow-up planning — without needing separate subscriptions to professional broker infrastructure.

**Live:** https://rubin-scout.vercel.app  
**API docs:** https://rubin-scout.vercel.app/api/docs  
**Version:** v0.1

---

## Data Sources

| Source | What it provides | Cadence |
|--------|-----------------|---------|
| [Fink broker (ZTF)](https://fink-portal.org) | Live ZTF transient alerts: SN candidates, kilonova candidates, SLSN candidates, early SN Ia — classified by ML pipelines | Daily at 10:00 UTC |
| [IAU Transient Name Server](https://www.wis-tns.org) | Spectroscopically classified transients | Daily CSV + API |
| [ALeRCE](https://alerce.science) | ZTF light curves, g/r band photometry, ML classifications | Per ingestion cycle |
| [GWOSC / GWTC](https://gwosc.org) | Full GWTC catalog of GW events (GPS→UTC, all versions) | Weekly refresh |
| [SIMBAD](https://simbad.u-strasbg.fr) | Catalog cross-matches within 5 arcsec | Per new object |
| [Legacy Survey](https://www.legacysurvey.org) | Optical cutout images | On demand |
| [CHIME/FRB](https://www.chime-frb.ca) | Fast Radio Burst catalog (Catalog 1, 536 FRBs, with positional uncertainties) | Monthly refresh + manual trigger (`POST /api/ingest/chime/trigger`) |

---

## Features

**Transient alert browser**  
Paginated dashboard filtered by classification (SNIa, SNII, SNIbc, SLSN, TDE, KN, AGN, Blazar, QSO, CV/Nova, FRB), time window, and ML confidence threshold. Each object shows a Legacy Survey cutout, ZTF light curve, per-band photometry (g/r/i), SIMBAD association, and classification probability breakdown.

**Immersive all-sky view**  
The dashboard includes a first-person 3D star field rendered with Three.js — the camera sits at the origin looking outward, surrounded by 40,000+ background stars with a tilted Milky Way band. Real astronomical events are overlaid as emoji markers positioned at their true RA/Dec coordinates: transients (💥🌟⚡✨🌀💎🕳️🔔🔥🔦), gravitational wave events (🌊), and fast radio bursts (📡). Each marker glows in its classification colour. Controls: drag to look around, scroll to fly forward/back, click any event to navigate to its detail page. The view auto-rotates slowly and pauses on interaction.

**Live alert ingestion**
Rubin Scout polls the [Fink broker](https://fink-portal.org) REST API daily at 10:00 UTC
and ingests the latest ZTF transient alerts across four classification classes. Each
alert is stored in the `alerts_live` table with its sky position, Julian date, Fink
classification label, classifier probability score, and full raw payload (large
light-curve feature blobs are stripped before storage to keep the database bounded).
Duplicate ingestion is prevented by a named unique constraint on `(source_id,
external_id)`; every insert uses `ON CONFLICT DO NOTHING` so reruns are safe. A
90-day retention window keeps the table under ~72 MB on Supabase free tier.

**Gravitational wave counterpart search**  
All public LIGO/Virgo/KAGRA events from GWTC (GWOSC) are ingested automatically, and the counterpart-search infrastructure — a PostGIS `ST_DWithin` query that finds optical transients within a skymap's credible region and persists them as candidates — is in place. However, sky-localization ingestion (parsing per-event skymaps into each event's centre and 90% area) is **not yet implemented**, so no ingested event currently carries localization. Until it lands, `POST /api/gw/events/{id}/crossmatch` returns **HTTP 422** (localization unavailable) rather than a spatially unfiltered candidate list. `GET /api/gw/events/{id}/candidates` reads any previously computed candidates.

**Observatory visibility planning**  
`GET /api/alerts/{oid}/visibility` computes an hourly altitude curve, astronomical dark window (sun < −18°), moon separation, and observable flag (target > 30° for ≥1 hr during dark time) for any observer location. Built-in presets:

| Observatory | Location |
|-------------|----------|
| Devasthal (ARIES/ILMT) | Nainital, India |
| DRAO Penticton | British Columbia, Canada |
| DAO Victoria | British Columbia, Canada |
| Mauna Kea | Hawaii, USA |
| Paranal (VLT) | Atacama Desert, Chile |
| La Palma (ORM) | Canary Islands, Spain |
| Palomar | California, USA |

**REST API**  
All data accessible via JSON endpoints. Key routes:

```
GET /api/alerts/recent          Paginated alert list with filters
GET /api/alerts/{oid}           Full object detail + light curve + probabilities
GET /api/alerts/{oid}/visibility Observatory visibility computation
GET /api/alerts/conesearch/query PostGIS cone search (ra, dec, radius_arcsec)
GET /api/ilmt/followup          ILMT follow-up planner (ZTF history, SIMBAD, GW coincidence, observatory visibility, recommendation)
GET /api/observatories          Observatory preset list
GET /api/gw/events              All GW events with candidate counts
POST /api/gw/events/{id}/crossmatch  Run GW-optical counterpart search
GET /api/gw/events/{id}/candidates   Read stored counterpart candidates
POST /api/ingest/chime/trigger  Manually trigger CHIME/FRB catalog ingestion (idempotent)
GET /api/health/ping            Keep-alive / uptime check
```

Full Swagger UI at `/api/docs` (development) and the live API docs link above.


# Running Tests

```bash
cd backend
pytest tests/ -v
```

## Run a specific test file

```bash
pytest tests/test_validation.py -v
```

## Run with coverage report

```bash
pytest tests/ --cov=app --cov-report=term-missing
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, FastAPI, SQLAlchemy (async), APScheduler |
| Database | PostgreSQL 17 + PostGIS (Supabase) |
| Frontend | React 18, Vite, Recharts, Tailwind CSS |
| Astronomy | astropy ≥ 6.1, astroquery, alerce client |
| Deployment | Vercel (frontend), Render (backend) |

---

## Quick Start

```bash
git clone https://github.com/Namrata-Modha/rubin-scout.git
cd rubin-scout
cp .env.example .env         # add TNS credentials and DATABASE_URL

# Backend
cd backend
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Dashboard at http://localhost:5173. API docs at http://localhost:8000/docs.

See [docs/getting-started.md](docs/getting-started.md) for environment variables and TNS credential setup.

---

## Project Structure

```
rubin-scout/
├── backend/app/
│   ├── api/             Route handlers (alerts, GW, images, subscriptions)
│   ├── ingestion/       TNS, ALeRCE, CHIME/FRB, Fink/ZTF ingestors + scheduler
│   ├── enrichment/      SIMBAD cross-matching, GW counterpart search
│   ├── utils/           Observatory presets
│   ├── models/          SQLAlchemy ORM
│   └── validation.py    Input validation, OID patterns, classification allowlist
├── frontend/src/
│   ├── components/      SkyMap, LightCurveChart, VisibilityCard, AlertTable
│   ├── pages/           Dashboard, AlertDetail, GravitationalWaves, FollowUpPlanner
│   └── lib/             API client, astronomy utilities, class metadata
├── backend/alembic/     Database migrations
├── docs/                Architecture notes, science guide
└── KEEPALIVE.md         Supabase free-tier keep-alive setup
```

---

## Contributing

Contributions are welcome — code, documentation, bug reports, and ideas alike.

- **How to contribute:** Fork the repo, create a branch off `main`, make focused commits with tests for backend logic, run `cd backend && ruff check app/` and `pytest tests/ -v`, then open a pull request against `main`. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development workflow and areas where help is especially welcome.
- **How to report an issue:** Open a [GitHub issue](https://github.com/Namrata-Modha/rubin-scout/issues) with a clear description and steps to reproduce, labelled `bug`, `feature`, `documentation`, or `question`.
- **How to get support:** Ask a question by opening a GitHub issue with the `question` label, or start a discussion on the repository. For anything security-sensitive, see the Security section in [CONTRIBUTING.md](CONTRIBUTING.md) rather than filing a public issue.

All participation is governed by our [Code of Conduct](CODE_OF_CONDUCT.md).

---

## Attribution

If you use Rubin Scout data in research, cite the upstream sources:

- ALeRCE: Förster et al. (2021), AJ, 161, 242
- Fink broker: Möller et al. (2021), MNRAS, 501, 3272
- ZTF: Bellm et al. (2019), PASP, 131, 018002
- GWTC events: per-event citations specified by the LVK collaboration at gwosc.org
- SIMBAD: Wenger et al. (2000), A&AS, 143, 9

---

Unlike primary brokers, Rubin Scout is a downstream aggregator — it ingests classified alert streams from Fink/ZTF and catalog feeds from TNS and ALeRCE, rather than processing raw telescope data. Observatory visibility planning is embedded directly alongside alert data so researchers do not need a separate Target Observation Manager instance.

*Rubin Scout is an independent open-source project and is not affiliated with the Vera C. Rubin Observatory or the LSST project.*

---

## License

MIT. See [LICENSE](LICENSE).
