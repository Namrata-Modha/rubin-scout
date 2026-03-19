# Rubin Scout

**Filtered, enriched transient alerts from the Vera C. Rubin Observatory and ZTF.**

Rubin Scout is a downstream alert processing tool that connects to astronomical alert brokers (ALeRCE, Pitt-Google), filters for specific transient event classes (supernovae, tidal disruption events, kilonovae), enriches them with cross-catalog data, and serves them through a real-time dashboard and notification system.

## Why This Exists

The Rubin Observatory produces up to 7 million alerts per night. Community brokers classify and distribute these alerts, but astronomers managing follow-up observations need filtered, enriched, prioritized events tailored to their science cases. Rubin Scout sits between the brokers and the telescope operators who decide what to observe next.

## Features

- **Real-time ingestion** from ALeRCE (REST API + Kafka) and Pitt-Google (GCP Pub/Sub)
- **Cross-matching** against SIMBAD, NED, and the Transient Name Server
- **Multi-messenger support** — cross-match optical transients with LIGO/Virgo gravitational wave skymaps
- **Notification system** — Slack webhooks, email digests, and generic webhooks with customizable filters
- **Interactive dashboard** — sky map, light curve viewer, event table, nightly highlights
- **REST API** with cone search, time-range queries, and classification filters

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+, FastAPI, SQLAlchemy |
| Database | PostgreSQL + TimescaleDB + PostGIS |
| Frontend | React, Recharts, Aladin Lite |
| Streaming | Apache Kafka (ALeRCE), GCP Pub/Sub (Pitt-Google) |
| Deployment | Docker Compose (local), AWS EC2 + RDS (prod) |

## Quick Start

```bash
# Clone the repo
git clone https://github.com/Namrata-Modha/rubin-scout.git
cd rubin-scout

# Copy environment template
cp .env.example .env

# Start the stack (PostgreSQL + backend)
docker-compose up -d

# Run database migrations
cd backend
pip install -r requirements.txt
alembic upgrade head

# Verify ALeRCE connection
python -m scripts.verify_connection

# Start the backend
uvicorn app.main:app --reload --port 8000

# In another terminal, start the frontend
cd frontend
npm install
npm run dev
```

API docs will be available at `http://localhost:8000/docs`

## Architecture

```
ALeRCE API/Kafka ──┐
                    ├──▶ Ingestion Workers ──▶ PostgreSQL ──▶ FastAPI ──▶ React Dashboard
Pitt-Google Pub/Sub ┤                              │                        │
                    │                         Enrichment              WebSocket
GCN/GWOSC ─────────┘                        (SIMBAD, NED)          (live alerts)
                                                  │
                                            Notifications
                                         (Slack, Email, Webhook)
```

## Data Sources

- [ALeRCE Broker](https://alerce.science) — ML-classified alerts from ZTF and Rubin/LSST
- [Pitt-Google Broker](https://pitt-broker.readthedocs.io) — Cloud-native alert distribution
- [GWOSC](https://gwosc.org) — Gravitational wave open science data
- [SIMBAD](https://simbad.u-strasbg.fr) / [NED](https://ned.ipac.caltech.edu) — Astronomical object catalogs

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License. See [LICENSE](LICENSE) for details.

## Acknowledgments

This project uses data from the Vera C. Rubin Observatory, the Zwicky Transient Facility, and the LIGO/Virgo/KAGRA collaboration. We acknowledge the ALeRCE broker team and the Pitt-Google broker team for providing open access to classified alert streams.
