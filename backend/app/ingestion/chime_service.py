"""
CHIME/FRB Ingestion Service.

Pulls public Fast Radio Burst (FRB) detections from the CHIME/FRB
Catalog 1 (CHIME/FRB Collaboration 2021, ApJS 257, 59) via the CDS
VizieR astronomical data service and stores them in the local database.

VizieR catalog column mapping (J/ApJS/257/59/table2):
  Name      - TNS name (e.g. "FRB20180725A")
  RAJ2000   - Right Ascension (degrees, J2000)
  DEJ2000   - Declination (degrees, J2000)
  e_RAJ2000 - Right Ascension 1-sigma uncertainty (degrees; see catalog §3.2)
  e_DEJ2000 - Declination 1-sigma uncertainty (degrees; see catalog §3.2)
  DM        - Dispersion measure (pc/cm³, BONSAI pipeline value)
  MJD400    - MJD of detection (referenced to 400.1953125 MHz, DM=0)

CHIME/FRB Catalog 1 positions are beam-S/N derived and only accurate to
~arcminutes (the published e_RAJ2000 / e_DEJ2000 range up to ~0.4-0.5 deg),
so these uncertainties are persisted and must gate any cross-match radius.

CHIME is a Canadian telescope at DRAO Penticton, British Columbia.
"""

import io
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from astropy.io.votable import parse_single_table
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Detection, IngestionLog, Object

logger = logging.getLogger(__name__)

# CDS VizieR: CHIME/FRB Catalog 1 (CHIME/FRB Collaboration 2021, ApJS 257, 59)
VIZIER_URL = (
    "https://vizier.cds.unistra.fr/viz-bin/votable"
    "?-source=J/ApJS/257/59/table2&-out.all&-out.max=unlimited"
)


def _mjd_to_datetime(mjd: float) -> datetime:
    from astropy.time import Time
    return Time(mjd, format="mjd").to_datetime(timezone=timezone.utc)


class ChimeFRBIngestionService:
    """Pulls CHIME/FRB catalog detections and stores them locally."""

    async def ingest(self, session: AsyncSession) -> int:
        """
        Fetch CHIME/FRB catalog from VizieR and upsert into the objects table.

        Returns:
            Number of FRBs ingested (0 on failure).
        """
        log_entry = IngestionLog(
            source="chimefrb_catalog",
            query_params={"catalog": "CHIME/FRB Catalog 1 via VizieR"},
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
                if await self._upsert_frb(session, frb):
                    count += 1

            await session.flush()

            log_entry.objects_ingested = count
            log_entry.status = "completed"
            log_entry.completed_at = datetime.now(timezone.utc)
            await session.commit()

            logger.info("CHIME/FRB ingestion complete: %d FRBs upserted", count)
            return count

        except Exception as e:
            log_entry.status = "failed"
            log_entry.error_message = str(e)
            log_entry.completed_at = datetime.now(timezone.utc)
            await session.commit()
            logger.error("CHIME/FRB ingestion failed: %s", e, exc_info=True)
            return 0

    async def _fetch_catalog(self) -> list[dict]:
        """
        Fetch CHIME/FRB Catalog 1 from VizieR as a VOTable and parse it.

        Returns a list of row dicts with keys: Name, RAJ2000, DEJ2000, DM, MJD400.
        Returns an empty list if VizieR is unreachable or the response is invalid.
        """
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(VIZIER_URL, follow_redirects=True)
                resp.raise_for_status()
                votable_bytes = resp.content
        except Exception as e:
            logger.error("CHIME/FRB VizieR fetch failed: %s", e)
            return []

        try:
            table = parse_single_table(io.BytesIO(votable_bytes))
            df = table.to_table().to_pandas()
        except Exception as e:
            logger.error("CHIME/FRB VOTable parse failed: %s", e)
            return []

        rows = df.to_dict(orient="records")
        logger.info("Fetched %d FRBs from VizieR (CHIME/FRB Catalog 1)", len(rows))
        return rows

    def _extract_fields(self, row: dict) -> Optional[dict]:
        """
        Extract and validate fields from a VizieR VOTable row.

        VizieR column names: Name, RAJ2000, DEJ2000, DM, MJD400.
        Returns None if mandatory fields are missing or invalid.
        """
        # TNS name → OID
        tns_name = row.get("Name")
        if not tns_name or str(tns_name).strip() in ("", "nan"):
            return None

        tns_name = str(tns_name).strip()
        # VizieR Name column: "FRB20180725A" — already compact, no spaces
        oid = tns_name if tns_name.startswith("FRB") else f"FRB{tns_name}"

        # Position
        try:
            ra = float(row["RAJ2000"])
            dec = float(row["DEJ2000"])
        except (KeyError, TypeError, ValueError):
            return None

        if ra == 0.0 and dec == 0.0:
            return None

        # Localization uncertainty (degrees, 1-sigma). Optional — some rows may
        # lack an error estimate; store None rather than a false zero.
        def _opt_float(key: str) -> Optional[float]:
            raw = row.get(key)
            if raw is None:
                return None
            try:
                val = float(raw)
            except (TypeError, ValueError):
                return None
            # VizieR uses NaN for absent numeric cells after pandas conversion.
            return None if val != val else val

        ra_err_deg = _opt_float("e_RAJ2000")
        dec_err_deg = _opt_float("e_DEJ2000")

        # Dispersion measure
        dm = None
        dm_raw = row.get("DM")
        if dm_raw is not None:
            try:
                dm = float(dm_raw)
            except (TypeError, ValueError):
                pass

        # Detection time (MJD at 400 MHz)
        mjd = None
        detection_time = None
        mjd_raw = row.get("MJD400")
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
            "ra_err_deg": ra_err_deg,
            "dec_err_deg": dec_err_deg,
            "dispersion_measure": dm,
            "mjd": mjd,
            "detection_time": detection_time,
        }

    async def _upsert_frb(self, session: AsyncSession, row: dict) -> bool:
        """Insert or update one FRB record. Returns True if successful."""
        fields = self._extract_fields(row)
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
            ra_err_deg=fields["ra_err_deg"],
            dec_err_deg=fields["dec_err_deg"],
            first_detection=detection_time,
            last_detection=detection_time,
            n_detections=1,
            classification="FRB",
            classification_probability=1.0,
            dispersion_measure=fields["dispersion_measure"],
            broker_source="chimefrb",
            alert_url="https://www.chime-frb.ca/catalog",
        ).on_conflict_do_update(
            index_elements=["oid"],
            set_={
                "ra_err_deg": fields["ra_err_deg"],
                "dec_err_deg": fields["dec_err_deg"],
                "dispersion_measure": fields["dispersion_measure"],
                "updated_at": datetime.now(timezone.utc),
            },
        )
        await session.execute(stmt)

        await session.execute(
            text(
                "UPDATE objects SET position = ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography "
                "WHERE oid = :oid"
            ),
            {"ra": ra, "dec": dec, "oid": oid},
        )

        if fields["mjd"] and detection_time:
            det_stmt = pg_insert(Detection).values(
                oid=oid,
                mjd=fields["mjd"],
                detection_time=detection_time,
            ).on_conflict_do_nothing()
            try:
                await session.execute(det_stmt)
            except Exception:
                pass

        return True
