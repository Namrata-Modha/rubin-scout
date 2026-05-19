# Rubin Scout

**Open-source multi-messenger astronomical alert platform.**

Rubin Scout aggregates transient detections from multiple sky surveys and gravitational wave catalogs into a single searchable database. It enriches each event with ML classifications, photometry, SIMBAD cross-matches, and observatory visibility windows, and exposes everything through a REST API and interactive dashboard.

**Live:** https://rubin-scout.vercel.app  
**API docs:** https://rubin-scout.vercel.app/api/docs  
**Version:** v0.1

---

## Data Sources

| Source | What it provides | Cadence |
|--------|-----------------|---------|
| [IAU Transient Name Server](https://www.wis-tns.org) | Spectroscopically classified transients (primary) | Daily CSV + API |
| [ALeRCE](https://alerce.science) | ZTF light curves, g/r band photometry, ML classifications | Per ingestion cycle |
| [GWOSC / GWTC](https://gwosc.org) | Full GWTC catalog of GW events (GPS→UTC, all versions) | Weekly refresh |
| [SIMBAD](https://simbad.u-strasbg.fr) | Catalog cross-matches within 5 arcsec | Per new object |
| [Legacy Survey](https://www.legacysurvey.org) | Optical cutout images | On demand |
| [CHIME/FRB](https://www.chime-frb.ca) | Fast Radio Burst catalog (integration ready) | Per ingestion cycle |

---

## Features

**Transient alert browser**  
Paginated dashboard filtered by classification (SNIa, SNII, SNIbc, SLSN, TDE, KN, AGN, Blazar, QSO, CV/Nova, FRB), time window, and ML confidence threshold. Each object shows a Legacy Survey cutout, ZTF light curve, per-band photometry (g/r/i), SIMBAD association, and classification probability breakdown.

**Gravitational wave counterpart search**  
All public LIGO/Virgo/KAGRA events from GWOSC are ingested automatically. For localized events the PostGIS `ST_DWithin` query finds optical transients within the 90% credible region detected within a configurable time window around the GW trigger.

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
GET /api/observatories          Observatory preset list
GET /api/gw/events              All GW events with candidate counts
GET /api/health/ping            Keep-alive / uptime check
```

Full Swagger UI at `/api/docs` (development) and the live API docs link above.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13, FastAPI, SQLAlchemy (async), APScheduler |
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
│   ├── ingestion/       TNS, ALeRCE, CHIME/FRB ingestors + scheduler
│   ├── enrichment/      SIMBAD cross-matching, GW counterpart search
│   ├── utils/           Observatory presets
│   ├── models/          SQLAlchemy ORM
│   └── validation.py    Input validation, OID patterns, classification allowlist
├── frontend/src/
│   ├── components/      SkyMap, LightCurveChart, VisibilityCard, AlertTable
│   ├── pages/           Dashboard, AlertDetail, GravitationalWaves
│   └── lib/             API client, astronomy utilities, class metadata
├── backend/alembic/     Database migrations
├── docs/                Architecture notes, science guide
└── KEEPALIVE.md         Supabase free-tier keep-alive setup
```

---

## Attribution

If you use Rubin Scout data in research, cite the upstream sources:

- ALeRCE: Förster et al. (2021), AJ, 161, 242
- ZTF: Bellm et al. (2019), PASP, 131, 018002
- GWTC events: per-event citations specified by the LVK collaboration at gwosc.org
- SIMBAD: Wenger et al. (2000), A&AS, 143, 9

---
<h5>Unlike primary brokers, Rubin Scout is a lightweight downstream aggregator - it consumes ALeRCE and TNS APIs rather than raw telescope streams, and embeds visibility planning directly alongside the alert data so researchers don’t need a separate TOM instance.</h5>

*Rubin Scout is an independent open-source project and is not affiliated with the Vera C. Rubin Observatory or the LSST project.*

---

## License

MIT. See [LICENSE](LICENSE).
