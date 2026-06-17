# Rubin Scout RAG Knowledge Base

Vector knowledge base for Rubin Scout, backed by PGVector on Supabase.

## Setup

1. **Enable the pgvector extension** (one-time, already done via Alembic):
   ```
   cd backend && alembic upgrade head
   ```

2. **Install RAG dependencies**:
   ```
   pip install -r requirements.txt
   ```

3. **Set env vars**:
   ```
   export DATABASE_URL=postgresql+psycopg://user:pass@host/db
   export GEMINI_API_KEY=your_key
   ```
   For Supabase: use the **Transaction pooler** connection string (port 6543) and replace the scheme with `postgresql+psycopg://`.

## Building / Rebuilding

```bash
cd backend
python -m rag.build_knowledge_base
```

This is **idempotent** — it drops and recreates the collection on every run. Re-run whenever source docs or `cosmos.js` CLASS_INFO changes.

## Sources

| File | Content |
|------|---------|
| `docs/science-guide.md` | Transient science background |
| `docs/architecture.md` | System architecture |
| `frontend/src/lib/cosmos.js` | CLASS_INFO transient catalogue (one doc per class) |

## Collection

- Name: `rubin_scout_kb`
- Model: `gemini-embedding-001`
- Dimensions: 768 (L2-normalised)
- Storage: `langchain_pg_embedding` / `langchain_pg_collection` tables (JSONB metadata)
