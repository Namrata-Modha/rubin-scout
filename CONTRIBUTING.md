# Contributing to Rubin Scout

Thanks for your interest in contributing! Whether you're an astronomer who wants better tools, a developer who loves space, or both, there's a place for you here.

## Quick Start for Contributors

```bash
# Fork and clone the repo
git clone https://github.com/<your-username>/rubin-scout.git
cd rubin-scout

# Start the database
docker-compose up db -d

# Backend setup
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Initialize the database
psql -U rubinscout -d rubinscout -f sql/init.sql

# Run the backend
uvicorn app.main:app --reload

# Frontend setup (separate terminal)
cd frontend
npm install
npm run dev
```

## Types of Contributions

**Code contributions:** Bug fixes, new features, performance improvements, test coverage. Check the Issues tab for `good first issue` and `help wanted` labels.

**Astronomy expertise:** If you're an astronomer or astrophysics student, we need your help validating cross-matching logic, improving classification displays, and ensuring scientific accuracy. Open an issue describing what could be better.

**Documentation:** Improve setup guides, add API usage examples, write tutorials. The `docs/` folder and Jupyter notebooks in `notebooks/` are great starting points.

**Bug reports:** Found something broken? Open an issue with steps to reproduce, expected behavior, and actual behavior.

## Development Workflow

1. Create a branch from `main`: `git checkout -b feature/your-feature`
2. Make your changes
3. Run the linter: `cd backend && ruff check app/`
4. Run tests: `cd backend && python -m pytest tests/ -v`
5. Commit with a clear message: `git commit -m "Add cone search radius validation"`
6. Push and open a PR against `main`

## Code Style

**Python (backend):** We use `ruff` for linting. Run `ruff check app/` before committing. Follow PEP 8. Type hints are encouraged but not required. Docstrings on all public functions.

**JavaScript (frontend):** Standard React conventions. Functional components with hooks. Tailwind for styling. No CSS-in-JS.

**Commits:** Write clear, descriptive commit messages. One logical change per commit.

## Architecture Decisions

If you're proposing a significant change (new data source, database schema change, new dependency), please open an issue first to discuss the approach. This saves everyone time and helps maintain a coherent architecture.

## Astronomy-Specific Notes

- **Coordinates:** We use ICRS (J2000) for all RA/Dec values, stored in degrees.
- **Time:** Modified Julian Date (MJD) from ALeRCE, converted to UTC timestamps for storage.
- **Magnitudes:** AB magnitude system. Remember, lower magnitude = brighter.
- **Cross-matching:** Default radius is 5 arcseconds for SIMBAD, adjustable per query.
- **Data attribution:** Always cite the data sources (ALeRCE, ZTF, Rubin) in any public-facing outputs.

## Questions?

Open an issue or start a discussion on the repo. There are no dumb questions, especially about the astronomy side of things.
