"""
Cross-matching service for enriching alerts with catalog data.

Queries SIMBAD (known astronomical objects) and TNS (Transient Name Server)
to add context to each alert: is this a known source? Is it near a galaxy?
Has it already been reported by another group?
"""

import asyncio
import logging
import threading
import time
from typing import Optional

import astropy.units as u
import requests
from astropy.coordinates import SkyCoord
from astroquery.simbad import SimbadClass
from astroquery.simbad import conf as simbad_conf
from pyvo.dal.exceptions import DALServiceError
from requests.adapters import HTTPAdapter
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.models import Object

logger = logging.getLogger(__name__)

# Politeness delay between successive SIMBAD cone searches. SIMBAD asks clients
# not to hammer the service; one query per second is well within their limits
# while keeping a 50-object batch bounded (~50s of wall clock, none of it
# blocking the event loop because the query itself runs in a worker thread).
SIMBAD_RATE_LIMIT_SECONDS = 1.0

# Hard ceilings, because astroquery gives us none that work. In 0.4.x
# ``Simbad.TIMEOUT`` is a dead attribute (the live one is ``Simbad.timeout``,
# default 1080 s, a server-side execution limit), and the pyvo capabilities
# request that precedes every query has no connect timeout at all, so an
# unreachable SIMBAD blocks until the OS gives up (~127 s on Linux). Fifty
# objects at ~135 s each is ~1h52m, which is how one cycle ran almost two hours.
SIMBAD_CALL_TIMEOUT_SECONDS = 15.0
# Whole-step budget: the step ends cleanly when it is spent, and the objects
# not reached are simply picked up by the next cycle.
SIMBAD_STEP_BUDGET_SECONDS = 300.0
# wait_for abandons the await but cannot kill the worker thread, which keeps
# blocking until its socket gives up. When SIMBAD is down, stop after this many
# timeouts in a row instead of leaking a thread per remaining object.
SIMBAD_MAX_CONSECUTIVE_TIMEOUTS = 3

# Real socket timeouts, injected into the requests session each SIMBAD client
# uses (see _TimeoutAdapter). Without them a dead host blocks ~135 s, and --
# the part that hurts -- that happens while holding a lock shared by every
# SIMBAD host: pyvo fetches a service's capabilities through astropy's
# ``lazyproperty``, whose RLock is one per descriptor (class-wide), is held for
# the whole HTTP request, and is retaken after a failure because failures are
# not cached. So a hung CDS request queued the mirror's request behind it, and
# abandoned worker threads then ran strictly one at a time, ~135 s apart.
# With a short connect timeout the lock is held for seconds, not minutes.
SIMBAD_CONNECT_TIMEOUT_SECONDS = 5.0
SIMBAD_READ_TIMEOUT_SECONDS = 10.0


class SimbadUnavailable(Exception):
    """A SIMBAD host could not be reached or did not answer in time.

    Distinct from "no object at this position", which is a normal answer.
    """


