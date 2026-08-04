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

    A deeper, related risk was also found and is NOT fully solved by
    correcting the direction alone: a single visit can produce many alerts
    sharing the identical midpointMjdTai — confirmed live, one visit
    produced 460 most_likely_sn alerts at one exact timestamp. There is no
    finer-grained, unique, monotonic secondary sort key available from this
    API to guarantee a same-timestamp cluster is never split unsafely across
    a page boundary. _fetch_tag_window detects the specific case where an
    ENTIRE full page shares one timestamp (a strong signal that instant's
    true cluster size may exceed PAGE_SIZE) and treats the run as NOT
    exhausted rather than guessing — this converts a possible silent gap
    into a logged, visible one, but does not provably eliminate the
    underlying risk. GET with query-string parameters was also confirmed to
    return actual alert records (not the tag-name/description catalog that a
    bare, param-less GET to the same path returns) — the calling convention
    itself is correct.

  Current operational status (as of this writing)
    The most recent night with any recorded alerts is 2026-07-14. Rubin
    Observatory's summit was evacuated on 2026-07-14 due to a record-breaking
    storm and, per the 2026-07-24 community status update, staff had not yet
    been able to return. This is a real, documented, presumably-temporary
    observatory closure — not a Fink/API failure and not evidence that this
    ingestion path is broken. A live run against this deployment while the
    summit is closed will correctly find zero new alerts and should log that
    plainly, not treat it as an error.
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

        The date window is [last successful run's stop, now) — advancing a
        real cursor, not a fixed-count fetch (see module docstring for why
        LSST's alert volume makes a fixed n=100 silently lossy).

        All-or-nothing per cycle: if EVERY tag fully drains its window, the
        cursor advances to window_stop_dt and this run is marked
        "completed" — the normal case. If ANY tag does not (safety cap hit,
        an HTTP error mid-pagination, or a same-timestamp cluster whose true
        size can't be confirmed — see _fetch_tag_window), this run is marked
        "partial" instead, and _get_window_start (which only considers
        "completed" rows) will have the NEXT cycle retry the identical
        window from scratch. Re-covering already-ingested alerts this way is
        harmless (ON CONFLICT DO NOTHING); silently advancing past
        unconfirmed data would not be. Rows are never deleted, matching the
        retention philosophy already established for ZTF alerts.

        Returns:
            Number of rows **actually inserted** (duplicate skips excluded).
        """
        window_start_dt = await self._get_window_start(session)
        window_stop_dt = datetime.now(timezone.utc)

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
                log.completed_at = window_stop_dt
                log.status = "failed" if (had_http_error and inserted == 0) else "completed"
            else:
                # Do NOT advance the cursor -- the next cycle retries this
                # exact window from scratch rather than risk skipping
                # whatever wasn't confirmed fully drained this time.
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

    # ----------------------------------------------------------------------- #
    # Internal helpers                                                          #
    # ----------------------------------------------------------------------- #

    async def _get_window_start(self, session: AsyncSession) -> datetime:
        """Resume from the last successfully-completed run's stop time.

        Falls back to DEFAULT_LOOKBACK_HOURS on the very first run (no prior
        completed IngestionLog row for this source exists yet).
        """
        result = await session.execute(
            select(IngestionLog.completed_at)
            .where(
                IngestionLog.source == LSST_SOURCE_NAME,
                IngestionLog.status == "completed",
                IngestionLog.completed_at.isnot(None),
            )
            .order_by(IngestionLog.completed_at.desc())
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
                       timestamp), or a full page shared one single
                       timestamp (see below).
          reached_mjd — the oldest point actually processed; equals
                       start_dt's MJD if the window was fully drained.

        A single visit can produce many alerts sharing the identical
        midpointMjdTai (confirmed live: 460 alerts at one exact timestamp
        for one tag on one real visit, using a +/-2s bracket at n=5000) --
        there is no finer-grained, unique, monotonic secondary sort key
        available from this API. If an ENTIRE full page shares one
        timestamp, that cluster's true size may exceed PAGE_SIZE, and
        shrinking `stopdate` to it would silently drop whatever didn't fit
        in this page (this exact stall was reproduced live: a real
        page-by-page walk against the 2026-07-14 window repeatedly stuck on
        mjd=61235.3751192781). Rather than guess, this is detected
        explicitly and treated as NOT exhausted with a loud ERROR log --
        this converts a possible silent gap into a visible one, but does
        NOT provably eliminate the underlying risk in an even larger
        cluster than one page.
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
                logger.error(
                    "LSST tag %r: a full page of %d alerts all share "
                    "r:midpointMjdTai=%s -- this cluster may be larger than "
                    "PAGE_SIZE and cannot be safely paged past without risking "
                    "a silent gap. Marking this tag's window as NOT exhausted; "
                    "the next cycle retries the whole window rather than "
                    "advancing past unconfirmed data.",
                    tag, PAGE_SIZE, last_mjd,
                )
                collected.extend(batch)
                return collected, False, last_mjd

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
        self, tag: str, start: str, stop: str
    ) -> Optional[list[dict]]:
        """GET one page from LSST Fink's /api/v1/tags for one tag.

        `start`/`stop` are ISO datetime strings ("%Y-%m-%d %H:%M:%S.%f") --
        confirmed live against the real API to parse correctly, unlike a raw
        MJD numeric string, which the API documents as accepted but which
        actually returns a 500 (verified 2026-07).

        Returns a list of alert dicts on success, or None on any HTTP/parse
        error (caller treats that as a failure and does not advance the
        ingestion cursor past this point).
        """
        params = {
            "tag": tag,
            "n": PAGE_SIZE,
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
