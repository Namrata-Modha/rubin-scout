# Contributing to Rubin Scout

Thank you for your interest in contributing. Whether you're fixing a bug, adding a feature, improving documentation, or suggesting an idea, your help is welcome.

## Getting Started

1. Fork the repository and clone your fork
2. `cp .env.example .env`
3. `docker compose up -d db`
4. `cd backend && pip install -r requirements.txt`
5. `uvicorn app.main:app --reload`
6. In another terminal: `cd frontend && npm install && npm run dev`

Or on Windows, just run `start.bat`.

## Project Structure

```
backend/app/
  api/              REST endpoints (alerts, GW events, subscriptions)
  ingestion/        TNS CSV ingestion + ALeRCE enrichment and scheduling
  enrichment/       SIMBAD cross-matching, GW skymap cross-matching
  models/           SQLAlchemy ORM models (7 tables)
  notifications/    Slack, email, webhook delivery
  security.py       Rate limiting, security headers, admin key
  validation.py     Input validation, regex patterns, allowlists

frontend/src/
  components/       SkyMap, AlertTable, LightCurveChart, ClassBadge, StatsBar
  pages/            Dashboard, AlertDetail, GravitationalWaves
  lib/              API client (api.js), cosmos translations (cosmos.js)
```

## Development Workflow

1. Create a branch from `main` with a descriptive name (`feature/healpix-skymap`, `fix/simbad-columns`)
2. Make focused commits
3. Add or update tests for backend logic changes
4. Run linting: `cd backend && ruff check app/`
5. Open a pull request against `main`

## Areas Where Help Is Especially Welcome

**Backend (Python):**
- Full HEALPix skymap parsing for GW cross-matching (replace circular approximation)
- Additional broker integrations (Fink, Lasair, ANTARES)
- NED cross-matching (in addition to SIMBAD)
- Kafka consumer for real-time ALeRCE streaming
- Luminosity distance filtering for GW candidates
- Bayesian ranking for GW counterpart candidates

**Frontend (React):**
- Mobile-responsive layout
- Interactive sky map improvements (zoom, pan, click to filter)
- Light curve template overlay (compare against known SN models)
- GW skymap visualization (plot the probability contours on the sky map)
- Filter persistence (save filter state in URL or local storage)

**Science and Documentation:**
- More GW events from GWTC-4.0 with detailed descriptions
- Validation against known transient catalogs
- Jupyter notebooks demonstrating science use cases
- Tutorials for astronomers and curious non-experts
- Science validation: compare ALeRCE classifications to spectroscopic confirmations

**Infrastructure:**
- Monitoring and alerting (health check endpoints exist, need dashboarding)
- Load testing for high-volume ingestion
- CI/CD improvements (automated testing on PRs)
- Caching layer for telescope images to reduce Legacy Survey rate limiting

## Security

Rubin Scout takes security seriously. If you're making changes:

- All user inputs must be validated (see `validation.py` for patterns)
- New string parameters need length limits
- New endpoints need rate limiting via `@limiter.limit()`
- Write endpoints need `dependencies=[Depends(require_admin_key)]`
- Never log database URLs, API keys, or user emails
- Use parameterized queries only (SQLAlchemy ORM or `text()` with `:param`)
- Run `ruff check app/` before committing

## Code Style

**Python:** Use `ruff` for linting and formatting. Type hints encouraged. Target Python 3.11+.

**JavaScript/React:** Functional components with hooks. Tailwind CSS for styling. No external UI libraries beyond what's already included (Recharts, Lucide icons).

**Commits:** Clear summary line, blank line, then details if needed. No enforced format.

## Reporting Issues

Open a GitHub issue with a clear description and steps to reproduce. Label with `bug`, `feature`, `documentation`, or `question`.

## Code of Conduct

Be kind, be respectful, be collaborative. We're building tools for science, and everyone contributing deserves a welcoming environment regardless of background or experience.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.