"""
Fink/LSST (Rubin) Live Alert Ingestion Service.

Rubin/LSST alerts are served by a **separate** Fink deployment from the ZTF
one this codebase already ingests (see fink_service.py) — a different
domain, a different endpoint structure, and a materially different alert
schema. This module is deliberately its own service rather than a branch
inside FinkIngestionService, because the two are not a drop-in match:

  Endpoint
    ZTF:  POST https://api.ztf.fink-portal.org/api/v1/latests
          (fixed `class` + `n`, no real date window used by this codebase)
    LSST: GET  https://api.lsst.fink-portal.org/api/v1/tags
          (a genuinely different discovery mechanism — see below — with
          real `startdate`/`stopdate` parameters)

  Classification
    ZTF exposes a single `v:classification` string per alert (SN candidate,
    Kilonova candidate, ...). LSST has no equivalent single-label field.
    Discovery instead happens via "tags" — named, curated alert-selection
    filters — queried with `?tag=<name>`. As of this writing, GET
    /api/v1/tags (no params) lists nine defined tags total, of which SIX
    report "API support": true (genuinely queryable) and three report false
    (documented but not yet queryable) — confirmed live:

      API-supported (6):
        extragalactic_lt20mag_candidate  "rising, bright (mag < 20), and
                                          extragalactic candidates"
        extragalactic_new_candidate      "new (< 5 days first apparition),
                                          bright (mag < 24), potentially
                                          extragalactic with a fading or
                                          rising rate passing the cuts"
        hostless_candidate               "hostless according to ELEPHANT"
        in_tns                           "known counterpart in TNS (AT or
                                          confirmed) at the time of emission
                                          by Rubin"
        most_likely_sn                   "likely to be SN, based on
                                          SuperNNova and CATS classifiers"
        sn_near_galaxy_candidate         "matching in catalogs to a galaxy
                                          and properties consistent with SNe"
      NOT API-supported (3, excluded — no live query path exists):
        extragalactic_svom, remove_unlikely_transients, uniform_sample

    LSST_TAGS below uses FIVE of the six supported tags:
      most_likely_sn, sn_near_galaxy_candidate, in_tns,
      extragalactic_new_candidate, extragalactic_lt20mag_candidate.
    in_tns is included deliberately: TNS cross-matching is already the
    signal this codebase treats as central everywhere else (it drives the
    Dashboard's primary discovery feed via tns_service.py), so excluding it
    here would be inconsistent with no real justification.
    extragalactic_new_candidate and extragalactic_lt20mag_candidate are
    included as broad, general "is this a new/bright extragalactic
    transient" filters — directly analogous in breadth to ZTF's "SN
    candidate" class, and (for the lt20mag tag specifically) a natural fit
    for a follow-up-planning platform, since bright targets are the most
    tractable for spectroscopic follow-up.

    hostless_candidate is the one supported tag deliberately EXCLUDED. Its
    own description selects for the ABSENCE of a cross-matched host galaxy
    (per the ELEPHANT algorithm), whereas host-context is used elsewhere in
    this platform as a positive quality signal, not a selection criterion on
    its own (see sn_near_galaxy_candidate above, and Object.host_galaxy_name/
    host_galaxy_redshift). A "no known host" filter sits outside that
    established pattern and, without additional vetting infrastructure this
    pass does not add, risks surfacing a higher share of image artifacts or
    simply under-catalogued fields as if they were noteworthy hostless
    transients. This can be revisited if there's a specific use case for it.

    There is currently **no Kilonova- or SLSN-equivalent tag** on the LSST
    side under any of the nine — a real, current gap versus ZTF's four
    classes, not an oversight.

  Fields (verified against a real, live LSST alert sample — 135 fields)
    i:objectId          -> r:diaObjectId       (renamed, same concept)
    i:candid             -> r:diaSourceId        (renamed, same concept —
                                                   the natural external_id)
    i:ra / i:dec          -> r:ra / r:dec          (renamed prefix only)
    i:jd                 -> MISSING. Only r:midpointMjdTai (MJD) exists;
                             JD is derived via the exact MJD+2400000.5
                             identity, not approximated.
    v:lastdate            -> MISSING. Derived from r:midpointMjdTai instead.
    i:magpsf / i:sigmapsf -> MISSING. LSST alerts carry FLUX photometry
                             (r:psfFlux, r:scienceFlux, r:apFlux, ...), not
                             magnitudes — a representation change, not a
                             rename. Not converted here (no LSST zeropoint
                             was found in the payload); flux fields are
                             preserved as-is in raw_payload only.
    i:drb                 -> MISSING. Closest analogue is r:reliability
                             (+ r:reliabilityVersion), a different
                             algorithm/scale, not a renamed equivalent.
    v:classification      -> MISSING. Replaced by f:clf_cats_class (numeric)
                             + f:clf_cats_score, from Fink's newer "CATS"
                             classifier — a different classification system,
                             not a renamed field.
    d:snn_sn_vs_all       -> f:clf_snnSnVsOthers_score (same underlying
                             SuperNNova model, renamed field)
    d:snn_snia_vs_nonia   -> MISSING (not present in the sampled payload)
    d:rf_kn_vs_nonkn      -> MISSING (no kilonova-specific score exists)
    d:slsn_score          -> MISSING (no SLSN-specific score exists)
    d:cdsxmatch           -> replaced by a much richer, differently-shaped
                             set of per-catalog xm:* fields (Gaia DR3, Legacy
                             Survey DR8, Mangrove, SIMBAD, SPICY, TNS, VSX,
                             3HSP, 4LAC) — not parsed individually here;
                             preserved in raw_payload.

  Volume (confirmed via GET /api/v1/statistics against real, live data)
    132 real nights on record, from 2025-10-25 (commissioning, 414 alerts)
    through 2026-07-14 (744,559 alerts in one night, the night before a
    documented weather-emergency summit evacuation — see below). Nightly
    volume during steady-state full-survey operation (since 2026-06-29)
    ranges roughly 19,000-745,000 alerts/night — 100-2000x a typical ZTF
    night. A fixed `n=100` (or even n=2000) single-shot fetch, the approach
    fink_service.py uses for ZTF, would silently drop the overwhelming
    majority of a single LSST night. This service instead pages through a
    real date window (see _fetch_tag_window), advancing a cursor from the
    last successful run rather than fetching a fixed count.

  Pagination ordering (confirmed empirically, not assumed — see below)
    The API returns alerts in DESCENDING order by r:midpointMjdTai (newest
    first). Confirmed live: a 500-record fetch for most_likely_sn over a
    real 2026-07-14 window showed the response's distinct timestamps were
    monotonically decreasing across its full length, and the very first
    record's timestamp matched the query's stopdate side while the last
    matched its startdate side. Pagination therefore walks BACKWARD:
    `startdate` stays fixed at the true window start; `stopdate` shrinks to
    the oldest timestamp seen in each page, so each subsequent page asks for
    "everything even older than what's already been collected."

    A single visit can also produce many alerts sharing one identical
    midpointMjdTai, with no finer secondary sort key available to split a
    cluster safely across a page boundary. A full page sharing one
    timestamp triggers a targeted recovery fetch (_fetch_cluster) rather
    than an immediate give-up; only if that also can't confirm the
    cluster's true size does the tag fall back to NOT exhausted. GET with
    query-string parameters was confirmed to return actual alert records
    (not the tag-name/description catalog a bare, param-less GET returns)
    — the calling convention itself is correct. Full investigation and
    real numbers: docs/lsst-ingestion-recovery.md.

  Bounded retries, cluster recovery, and stall visibility
    Three related fixes, verified against real Fink LSST data: (1)
    MAX_WINDOW_SPAN bounds how much time a single cycle's window can span,
    fixing a bug where a stalled retry's window grew wider every cycle
    instead of staying identical; (2) _fetch_cluster recovers a
    same-timestamp cluster the normal page size couldn't confirm was
    drained, instead of stalling on it forever; (3) check_stall() surfaces
    a genuine stall through GET /api/ingest/lsst/status rather than only
    log lines -- kept separate from /api/health/ping's pure process
    liveness, since a stalled ingestion cursor is not a reason to restart
    the process. Full investigation, real cluster/volume numbers, and the
    still-open scheduler interval decision: docs/lsst-ingestion-recovery.md.

  Current operational status (as of this writing)
    The most recent night with any recorded alerts is still 2026-07-14,
    confirmed via a live statistics check on 2026-08-04 — the observatory
    has not resumed since the storm evacuation documented in
    docs/lsst-ingestion-recovery.md. Not a Fink/API failure; a live run
    while the summit is closed correctly finds zero new alerts.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from astropy.time import Time
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AlertLive, AlertSource, IngestionLog

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #

LSST_API_URL = "https://api.lsst.fink-portal.org/api/v1/tags"

# Five of the six API-supported tags (confirmed live via GET /api/v1/tags) —
# see module docstring's "Classification" section for the full nine-tag
# breakdown and the specific reasoning for including in_tns,
# extragalactic_new_candidate, and extragalactic_lt20mag_candidate, and for
# excluding hostless_candidate despite it also being API-supported. No
# Kilonova- or SLSN-equivalent tag currently exists under any of the nine.
LSST_TAGS = [
    "most_likely_sn",
    "sn_near_galaxy_candidate",
    "in_tns",
    "extragalactic_new_candidate",
    "extragalactic_lt20mag_candidate",
]

# Per-page fetch size within a date window. Deliberately NOT the sole bound
# on how much a cycle can ingest — see _fetch_tag_window, which pages past
# this repeatedly within the window rather than truncating at one page.
PAGE_SIZE = 500

# Safety bound on pages fetched per tag per cycle (10,000 alerts/tag/cycle
# at PAGE_SIZE=500). If hit, the run logs a warning and does NOT advance its
# ingestion cursor past the point actually reached -- the next cycle resumes
# from there rather than silently skipping the unfetched remainder. This is
# a real possibility (not just theoretical) given the confirmed volume above,
# e.g. catching up after an extended gap.
MAX_PAGES_PER_TAG = 20

# On the very first run (no prior completed IngestionLog row for this
# source), start the window this far back rather than attempting "all of
# history".
DEFAULT_LOOKBACK_HOURS = 24

# Upper bound on how much wall-clock time a single cycle's window can span
# -- fixes a bug where a stalled "partial" cycle's window grew wider every
# retry instead of staying bounded (window_stop_dt was always "now"). Also
# protects the manual trigger route, which has no cadence of its own. MUST
# stay strictly smaller than the LSST job's interval (see scheduler.py's
# assertion) or normal jitter would trip this on every healthy cycle.
# Provisional value tied to the still-open interval decision. Full
# investigation and real burst numbers: docs/lsst-ingestion-recovery.md.
MAX_WINDOW_SPAN = timedelta(minutes=30)

# Larger fetch size used ONLY by _fetch_cluster's targeted same-timestamp
# recovery, never for normal paging (see PAGE_SIZE). Confirmed live: the
# largest same-timestamp cluster found so far is 2,288 alerts, giving
# ~2.2x headroom. Details: docs/lsst-ingestion-recovery.md.
CLUSTER_FETCH_SIZE = 5000

# How many consecutive "partial" IngestionLog cycles with an IDENTICAL
# window_start must be observed before check_stall() reports a stall. Only
# meaningful now that MAX_WINDOW_SPAN keeps that window genuinely stable
# across repeated failures -- see docs/lsst-ingestion-recovery.md.
STALL_THRESHOLD_CYCLES = 3

# Stable machine key that identifies this source in alert_sources — distinct
# from "fink_ztf" so ZTF and LSST alerts remain distinguishable in
# alerts_live via source_id, the same mechanism that already keeps CHIME/FRB
# rows distinguishable from everything else.
LSST_SOURCE_NAME = "fink_lsst"


# --------------------------------------------------------------------------- #
# Pure helpers — no I/O, easy to unit-test                                    #
# --------------------------------------------------------------------------- #

def _is_valid_lsst_alert(alert: dict) -> bool:
    """Return True only if the mandatory identifying fields are present.

    Alerts missing sky position or identifiers cannot be stored meaningfully.
    """
    return (
        alert.get("r:diaObjectId") is not None
        and alert.get("r:diaSourceId") is not None
        and alert.get("r:ra") is not None
        and alert.get("r:dec") is not None
    )


def _mjd_to_datetime(mjd: float) -> datetime:
    return Time(mjd, format="mjd").to_datetime(timezone=timezone.utc)


def _mjd_to_jd(mjd: float) -> float:
    """Exact MJD -> JD conversion (JD = MJD + 2400000.5). LSST alerts carry
    no direct JD field, only midpointMjdTai; this is precise arithmetic, not
    an approximation."""
    return mjd + 2400000.5


def _extract_cats_score(alert: dict) -> Optional[float]:
    """Best available single confidence score on the LSST side: the CATS
    classifier's class-confidence (f:clf_cats_score). This is NOT the same
    metric or model as any of ZTF's SuperNNova-family scores — see module
    docstring — it is simply the closest analogue Fink's LSST schema offers.
    """
    val = alert.get("f:clf_cats_score")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Service                                                                      #
# --------------------------------------------------------------------------- #

class LsstFinkIngestionService:
    """Ingests live Rubin/LSST alerts from Fink's LSST-specific REST API."""

    def __init__(self, api_url: str = LSST_API_URL) -> None:
        self._api_url = api_url

    # ----------------------------------------------------------------------- #
    # Public entry point                                                        #
    # ----------------------------------------------------------------------- #

    async def ingest(self, session: AsyncSession) -> int:
        """Run one ingestion cycle across all LSST_TAGS.

        The date window is [last successful run's stop, min(now, last
        successful run's stop + MAX_WINDOW_SPAN)) — advancing a real
        cursor, not a fixed-count fetch (see module docstring), with a
        hard cap on how much time a single cycle's window can cover.

        All-or-nothing per cycle: if EVERY tag fully drains its window, the
        cursor advances to window_stop_dt and this run is marked
        "completed". If ANY tag does not (safety cap hit, an HTTP error, or
        an unrecovered same-timestamp cluster — see _fetch_tag_window), the
        run is marked "partial", and the NEXT cycle retries a genuinely
        identical window (MAX_WINDOW_SPAN caps window_stop_dt as well as
        window_start_dt — see its docstring). Re-covering already-ingested
        alerts this way is harmless (ON CONFLICT DO NOTHING); silently
        advancing past unconfirmed data would not be. Rows are never
        deleted, matching the retention philosophy already established for
        ZTF alerts.

        Returns:
            Number of rows **actually inserted** (duplicate skips excluded).
        """
        window_start_dt = await self._get_window_start(session)
        # Capped at MAX_WINDOW_SPAN so a stalled/catching-up window can
        # never grow unboundedly -- see MAX_WINDOW_SPAN's docstring. A
        # no-op when caught up: min() resolves to "now" exactly as before.
        window_stop_dt = min(
            datetime.now(timezone.utc),
            window_start_dt + MAX_WINDOW_SPAN,
        )

        log = IngestionLog(
            source=LSST_SOURCE_NAME,
            query_params={
                "tags": LSST_TAGS,
                "window_start": window_start_dt.isoformat(),
                "window_stop": window_stop_dt.isoformat(),
            },
            status="running",
        )
        session.add(log)
        await session.flush()

        inserted = 0
        had_http_error = False
        all_exhausted = True

        try:
            source_id = await self._ensure_source(session)

            for tag in LSST_TAGS:
                alerts, exhausted, _reached = await self._fetch_tag_window(
                    tag, window_start_dt, window_stop_dt
                )
                if alerts is None:
                    had_http_error = True
                    all_exhausted = False
                    continue

                if not exhausted:
                    all_exhausted = False

                for alert in alerts:
                    if not _is_valid_lsst_alert(alert):
                        logger.warning(
                            "Skipping invalid LSST alert (missing diaObjectId/"
                            "diaSourceId/ra/dec)"
                        )
                        continue
                    try:
                        inserted += await self._insert_alert(session, alert, tag, source_id)
                    except Exception as exc:
                        logger.warning(
                            "Skipping LSST alert %r (tag=%s): %s",
                            alert.get("r:diaSourceId"), tag, exc,
                        )

            if all_exhausted:
                # The window was fully drained, so the next run resumes from
                # its stop time. That resume point goes in cursor_position;
                # completed_at means what it means for every other source --
                # when this run finished. Conflating the two is what made 577
                # historical rows report a negative duration.
                log.cursor_position = window_stop_dt
                log.completed_at = datetime.now(timezone.utc)
                log.status = "failed" if (had_http_error and inserted == 0) else "completed"
            else:
                # Do NOT advance the cursor -- the next cycle retries this
                # exact window from scratch rather than risk skipping
                # whatever wasn't confirmed fully drained this time. Leaving
                # cursor_position NULL is what encodes that: _get_window_start
                # only ever resumes from a row that has one.
                log.completed_at = datetime.now(timezone.utc)
                log.status = "partial"
                logger.warning(
                    "LSST ingestion did not fully drain its window for at "
                    "least one tag; the next cycle will retry the identical "
                    "window [%s, %s] rather than advance past it.",
                    window_start_dt, window_stop_dt,
                )

            log.objects_ingested = inserted
            await session.commit()

        except Exception as exc:
            logger.error("LSST ingestion run aborted: %s", exc, exc_info=True)
            try:
                log.status = "failed"
                log.error_message = str(exc)[:2000]
                log.completed_at = datetime.now(timezone.utc)
                await session.commit()
            except Exception as commit_exc:
                logger.error(
                    "Failed to persist IngestionLog failure record: %s", commit_exc
                )
            return 0

        logger.info(
            "LSST/Rubin ingestion complete: %d inserted across %d tags",
            inserted, len(LSST_TAGS),
        )
        return inserted

    async def check_stall(self, session: AsyncSession) -> dict:
        """Report whether LSST ingestion looks genuinely stuck, for
        surfacing through GET /api/ingest/lsst/status -- deliberately not
        GET /api/health/ping, which stays pure process liveness (see that
        endpoint's docstring for why the two are kept separate).

        Stalled means: the last STALL_THRESHOLD_CYCLES IngestionLog rows
        for this source are all status == "partial" AND share an identical
        window_start (see MAX_WINDOW_SPAN's docstring for why this is only
        a meaningful signal now). Intended for each status-check poll, not
        the ingestion hot path -- ingestion_log is a small table, so this
        is a cheap read.
        """
        result = await session.execute(
            select(IngestionLog.status, IngestionLog.query_params)
            .where(IngestionLog.source == LSST_SOURCE_NAME)
            .order_by(IngestionLog.id.desc())
            .limit(STALL_THRESHOLD_CYCLES)
        )
        rows = result.all()

        if len(rows) < STALL_THRESHOLD_CYCLES:
            return {"stalled": False, "consecutive_partial_cycles": len(rows)}

        if not all(r.status == "partial" for r in rows):
            return {"stalled": False, "consecutive_partial_cycles": 0}

        window_starts = {
            (r.query_params or {}).get("window_start") for r in rows
        }
        if len(window_starts) == 1:
            return {
                "stalled": True,
                "consecutive_partial_cycles": len(rows),
                "stuck_window_start": window_starts.pop(),
            }

        return {"stalled": False, "consecutive_partial_cycles": 0}

    # ----------------------------------------------------------------------- #
    # Internal helpers                                                          #
    # ----------------------------------------------------------------------- #

    async def _get_window_start(self, session: AsyncSession) -> datetime:
        """Resume from the furthest cursor a completed run has reached.

        Reads `cursor_position`, NOT `completed_at`: the latter is wall-clock
        completion time for every source and says nothing about how far this
        one got. Ordering by the cursor rather than by recency also means a
        late-finishing run that covered an earlier window cannot drag the
        resume point backwards.

        Only "completed" runs carry a cursor -- partial and failed runs leave
        it NULL so their window is retried rather than skipped. Falls back to
        DEFAULT_LOOKBACK_HOURS when no completed run exists yet.
        """
        result = await session.execute(
            select(IngestionLog.cursor_position)
            .where(
                IngestionLog.source == LSST_SOURCE_NAME,
                IngestionLog.status == "completed",
                IngestionLog.cursor_position.isnot(None),
            )
            .order_by(IngestionLog.cursor_position.desc())
            .limit(1)
        )
        last = result.scalar_one_or_none()
        if last is not None:
            return last
        return datetime.now(timezone.utc) - timedelta(hours=DEFAULT_LOOKBACK_HOURS)

    async def _ensure_source(self, session: AsyncSession) -> int:
        """Return ``alert_sources.id`` for ``fink_lsst``, seeding if absent."""
        result = await session.execute(
            select(AlertSource).where(AlertSource.name == LSST_SOURCE_NAME)
        )
        source = result.scalar_one_or_none()

        if source is None:
            source = AlertSource(
                name=LSST_SOURCE_NAME,
                display_name="Fink (LSST/Rubin)",
                source_type="broker",
                base_url="https://api.lsst.fink-portal.org/api/v1/",
                is_active=True,
            )
            session.add(source)
            await session.flush()
            logger.info("Seeded alert_sources row: %s", LSST_SOURCE_NAME)

        return source.id

    async def _fetch_tag_window(
        self, tag: str, start_dt: datetime, stop_dt: datetime
    ) -> tuple[Optional[list[dict]], bool, float]:
        """Fetch every alert for `tag` within [start_dt, stop_dt), paging
        BACKWARD from stop_dt toward start_dt.

        Confirmed live (2026-07-31; see module docstring's "Pagination
        ordering" section): the API returns alerts in DESCENDING order by
        r:midpointMjdTai (newest first), not ascending. `start_dt` therefore
        stays FIXED as the true window floor, and the query's `stopdate`
        shrinks to the oldest timestamp seen in the page just fetched --
        each subsequent page asks for "everything even older than what's
        already been collected."

        Returns (alerts, exhausted, reached_mjd):
          alerts     — collected alert dicts, or None if the very first page
                       failed outright (HTTP/parse error).
          exhausted  — True if the window was fully drained (a short page,
                       or a naturally empty result) before MAX_PAGES_PER_TAG
                       was reached. False if the safety cap was hit, an HTTP
                       error interrupted paging partway through, the cursor
                       could not be safely retreated (missing/non-retreating
                       timestamp), or a full-page same-timestamp cluster
                       could not be confirmed drained even after a targeted
                       recovery attempt (see below).
          reached_mjd — the oldest point actually processed; equals
                       start_dt's MJD if the window was fully drained.

        A single visit can produce many alerts sharing one identical
        midpointMjdTai, with no finer secondary sort key to split a
        cluster safely across a page boundary. If an ENTIRE full page
        shares one timestamp, this triggers a targeted recovery fetch
        (_fetch_cluster) rather than guessing; only if that's also capped
        is the tag marked NOT exhausted with a loud ERROR log. Real
        cluster sizes and the investigation behind this design:
        docs/lsst-ingestion-recovery.md.
        """
        collected: list[dict] = []
        # `cursor_dt` is the shrinking stop-side boundary (walking
        # backward); start_dt itself never moves.
        cursor_dt = stop_dt
        start_mjd = Time(start_dt).mjd
        start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S.%f")

        for _ in range(MAX_PAGES_PER_TAG):
            cursor_str = cursor_dt.strftime("%Y-%m-%d %H:%M:%S.%f")
            prev_cursor_mjd = Time(cursor_dt).mjd
            batch = await self._fetch_page(tag, start_str, cursor_str)

            if batch is None:
                return (collected if collected else None), False, prev_cursor_mjd

            if not batch:
                return collected, True, start_mjd

            first_mjd = batch[0].get("r:midpointMjdTai")
            last_mjd = batch[-1].get("r:midpointMjdTai")
            full_page = len(batch) == PAGE_SIZE

            if full_page and first_mjd is not None and first_mjd == last_mjd:
                cluster_mjd = last_mjd
                recovered = await self._fetch_cluster(tag, cluster_mjd)

                if recovered is not None:
                    # Confirmed the full cluster (not just this one capped
                    # page of it) -- safe to treat as drained and keep
                    # paging past it, rather than giving up on the tag.
                    collected.extend(recovered)
                    logger.warning(
                        "LSST tag %r: recovered a %d-alert same-timestamp "
                        "cluster at r:midpointMjdTai=%s via a targeted "
                        "follow-up fetch (a normal PAGE_SIZE=%d page alone "
                        "could not confirm it was fully drained).",
                        tag, len(recovered), cluster_mjd, PAGE_SIZE,
                    )
                    if cluster_mjd <= start_mjd:
                        return collected, True, start_mjd
                    # Step strictly past the cluster -- it's now fully
                    # captured, so excluding it from the next page's
                    # stopdate bound avoids re-triggering this same branch
                    # on the identical cluster next iteration.
                    cursor_dt = _mjd_to_datetime(cluster_mjd) - timedelta(microseconds=1)
                    continue

                logger.error(
                    "LSST tag %r: a full page of %d alerts all share "
                    "r:midpointMjdTai=%s, and a targeted follow-up fetch "
                    "capped at CLUSTER_FETCH_SIZE=%d alerts could not "
                    "confirm the cluster's true size -- this exceeds "
                    "automatic recovery. Marking this tag's window as NOT "
                    "exhausted; the next cycle retries the identical window "
                    "rather than advancing past unconfirmed data.",
                    tag, PAGE_SIZE, cluster_mjd, CLUSTER_FETCH_SIZE,
                )
                collected.extend(batch)
                return collected, False, cluster_mjd

            collected.extend(batch)

            if not full_page:
                return collected, True, start_mjd

            if last_mjd is None or last_mjd >= prev_cursor_mjd:
                # General stall guard -- catches what the same-timestamp
                # cluster check above does NOT: a cluster boundary split
                # subtly across a page (not the whole page), where shrinking
                # stopdate to last_mjd would still yield a non-progressing
                # (or backward) next query even though this page itself
                # wasn't 100% one timestamp. Rather than loop or guess, stop
                # and report not-exhausted.
                #
                # Note: collected.extend(batch) above already ran, so the
                # alerts returned here duplicate whatever the previous page
                # already contributed. That's expected, not an oversight --
                # ON CONFLICT DO NOTHING at insert time makes the repeat
                # harmless, and the alternative (dropping this page to avoid
                # the duplicate) risks silently discarding alerts that were
                # never actually captured elsewhere.
                return collected, False, prev_cursor_mjd

            if last_mjd <= start_mjd:
                return collected, True, start_mjd

            cursor_dt = _mjd_to_datetime(last_mjd)

        logger.warning(
            "Hit MAX_PAGES_PER_TAG=%d for tag %r; more alerts may remain in "
            "this window. The next cycle retries the identical window.",
            MAX_PAGES_PER_TAG, tag,
        )
        return collected, False, Time(cursor_dt).mjd

    async def _fetch_page(
        self, tag: str, start: str, stop: str, n: int = PAGE_SIZE
    ) -> Optional[list[dict]]:
        """GET one page from LSST Fink's /api/v1/tags for one tag.

        `start`/`stop` are ISO datetime strings ("%Y-%m-%d %H:%M:%S.%f") --
        confirmed live against the real API to parse correctly, unlike a raw
        MJD numeric string, which the API documents as accepted but which
        actually returns a 500 (verified 2026-07). `n` defaults to PAGE_SIZE
        for normal paging; _fetch_cluster passes CLUSTER_FETCH_SIZE instead
        for its one-off targeted recovery fetch.

        Returns a list of alert dicts on success, or None on any HTTP/parse
        error (caller treats that as a failure and does not advance the
        ingestion cursor past this point).
        """
        params = {
            "tag": tag,
            "n": n,
            "startdate": start,
            "stopdate": stop,
            "output-format": "json",
        }
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.get(self._api_url, params=params)
        except httpx.HTTPError as exc:
            logger.error("HTTP error fetching LSST tag %r: %s", tag, exc)
            return None

        if resp.status_code != 200:
            logger.error(
                "LSST Fink returned HTTP %d for tag %r: %.200s",
                resp.status_code, tag, resp.text,
            )
            return None

        try:
            data = resp.json()
        except Exception as exc:
            logger.error("Failed to parse LSST Fink JSON for tag %r: %s", tag, exc)
            return None

        if not isinstance(data, list):
            logger.warning(
                "Unexpected LSST Fink response shape for %r: %s",
                tag, type(data).__name__,
            )
            return None

        return data

    async def _fetch_cluster(
        self, tag: str, cluster_mjd: float
    ) -> Optional[list[dict]]:
        """Targeted recovery fetch for a same-timestamp cluster that a
        normal PAGE_SIZE page could not confirm was fully drained (see the
        cluster-detection branch in _fetch_tag_window).

        Queries a narrow +/-2s bracket around cluster_mjd with
        CLUSTER_FETCH_SIZE (much larger than PAGE_SIZE), then filters the
        result down to exactly cluster_mjd -- this exact-match filter is
        what makes the bracket width safe, not the width itself. The +/-2s
        width was verified against real visit-timestamp gap data, not
        assumed: docs/lsst-ingestion-recovery.md.

        Returns the exact-timestamp alert list if CLUSTER_FETCH_SIZE was
        enough to capture it uncapped, or None if even this generous fetch
        was itself capped or failed -- the caller falls back to the
        existing defensive not-exhausted behavior.
        """
        center_dt = _mjd_to_datetime(cluster_mjd)
        start_str = (center_dt - timedelta(seconds=2)).strftime("%Y-%m-%d %H:%M:%S.%f")
        stop_str = (center_dt + timedelta(seconds=2)).strftime("%Y-%m-%d %H:%M:%S.%f")

        batch = await self._fetch_page(tag, start_str, stop_str, n=CLUSTER_FETCH_SIZE)
        if batch is None or len(batch) >= CLUSTER_FETCH_SIZE:
            return None

        return [r for r in batch if r.get("r:midpointMjdTai") == cluster_mjd]

    async def _insert_alert(
        self,
        session: AsyncSession,
        alert: dict,
        tag: str,
        source_id: int,
    ) -> int:
        """Insert one alert into ``alerts_live`` using ON CONFLICT DO NOTHING.

        Same idempotent-rerun conflict target as the ZTF path
        (uq_alerts_live_source_external on (source_id, external_id)) —
        source_id differs (fink_lsst vs fink_ztf), so this can never collide
        with a ZTF row even if diaSourceId and candid happened to coincide.

        Returns:
            ``1`` if the row was inserted, ``0`` if it was a duplicate.
        """
        mjd = alert.get("r:midpointMjdTai")

        stmt = (
            pg_insert(AlertLive)
            .values(
                source_id=source_id,
                # diaSourceId is the per-detection identifier -- the LSST
                # analogue of ZTF's candid, and the natural external_id.
                external_id=str(alert["r:diaSourceId"]),
                ra=alert.get("r:ra"),
                dec=alert.get("r:dec"),
                alert_type="lsst_fink",
                # No single-label classification field exists on LSST's
                # schema (see module docstring) -- the matched tag is the
                # most honest classification label available.
                classification=tag,
                classification_score=_extract_cats_score(alert),
                jd=_mjd_to_jd(mjd) if mjd is not None else None,
                detected_at=_mjd_to_datetime(mjd) if mjd is not None else None,
                raw_payload=alert,
            )
            .on_conflict_do_nothing(constraint="uq_alerts_live_source_external")
        )
        result = await session.execute(stmt)
        return result.rowcount
