"""
Fink/ZTF Live Alert Ingestion Service — Sprint 4.

Polls the Fink broker REST API for the latest ZTF transient alerts across
four classification classes, strips heavy light-curve feature blobs from
the payload, maps fields onto AlertLive rows, and inserts them with
ON CONFLICT DO NOTHING so reruns are always safe.

One IngestionLog row is written per run.  A 90-day retention DELETE runs
after every insert batch to keep the table bounded.

Endpoint: https://api.ztf.fink-portal.org/api/v1/latests
Confirmed reachable from Render's network during Sprint 4 testing.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AlertLive, AlertSource, IngestionLog

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #

FINK_API_URL = "https://api.ztf.fink-portal.org/api/v1/latests"

FINK_CLASSES = [
    "SN candidate",
    "Kilonova candidate",
    "Early SN Ia candidate",
    "SLSN candidate",
]

ALERTS_PER_CLASS = 100

# These fields are large string blobs (~500 chars each).  Strip them before
# storing in raw_payload JSONB to avoid unnecessary column bloat.
_STRIP_FIELDS = frozenset({"d:lc_features_g", "d:lc_features_r"})

# Stable machine key that identifies this source in alert_sources
FINK_SOURCE_NAME = "fink_ztf"


# --------------------------------------------------------------------------- #
# Pure helpers — no I/O, easy to unit-test                                    #
# --------------------------------------------------------------------------- #

def _strip_lc_features(alert: dict) -> dict:
    """Return a shallow copy of *alert* with lc_features_g/r removed.

    Both keys are dropped whether or not they are present in the dict,
    so callers never need to guard.
    """
    return {k: v for k, v in alert.items() if k not in _STRIP_FIELDS}


def _parse_lastdate(lastdate: Optional[str]) -> Optional[datetime]:
    """Parse Fink's ``v:lastdate`` string into a UTC-aware datetime.

    Fink emits two observed formats::

        "2026-06-07 10:03:28.002"   ← most common (with milliseconds)
        "2026-06-07 10:03:28"       ← occasionally without
    """
    if not lastdate:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(lastdate, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    logger.warning("Could not parse Fink lastdate: %r", lastdate)
    return None


def _is_valid_alert(alert: dict) -> bool:
    """Return True only if the three mandatory fields are present and non-None.

    Alerts missing sky position or object identifier cannot be stored
    meaningfully and must be skipped before reaching _insert_alert.
    """
    return (
        alert.get("i:objectId") is not None
        and alert.get("i:ra") is not None
        and alert.get("i:dec") is not None
    )


def _pick_score(alert: dict, class_name: str) -> Optional[float]:
    """Return the most relevant classifier score for *class_name*.

    * ``Kilonova candidate``  →  ``d:rf_kn_vs_nonkn``  (Random Forest KN)
    * everything else         →  ``d:snn_sn_vs_all``   (SuperNNova SN-vs-all)
    """
    key = (
        "d:rf_kn_vs_nonkn"
        if class_name == "Kilonova candidate"
        else "d:snn_sn_vs_all"
    )
    val = alert.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Service                                                                      #
# --------------------------------------------------------------------------- #

class FinkIngestionService:
    """Ingests live ZTF alerts from the Fink broker REST API."""

    def __init__(self, api_url: str = FINK_API_URL) -> None:
        self._api_url = api_url

    # ----------------------------------------------------------------------- #
    # Public entry point                                                        #
    # ----------------------------------------------------------------------- #

    async def ingest(self, session: AsyncSession) -> int:
        """Run one full ingestion cycle across all FINK_CLASSES.

        Steps:
          1. Seed ``alert_sources`` on first run (idempotent).
          2. Fetch ``ALERTS_PER_CLASS`` alerts per class from Fink.
          3. Insert each alert with ``ON CONFLICT DO NOTHING``.
          4. Write one ``IngestionLog`` row with outcome.
          5. Delete ``alerts_live`` rows older than 90 days.

        Returns:
            Number of rows **actually inserted** (duplicate skips excluded).
        """
        log = IngestionLog(
            source=FINK_SOURCE_NAME,
            query_params={"classes": FINK_CLASSES, "n_per_class": ALERTS_PER_CLASS},
            status="running",
        )
        session.add(log)
        await session.flush()  # materialise log.id before any potential failure

        inserted = 0
        had_http_error = False

        try:
            source_id = await self._ensure_source(session)

            for class_name in FINK_CLASSES:
                raw_alerts = await self._fetch_class(class_name)

                if raw_alerts is None:
                    # _fetch_class already logged the detail
                    had_http_error = True
                    continue

                for alert in raw_alerts:
                    if not _is_valid_alert(alert):
                        logger.warning(
                            "Skipping invalid alert (missing objectId/ra/dec): objectId=%r",
                            alert.get("i:objectId"),
                        )
                        continue

                    try:
                        inserted += await self._insert_alert(
                            session, alert, class_name, source_id
                        )
                    except Exception as exc:
                        # Single-alert failures must never abort the whole run
                        logger.warning(
                            "Skipping alert %r (class=%s): %s",
                            alert.get("i:objectId"),
                            class_name,
                            exc,
                        )

            # 90-day retention cap — Supabase free tier is 500 MB total.
            # At ~400 new rows/night, keeping 90 days costs ~72 MB.
            # Remove this line if Rubin Scout moves to paid/institutional hosting.
            # No user input involved — interval is hardcoded, not interpolated.
            await session.execute(
                text(
                    "DELETE FROM alerts_live "
                    "WHERE ingested_at < NOW() - INTERVAL '90 days'"
                )
            )

            log.objects_ingested = inserted
            log.completed_at = datetime.now(timezone.utc)
            # Only mark "failed" if we had HTTP errors AND inserted nothing at all
            log.status = (
                "failed" if (had_http_error and inserted == 0) else "completed"
            )
            await session.commit()

        except Exception as exc:
            logger.error("Fink ingestion run aborted: %s", exc, exc_info=True)
            try:
                log.status = "failed"
                log.error_message = str(exc)[:2000]  # truncate for column width
                log.completed_at = datetime.now(timezone.utc)
                await session.commit()
            except Exception as commit_exc:
                # Commit itself failed — log and bail without masking the original
                logger.error(
                    "Failed to persist IngestionLog failure record: %s", commit_exc
                )
            return 0

        logger.info(
            "Fink ingestion complete: %d inserted across %d classes",
            inserted,
            len(FINK_CLASSES),
        )
        return inserted

    # ----------------------------------------------------------------------- #
    # Internal helpers                                                          #
    # ----------------------------------------------------------------------- #

    async def _ensure_source(self, session: AsyncSession) -> int:
        """Return ``alert_sources.id`` for ``fink_ztf``, seeding if absent."""
        result = await session.execute(
            select(AlertSource).where(AlertSource.name == FINK_SOURCE_NAME)
        )
        source = result.scalar_one_or_none()

        if source is None:
            source = AlertSource(
                name=FINK_SOURCE_NAME,
                display_name="Fink (ZTF)",
                source_type="broker",
                base_url="https://api.fink-portal.org/api/v1/",
                is_active=True,
            )
            session.add(source)
            await session.flush()
            logger.info("Seeded alert_sources row: %s", FINK_SOURCE_NAME)

        return source.id

    async def _fetch_class(self, class_name: str) -> Optional[list[dict]]:
        """POST to Fink ``/api/v1/latests`` for one classification class.

        Returns:
            List of alert dicts on success.
            ``None`` on any HTTP or parse error (caller should treat as failure).
        """
        payload = {
            "class": class_name,
            "n": ALERTS_PER_CLASS,
            "output-format": "json",
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(self._api_url, json=payload)
        except httpx.HTTPError as exc:
            logger.error(
                "HTTP error fetching Fink class %r: %s", class_name, exc
            )
            return None

        if resp.status_code != 200:
            logger.error(
                "Fink returned HTTP %d for class %r: %.200s",
                resp.status_code,
                class_name,
                resp.text,
            )
            return None

        try:
            data = resp.json()
        except Exception as exc:
            logger.error(
                "Failed to parse Fink JSON for class %r: %s", class_name, exc
            )
            return None

        if not isinstance(data, list):
            logger.warning(
                "Unexpected Fink response shape for %r: %s",
                class_name,
                type(data).__name__,
            )
            return None

        return data

    async def _insert_alert(
        self,
        session: AsyncSession,
        alert: dict,
        class_name: str,
        source_id: int,
    ) -> int:
        """Insert one alert into ``alerts_live`` using ON CONFLICT DO NOTHING.

        The conflict target is the named unique constraint
        ``uq_alerts_live_source_external`` on ``(source_id, external_id)``,
        so reruns are always idempotent.

        Returns:
            ``1`` if the row was inserted, ``0`` if it was a duplicate.
        """
        stripped = _strip_lc_features(alert)

        stmt = (
            pg_insert(AlertLive)
            .values(
                source_id=source_id,
                # candid is the per-observation ID — one row per nightly alert.
                # Using objectId here would give a snapshot (one row per
                # transient, silently dropped on subsequent nights).  Using
                # candid gives a proper log: classification score evolution is
                # preserved and the 90-day retention actually does useful work.
                external_id=str(alert["i:candid"]),
                ra=alert.get("i:ra"),
                dec=alert.get("i:dec"),
                alert_type="ztf_fink",
                classification=alert.get("v:classification"),
                classification_score=_pick_score(alert, class_name),
                jd=alert.get("i:jd"),
                detected_at=_parse_lastdate(alert.get("v:lastdate")),
                raw_payload=stripped,
            )
            .on_conflict_do_nothing(constraint="uq_alerts_live_source_external")
        )
        result = await session.execute(stmt)
        # rowcount == 1 → inserted; 0 → DO NOTHING path (duplicate)
        return result.rowcount
