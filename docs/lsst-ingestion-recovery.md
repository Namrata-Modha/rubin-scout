# LSST ingestion: bounded retries, cluster recovery, and stall visibility

This note documents the investigation behind three fixes to `backend/app/ingestion/lsst_service.py`'s pagination and retry design, plus the real evidence each one is based on. It's the reference for anyone revisiting `MAX_WINDOW_SPAN`, `CLUSTER_FETCH_SIZE`, `_fetch_cluster`, `check_stall`, or the still-open LSST scheduler interval decision. Verified 2026-08.

## The window-growth bug (MAX_WINDOW_SPAN)

`ingest()`'s cursor design intends a "partial" cycle to retry an identical window next time, at bounded cost. That was broken: `window_start_dt` correctly stayed pinned to the last true `"completed"` run, but `window_stop_dt` was unconditionally `datetime.now(timezone.utc)` on every cycle. So a stalled retry's window grew wider every single cycle instead of staying fixed — the opposite of bounded-cost retry. The same gap applies to the very first run (`DEFAULT_LOOKBACK_HOURS` back) and to resuming after any long dormancy, not only to partial-retry loops.

The fix: `window_stop_dt = min(now, window_start_dt + MAX_WINDOW_SPAN)`. A no-op when caught up; a hard per-cycle bound otherwise, applied uniformly to the scheduled job and to the manual trigger route (`POST /api/ingest/lsst/trigger`), which has no cadence of its own.

`MAX_WINDOW_SPAN` must stay strictly larger than whatever interval the LSST job runs on (enforced by an assertion in `scheduler.py`) — otherwise ordinary scheduler jitter would trip the cap on every healthy cycle, not just genuine catch-up.

### Real burst data behind the 30-minute value

Re-bucketing a real `most_likely_sn` pull for the busiest known night (2026-07-14) at 15-minute resolution found the worst single window carried **8,342 alerts — 83% of the 10,000/tag/cycle cap** (`MAX_PAGES_PER_TAG=20` x `PAGE_SIZE=500`). An hourly average across the same night looked far safer (~4,323/cycle) and would have hidden this. `MAX_WINDOW_SPAN` is set to 30 minutes — double the widest window this burst data actually covers — to keep steady-state cycles clear of the cap while still bounding catch-up cost. This value is provisional, tied to the scheduler interval decision below.

## The same-timestamp cluster problem (_fetch_cluster)

A single LSST visit can produce many alerts sharing one exact `r:midpointMjdTai`, and there's no finer-grained, unique, monotonic secondary sort key available from the API. `_fetch_tag_window` detects the case where an entire full page shares one timestamp (a strong signal that instant's true cluster size exceeds `PAGE_SIZE`).

Originally this just gave up (marked the tag not-exhausted, logged an error). That's an incomplete fix on its own: retrying the *identical* window deterministically re-triggers the same cluster every time — not a temporary partial, a permanent stop for that tag (and, before `MAX_WINDOW_SPAN`, for the other four tags too, since they share one cursor).

The fix is `_fetch_cluster`: a targeted follow-up request for exactly that one timestamp, using a narrow bracket and a much higher fetch size (`CLUSTER_FETCH_SIZE=5000`) than normal paging (`PAGE_SIZE=500`). If that recovers the full cluster uncapped, pagination continues past it instead of stalling. Only if even that generous fetch is itself capped does the tag fall back to the original not-exhausted behavior.

### Verified, not assumed: cluster size and bracket width

- **Confirmed live**: a real cluster of **2,288** `most_likely_sn` alerts at one exact timestamp (2026-07-14). `CLUSTER_FETCH_SIZE=5000` gives ~2.2x headroom over this.
- **Bracket width (+/-2s)**: verified against real gap data, not assumed safe. Minimum gaps between distinct visit timestamps were checked across 2 tags (`most_likely_sn`, `in_tns`) and up to 4 nights (20260707/09/13/14) and were consistently **~37 seconds, never under 5s** — roughly 9x margin below the smallest gap ever observed. This reads as LSST's underlying visit cadence (exposure + readout + slew) rather than a coincidence specific to the one cluster first found, since every tag samples from the same stream of visits.
- Even if a future gap were narrower than expected, `_fetch_cluster` filters its bracket response down to the exact target timestamp, so a contaminated bracket can only produce a false "still capped" (safe fallback), never silent data loss.