class _TimeoutAdapter(HTTPAdapter):
    """Give every request on a session a default (connect, read) timeout.

    pyvo calls ``session.get(url, stream=True)`` with no timeout, and
    astroquery exposes no way to set one, so the session is the only seam.
    """

    def __init__(self, timeout, **kwargs):
        super().__init__(**kwargs)
        self._default_timeout = timeout

    def send(self, request, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self._default_timeout
        return super().send(request, **kwargs)


# One client per host. The astroquery module-level ``Simbad`` is a single
# shared instance whose ``server`` is global state; separate instances let the
# primary and the mirror coexist without flipping a global between calls.
_simbad_clients: dict[str, SimbadClass] = {}
_simbad_clients_lock = threading.Lock()


def _simbad_client(host: str) -> SimbadClass:
    with _simbad_clients_lock:
        client = _simbad_clients.get(host)
        if client is None:
            client = SimbadClass()
            client.server = host
            adapter = _TimeoutAdapter(
                (SIMBAD_CONNECT_TIMEOUT_SECONDS, SIMBAD_READ_TIMEOUT_SECONDS)
            )
            for scheme in ("https://", "http://"):
                client._session.mount(scheme, adapter)
            # Configure SIMBAD to return the fields we need
            try:
                client.add_votable_fields("otype", "distance_result")
            except Exception:
                pass  # Newer astroquery versions include these by default
            _simbad_clients[host] = client
        return client


class EnrichmentService:
    """Enriches alert objects with cross-catalog information."""

    def _simbad_hosts(self) -> list[str]:
        """SIMBAD hosts in the order to try them: the configured one, then the
        other mirror astroquery knows about."""
        servers = list(simbad_conf.servers_list)
        primary = get_settings().simbad_server
        if primary not in servers:
            logger.warning(
                f"SIMBAD_SERVER={primary!r} is not one of {servers}; using {servers[0]}"
            )
            primary = servers[0]
        return [primary] + [h for h in servers if h != primary]

    async def enrich_object(
        self,
        session: AsyncSession,
        oid: str,
        ra: float,
        dec: float,
        host: Optional[str] = None,
    ) -> bool:
        """
        Cross-match a single object against external catalogs.

        Args:
            session: Database session.
            oid: Object identifier.
            ra: Right Ascension in degrees.
            dec: Declination in degrees.
            host: SIMBAD host to ask; defaults to the configured one.

        Returns:
            True if SIMBAD answered (match or no match), False if the host
            could not be reached or exceeded SIMBAD_CALL_TIMEOUT_SECONDS.
        """
        host = host or self._simbad_hosts()[0]
        # SIMBAD cross-match. Simbad.query_region is a synchronous, network-
        # blocking call; run it in a worker thread so the asyncio event loop
        # (and the whole API) stays responsive during ingestion.
        try:
            simbad_result = await asyncio.wait_for(
                asyncio.to_thread(self._query_simbad_host, host, ra, dec, 5.0),
                timeout=SIMBAD_CALL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"SIMBAD query for {oid} on {host} exceeded "
                f"{SIMBAD_CALL_TIMEOUT_SECONDS:.0f}s; skipping"
            )
            return False
        except SimbadUnavailable as e:
            logger.warning(f"SIMBAD host {host} unavailable for {oid}: {e}")
            return False

        if simbad_result:
            await session.execute(
                update(Object)
                .where(Object.oid == oid)
                .values(
                    cross_match_catalog="SIMBAD",
                    cross_match_name=simbad_result["name"],
                    cross_match_type=simbad_result["otype"],
                    cross_match_distance_arcsec=simbad_result["distance_arcsec"],
                )
            )
            # Commit per object so the write transaction lasts milliseconds
            # rather than spanning every later SIMBAD call.
            await session.commit()
            logger.debug(f"{oid} matched to SIMBAD: {simbad_result['name']} ({simbad_result['otype']})")
        return True

    def _query_simbad(self, ra: float, dec: float, radius_arcsec: float = 5.0) -> Optional[dict]:
        """Query SIMBAD, trying each host in turn. None if no match or no host answers.

        The synchronous entry point for callers outside the batch step (the
        object-lookup API), which want a plain answer and no host bookkeeping.
        """
        for host in self._simbad_hosts():
            try:
                return self._query_simbad_host(host, ra, dec, radius_arcsec)
            except SimbadUnavailable as e:
                logger.warning(f"SIMBAD host {host} unavailable: {e}")
        return None

    def _query_simbad_host(
        self, host: str, ra: float, dec: float, radius_arcsec: float = 5.0
    ) -> Optional[dict]:
        """
        Query one SIMBAD host for objects near a given sky position.

        Args:
            host: SIMBAD TAP host.
            ra: Right Ascension in degrees.
            dec: Declination in degrees.
            radius_arcsec: Search radius in arcseconds.

        Returns:
            Dict with name, otype, distance_arcsec or None if no match.

        Raises:
            SimbadUnavailable: the host could not be reached, timed out, or
                returned a service-level error. Never folded into None: with
                real connect timeouts a dead host now fails in seconds, and
                reporting that as "no match" would look like a healthy,
                empty cycle.
        """
        try:
            coord = SkyCoord(ra=ra, dec=dec, unit=(u.degree, u.degree), frame="icrs")
            result = _simbad_client(host).query_region(coord, radius=radius_arcsec * u.arcsec)

            if result is None or len(result) == 0:
                return None

            # Take the closest match
            # Column names vary by astroquery version (MAIN_ID vs main_id)
            id_col = 'main_id' if 'main_id' in result.colnames else 'MAIN_ID'
            type_col = 'otype' if 'otype' in result.colnames else 'OTYPE'
            dist_col = next(
                (c for c in result.colnames if 'dist' in c.lower() or 'DISTANCE' in c),
                None
            )
            row = result[0]
            return {
                "name": str(row[id_col]),
                "otype": str(row[type_col]) if type_col in result.colnames else "unknown",
                "distance_arcsec": float(row[dist_col]) if dist_col else 0.0,
            }

        except (requests.RequestException, DALServiceError, TimeoutError, OSError) as e:
            first_line = (str(e).splitlines() or [""])[0][:120]
            raise SimbadUnavailable(f"{type(e).__name__}: {first_line}") from e
        except Exception as e:
            logger.warning(f"SIMBAD query failed for ({ra}, {dec}) on {host}: {e}")
            return None

    async def enrich_batch(self, session: AsyncSession, objects: list[Object]):
        """Enrich a batch of objects against SIMBAD.

        Rate limiting: a real ``asyncio.sleep`` of ``SIMBAD_RATE_LIMIT_SECONDS``
        is awaited after each SIMBAD query so the service is not hammered. The
        sleep is non-blocking, so the event loop remains free for API traffic.

        FRBs are skipped: CHIME/FRB positions carry ~arcminute uncertainty, far
        larger than the 5-arcsec SIMBAD cone, so any match would be a chance
        coincidence. This mirrors the exclusion in the scheduler query and
        guards callers that reach this method by another path.

        Bounded: each call is capped at SIMBAD_CALL_TIMEOUT_SECONDS, the step
        as a whole at SIMBAD_STEP_BUDGET_SECONDS, and it gives up early after
        SIMBAD_MAX_CONSECUTIVE_TIMEOUTS timeouts in a row. No database
        transaction is held open while waiting on SIMBAD.
        """
        # Snapshot plain values, then end the caller's read transaction. The
        # SELECT that produced `objects` left one open ("idle in transaction"),
        # and it would otherwise stay open for the whole network-bound loop,
        # blocking any DDL queued behind it.
        pending = [
            (obj.oid, obj.ra, obj.dec)
            for obj in objects
            if not obj.cross_match_catalog and obj.classification != "FRB"
        ]
        await session.commit()

        hosts = self._simbad_hosts()
        host_idx = 0
        answering_host = None
        enriched = 0
        timeouts = 0
        deadline = time.monotonic() + SIMBAD_STEP_BUDGET_SECONDS
        for i, (oid, ra, dec) in enumerate(pending):
            if time.monotonic() >= deadline:
                logger.warning(
                    f"SIMBAD step budget ({SIMBAD_STEP_BUDGET_SECONDS:.0f}s) spent; "
                    f"ending with {len(pending) - i} objects left for the next cycle"
                )
                break

            answered = await self.enrich_object(session, oid, ra, dec, host=hosts[host_idx])
            if not answered and host_idx + 1 < len(hosts):
                # First failure of a host: move the rest of this cycle to the
                # next one and retry this object there, so it is not lost.
                logger.warning(
                    f"SIMBAD host {hosts[host_idx]} failed; switching to "
                    f"{hosts[host_idx + 1]} for the rest of this cycle"
                )
                host_idx += 1
                answered = await self.enrich_object(session, oid, ra, dec, host=hosts[host_idx])

            if answered:
                enriched += 1
                timeouts = 0
                answering_host = hosts[host_idx]
            else:
                timeouts += 1
                if timeouts >= SIMBAD_MAX_CONSECUTIVE_TIMEOUTS:
                    logger.warning(
                        f"SIMBAD failed {timeouts} times in a row; assuming it is "
                        f"unreachable and ending the step"
                    )
                    break
                continue

            # SIMBAD rate limiting: space out successive queries.
            await asyncio.sleep(SIMBAD_RATE_LIMIT_SECONDS)

        # One line per cycle, always, naming the host that actually answered.
        if answering_host:
            via = f"answered by {answering_host}"
        elif pending:
            via = f"no host answered ({', '.join(hosts)})"
        else:
            via = "no query needed"
        logger.info(f"Enriched {enriched}/{len(objects)} objects with SIMBAD data; SIMBAD {via}")
        return enriched
