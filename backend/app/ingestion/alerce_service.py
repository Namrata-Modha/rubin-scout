"""
ALeRCE Alert Ingestion Service.

Polls the ALeRCE API for recent transient candidates, enriches them
with cross-match data, and stores them in the local database.

This is the polling-based approach for the MVP. Week 5 of the roadmap
replaces this with a real-time Kafka consumer.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from alerce.core import Alerce
from astropy.time import Time
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.models import Object, Detection, ClassificationProbability, IngestionLog

logger = logging.getLogger(__name__)
settings = get_settings()

# ALeRCE classification classes we care about (transients, not variables)
TARGET_CLASSES = [
    "SNIa", "SNIbc", "SNII", "SLSN",   # Supernovae
    "TDE",                                 # Tidal disruption events
    "KN",                                  # Kilonovae (GW counterparts!)
    "AGN", "Blazar", "QSO",              # Active galactic nuclei
    "CV/Nova",                             # Cataclysmic variables / novae
]

# ZTF filter ID to band name mapping
FILTER_MAP = {1: "g", 2: "r", 3: "i"}


def mjd_to_datetime(mjd: float) -> datetime:
    """Convert Modified Julian Date to a timezone-aware datetime."""
    return Time(mjd, format="mjd").to_datetime(timezone=timezone.utc)


class AlerceIngestionService:
    """Pulls classified alerts from ALeRCE and stores them locally."""

    def __init__(self):
        self.client = Alerce()

    async def ingest_recent(
        self,
        session: AsyncSession,
        lookback_days: float = 1.0,
        target_classes: Optional[list[str]] = None,
    ) -> int:
        """
        Pull recent transient candidates from ALeRCE and store them.

        Args:
            session: Async database session.
            lookback_days: How far back to look for new detections.
            target_classes: Which ALeRCE classes to ingest. Defaults to TARGET_CLASSES.

        Returns:
            Total number of objects ingested or updated.
        """
        classes = target_classes or TARGET_CLASSES
        now_mjd = Time.now().mjd
        start_mjd = now_mjd - lookback_days
        total_ingested = 0

        # Log this ingestion run
        log_entry = IngestionLog(
            source="alerce_api",
            query_params={
                "lookback_days": lookback_days,
                "classes": classes,
                "start_mjd": start_mjd,
            },
        )
        session.add(log_entry)
        await session.flush()

        try:
            for class_name in classes:
                count = await self._ingest_class(
                    session, class_name, start_mjd, now_mjd
                )
                total_ingested += count
                logger.info(f"Ingested {count} objects for class {class_name}")

            log_entry.objects_ingested = total_ingested
            log_entry.status = "completed"
            log_entry.completed_at = datetime.now(timezone.utc)
            await session.commit()

        except Exception as e:
            log_entry.status = "failed"
            log_entry.error_message = str(e)
            log_entry.completed_at = datetime.now(timezone.utc)
            await session.commit()
            logger.error(f"Ingestion failed: {e}", exc_info=True)
            raise

        logger.info(f"Ingestion complete: {total_ingested} objects across {len(classes)} classes")
        return total_ingested

    async def _ingest_class(
        self,
        session: AsyncSession,
        class_name: str,
        start_mjd: float,
        end_mjd: float,
    ) -> int:
        """Ingest all objects of a given class within a time window."""
        count = 0

        try:
            objects_df = self.client.query_objects(
                classifier="lc_classifier",
                class_name=class_name,
                format="pandas",
                firstmjd=[start_mjd, end_mjd],
                page_size=100,
                probability=settings.min_classification_probability,
            )
        except Exception as e:
            logger.warning(f"Failed to query ALeRCE for {class_name}: {e}")
            return 0

        if objects_df is None or objects_df.empty:
            return 0

        for _, row in objects_df.iterrows():
            oid = row.get("oid")
            if not oid:
                continue

            # Upsert the object
            await self._upsert_object(session, row, class_name)

            # Pull and store the light curve
            await self._store_detections(session, oid)

            # Pull and store classification probabilities
            await self._store_probabilities(session, oid)

            count += 1

        await session.flush()
        return count

    async def _upsert_object(self, session: AsyncSession, row, class_name: str):
        """Insert or update an object record."""
        oid = row["oid"]
        ra = float(row.get("meanra", 0))
        dec_val = float(row.get("meandec", 0))

        first_mjd = row.get("firstmjd")
        last_mjd = row.get("lastmjd")

        stmt = pg_insert(Object).values(
            oid=oid,
            ra=ra,
            dec=dec_val,
            first_detection=mjd_to_datetime(first_mjd) if first_mjd else None,
            last_detection=mjd_to_datetime(last_mjd) if last_mjd else None,
            n_detections=int(row.get("ndet", 0)),
            classification=class_name,
            classification_probability=float(row.get("probability", 0)),
            classifier_name="lc_classifier",
            broker_source="alerce",
            alert_url=f"https://alerce.online/object/{oid}",
        ).on_conflict_do_update(
            index_elements=["oid"],
            set_={
                "last_detection": mjd_to_datetime(last_mjd) if last_mjd else None,
                "n_detections": int(row.get("ndet", 0)),
                "classification": class_name,
                "classification_probability": float(row.get("probability", 0)),
                "updated_at": datetime.now(timezone.utc),
            },
        )
        await session.execute(stmt)

        # Update PostGIS position
        await session.execute(
            text(
                "UPDATE objects SET position = ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography "
                "WHERE oid = :oid"
            ),
            {"ra": ra, "dec": dec_val, "oid": oid},
        )

    async def _store_detections(self, session: AsyncSession, oid: str):
        """Pull light curve from ALeRCE and store detection records."""
        try:
            detections_df = self.client.query_detections(oid, format="pandas", sort="mjd")
        except Exception as e:
            logger.warning(f"Failed to fetch detections for {oid}: {e}")
            return

        if detections_df is None or detections_df.empty:
            return

        # Check what we already have to avoid duplicates
        existing = await session.execute(
            select(Detection.candid).where(Detection.oid == oid)
        )
        existing_candids = {row[0] for row in existing.fetchall()}

        for _, det in detections_df.iterrows():
            candid = det.get("candid")
            if candid and int(candid) in existing_candids:
                continue

            mjd = float(det.get("mjd", 0))
            fid = det.get("fid")

            detection = Detection(
                oid=oid,
                candid=int(candid) if candid else None,
                mjd=mjd,
                detection_time=mjd_to_datetime(mjd),
                fid=int(fid) if fid else None,
                band=FILTER_MAP.get(int(fid)) if fid else None,
                magpsf=float(det["magpsf"]) if det.get("magpsf") else None,
                sigmapsf=float(det["sigmapsf"]) if det.get("sigmapsf") else None,
                ra=float(det["ra"]) if det.get("ra") else None,
                dec=float(det["dec"]) if det.get("dec") else None,
                isdiffpos=str(det.get("isdiffpos", "")),
                rb=float(det["rb"]) if det.get("rb") else None,
            )
            session.add(detection)

    async def _store_probabilities(self, session: AsyncSession, oid: str):
        """Pull classification probabilities and store them."""
        try:
            probs = self.client.query_probabilities(oid)
        except Exception:
            return

        if not probs:
            return

        # probs is a list of dicts with classifier_name, class_name, probability, ranking
        for prob in probs if isinstance(probs, list) else [probs]:
            if not isinstance(prob, dict):
                continue

            stmt = pg_insert(ClassificationProbability).values(
                oid=oid,
                classifier_name=prob.get("classifier_name", "unknown"),
                classifier_version=prob.get("classifier_version"),
                class_name=prob.get("class_name", "unknown"),
                probability=float(prob.get("probability", 0)),
                ranking=prob.get("ranking"),
            ).on_conflict_do_update(
                constraint="classification_probabilities_oid_classifier_name_class_name_key",
                set_={"probability": float(prob.get("probability", 0))},
            )

            try:
                await session.execute(stmt)
            except Exception:
                pass  # Skip individual probability conflicts
