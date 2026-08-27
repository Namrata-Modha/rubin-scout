# Supabase Free Tier Keep-Alive

To keep Supabase free tier active, set up a free cron job at [cron-job.org](https://cron-job.org) pointing to:

```
https://rubin-scout.vercel.app/api/health/ping
```

**Interval:** every 5 minutes.

This prevents the 30-second cold start on the free tier.

## What this endpoint does

`GET /api/health/ping` returns:

```json
{ "status": "ok", "timestamp": "2026-05-16T12:00:00+00:00" }
```

No authentication, no database query, no rate limit. It is purely a lightweight HTTP response to signal that the service is alive.

## Internal keep-alive

The backend scheduler also runs a `SELECT 1` query every 4 minutes internally to keep the SQLAlchemy connection pool warm, independently of external pings.