## Stall visibility (check_stall / GET /api/ingest/lsst/status)

A cluster larger than `CLUSTER_FETCH_SIZE` — or any other cause of a sustained stall — would otherwise only be visible in log lines. `check_stall()` reports when the last `STALL_THRESHOLD_CYCLES` runs are all `"partial"` with an identical `window_start`. This check is only meaningful *because* `MAX_WINDOW_SPAN` makes that window genuinely stable across repeated failures; before that fix, "identical window" could never occur, so an equivalent check would never have fired even on a real stall.

**First design, rejected**: surface it through the existing `GET /api/health/ping` (reasoning: the deployed UptimeRobot monitor already polls that exact URL, and a new endpoint needs external dashboard configuration this codebase can't make happen). Two problems killed this:

1. A body-only signal (status always 200, stall state only in JSON) would only make the data available for manual inspection, not actually alert anyone — it doesn't meet "capable of noticing this failure mode."
2. Making `/ping` return 503 on a stall to fix (1) creates a worse problem: `render.yaml` doesn't set `healthCheckPath` explicitly, but other settings (`DATABASE_URL`, `CORS_ORIGINS`) are marked `sync: false`, meaning they're managed in the Render dashboard, outside this repo — so whether Render's own liveness/restart check is pointed at `/api/health/ping` can't be confirmed from the codebase alone. A stuck LSST ingestion cursor is not something restarting the process fixes; if Render's restart logic is wired to this path, this design would cause pointless restarts on every stall.

**Final design**: `GET /api/health/ping` stays exactly as it always was — pure liveness, always 200 if the process can respond, no DB query, no LSST awareness at all. The stall signal instead lives on its own path, `GET /api/ingest/lsst/status` (no admin key required — read-only, not sensitive), which returns **503 when genuinely stalled, 200 otherwise**. UptimeRobot can point a *second*, separate monitor at this path if genuine alerting is wanted, without any risk to whatever's watching `/ping` for liveness. This also means which UptimeRobot monitor mode (status vs. keyword) is configured no longer matters for `/ping` at all — only the new endpoint's own monitor, if one is ever added, needs to care.

## The scheduler interval decision (still open)

Per-tag volume, gathered live against the two highest-volume nights on record (`20260713`: 744,559 total alerts; `20260714`: 473,344 total alerts):

| tag | count (one night) |
|---|---|
| `most_likely_sn` | 41,207 (uncapped; needed `n=60000`) |
| `sn_near_galaxy_candidate` | 0 on two nights, 2,000+ (capped) on a third |
| `extragalactic_new_candidate` | 1,205 |
| `in_tns` | 871 |
| `extragalactic_lt20mag_candidate` | 65 |

`most_likely_sn` dominates: ~5,574 alerts/hour average across a ~7.4-hour observing session, peaking at 17,293 in the single busiest hour and 8,342 in the single busiest 15-minute window (see above). A daily or hourly cadence would routinely exceed the per-cycle cap on busy nights; a 15-minute provisional interval (`scheduler.py`'s `LSST_INGESTION_INTERVAL_SECONDS`) keeps the worst known case at 83% of the cap. This is provisional pending final confirmation, and `MAX_WINDOW_SPAN` must be revisited together with it if it changes.

As of this writing, Fink's LSST deployment has recorded no night past 2026-07-14 (confirmed live via `GET /api/v1/statistics` on 2026-08-04, three weeks later) — Rubin Observatory's summit was evacuated 2026-07-14 due to a record-breaking storm and had not resumed per the 2026-07-24 community status update either. This is a real, documented, presumably-temporary closure, not a Fink/API failure.
