"""
CHIME/FRB Ingestion Service.

Pulls public Fast Radio Burst (FRB) detections from the CHIME/FRB
public catalog and stores them in the local database.

Catalog field reference (from CHIME/FRB Catalog 1, CHIME/FRB Collaboration 2021):
  tns_name    - Transient Name Server identifier (e.g. "FRB 20121102A")
  ra          - Right Ascension (degrees, J2000)
  dec         - Declination (degrees, J2000)
  bonsai_dm   - Dispersion measure (pc/cm^3, BONSAI pipeline value)
  mjd_400     - MJD of detection (referenced to 400 MHz DM=0)

CHIME is a Canadian telescope at DRAO Penticton, British Columbia.
Its public catalog requires no authentication.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from astropy.time import Time
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Detection, IngestionLog, Object

logger = logging.getLogger(__name__)

# Primary catalog endpoint (CHIME/FRB Catalog 1, JSON format)
CHIME_API_URLS = [
    "https://www.chime-frb.ca/api/1/sources/?format=json&page_size=1000",
    "https://www.chime-frb.ca/api/1/sources/",
]

# Fallback: direct CSV download (mirrored by open-data project)
CHIME_CSV_URL = "https://raw.githubusercontent.com/CHIMEFRB/frb-master/main/data/catalog1.csv"

# TNS name pattern: "FRB YYYYMMDDX" or "FRB YYYYabc"
_TNS_SPACE_RE = re.compile(r"\s+")


def _normalize_oid(tns_name: str) -> str:
    """
    Normalize a TNS name to a compact OID string.
    "FRB 20121102A" → "FRB20121102a"
    "FRB 2020xyz"   → "FRB2020xyz"
    """
    oid = _TNS_SPACE_RE.sub("", tns_name)  # strip spaces
    return oid[:-1] + oid[-1].lower() if oid and oid[-1].isupper() else oid.lower() if len(oid) <= 4 else oid[:3] + oid[3:].lower()


def _mjd_to_datetime(mjd: float) -> datetime:
    return Time(mjd, format="mjd").to_datetime(timezone=timezone.utc)


class ChimeFRBIngestionService:
    """Pulls CHIME/FRB catalog detections and stores them locally."""

    async def ingest(self, session: AsyncSession) -> int:
        """
        Fetch CHIME/FRB catalog and upsert into the objects table.

        Returns:
            Number of FRBs ingested (0 on failure).
        """
        log_entry = IngestionLog(
            source="chimefrb_catalog",
            query_params={"catalog": "CHIME/FRB Catalog 1"},
        )
        session.add(log_entry)
        await session.flush()

        try:
            frbs = await self._fetch_catalog()
            if not frbs:
                log_entry.status = "completed"
                log_entry.objects_ingested = 0
                log_entry.completed_at = datetime.now(timezone.utc)
                await session.commit()
                return 0

            count = 0
            for frb in frbs:
                ingested = await self._upsert_frb(session, frb)
                if ingested:
                    count += 1

            await session.flush()

            log_entry.objects_ingested = count
            log_entry.status = "completed"
            log_entry.completed_at = datetime.now(timezone.utc)
            await session.commit()

            logger.info(f"CHIME/FRB ingestion complete: {count} FRBs upserted")
            return count

        except Exception as e:
            log_entry.status = "failed"
            log_entry.error_message = str(e)
            log_entry.completed_at = datetime.now(timezone.utc)
            await session.commit()
            logger.error(f"CHIME/FRB ingestion failed: {e}", exc_info=True)
            return 0

    async def _fetch_catalog(self) -> list[dict]:
        """Try each known endpoint; return parsed list or empty list."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Try JSON API endpoints
            for url in CHIME_API_URLS:
                try:
                    resp = await client.get(url, follow_redirects=True)
                    if resp.status_code == 200:
                        data = resp.json()
                        # API may return {"results": [...]} or a plain list
                        if isinstance(data, list):
                            logger.info(f"Fetched {len(data)} FRBs from {url}")
                            return data
                        if isinstance(data, dict):
                            results = data.get("results") or data.get("sources") or data.get("frbs") or []
                            if results:
                                logger.info(f"Fetched {len(results)} FRBs from {url}")
                                return results
                except Exception as e:
                    logger.warning(f"CHIME API {url} failed: {e}")

            # Fallback: CSV
            try:
                resp = await client.get(CHIME_CSV_URL, follow_redirects=True)
                if resp.status_code == 200:
                    return self._parse_csv(resp.text)
            except Exception as e:
                logger.warning(f"CHIME CSV fallback failed: {e}")

        logger.error("CHIME/FRB catalog unreachable from all sources; returning 0 FRBs")
        return []

    def _parse_csv(self, csv_text: str) -> list[dict]:
        """Parse the CHIME catalog CSV into a list of dicts."""
        import io
        import csv

        reader = csv.DictReader(io.StringIO(csv_text))
        rows = []
        for row in reader:
            rows.append(row)
        logger.info(f"Parsed {len(rows)} FRBs from CHIME CSV")
        return rows

    def _extract_fields(self, frb: dict) -> Optional[dict]:
        """
        Extract and validate fields from a raw CHIME catalog entry.

        Handles both JSON API responses and CSV-parsed dicts.
        Returns None if mandatory fields are missing.
        """
        # TNS name / OID
        tns_name = (
            frb.get("tns_name")
            or frb.get("frb_name")
            or frb.get("name")
            or frb.get("id")
        )
        if not tns_name:
            return None

        oid = _normalize_oid(str(tns_name))
        if not oid.startswith("FRB"):
            oid = f"FRB{oid}"

        # Position
        try:
            ra = float(frb.get("ra") or frb.get("ra_deg") or 0)
            dec = float(frb.get("dec") or frb.get("dec_deg") or 0)
        except (TypeError, ValueError):
            return None

        if ra == 0.0 and dec == 0.0:
            return None  # Invalid coordinates

        # Dispersion measure
        dm_raw = frb.get("bonsai_dm") or frb.get("dm") or frb.get("dispersion_measure")
        dm = None
        if dm_raw is not None:
            try:
                dm = float(dm_raw)
            except (TypeError, ValueError):
                pass

        # Detection time (MJD at 400 MHz)
        mjd_raw = frb.get("mjd_400") or frb.get("mjd") or frb.get("detection_mjd")
        detection_time = None
        mjd = None
        if mjd_raw is not None:
            try:
                mjd = float(mjd_raw)
                detection_time = _mjd_to_datetime(mjd)
            except (TypeError, ValueError):
                pass

        return {
            "oid": oid,
            "ra": ra,
            "dec": dec,
            "dispersion_measure": dm,
            "mjd": mjd,
            "detection_time": detection_time,
        }

    async def _upsert_frb(self, session: AsyncSession, frb: dict) -> bool:
        """Insert or update one FRB record. Returns True if successful."""
        fields = self._extract_fields(frb)
        if fields is None:
            return False

        oid = fields["oid"]
        ra = fields["ra"]
        dec = fields["dec"]
        detection_time = fields["detection_time"]

        stmt = pg_insert(Object).values(
            oid=oid,
            ra=ra,
            dec=dec,
            first_detection=detection_time,
            last_detection=detection_time,
            n_detections=1,
            classification="FRB",
            classification_probability=1.0,
            dispersion_measure=fields["dispersion_measure"],
            broker_source="chimefrb",
            alert_url=f"https://www.chime-frb.ca/catalog",
        ).on_conflict_do_update(
            index_elements=["oid"],
            set_={
                "dispersion_measure": fields["dispersion_measure"],
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
            {"ra": ra, "dec": dec, "oid": oid},
        )

        # Store detection record if we have a time
        if fields["mjd"] and fields["detection_time"]:
            det_stmt = pg_insert(Detection).values(
                oid=oid,
                mjd=fields["mjd"],
                detection_time=fields["detection_time"],
            ).on_conflict_do_nothing()
            try:
                await session.execute(det_stmt)
            except Exception:
                pass

        return True
