"""
IAU Transient Name Server (TNS) Ingestion Service.
All credentials from environment variables. Nothing hardcoded.
"""

import csv
import io
import json
import logging
import zipfile
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.models import Object, IngestionLog

logger = logging.getLogger(__name__)
settings = get_settings()

TNS_BASE_URL = "https://www.wis-tns.org"
TNS_API_URL = f"{TNS_BASE_URL}/api"
TNS_CSV_BASE = f"{TNS_BASE_URL}/system/files/tns_public_objects"

TNS_TYPE_MAP = {
    "SN Ia": "SNIa", "SN Ia-91T-like": "SNIa", "SN Ia-91bg-like": "SNIa",
    "SN Ia-CSM": "SNIa", "SN Ia-pec": "SNIa", "SN Ia-SC": "SNIa",
    "SN II": "SNII", "SN IIP": "SNII", "SN IIL": "SNII",
    "SN IIn": "SNII", "SN IIn-pec": "SNII", "SN II-pec": "SNII",
    "SN Ib": "SNIbc", "SN Ic": "SNIbc", "SN Ic-BL": "SNIbc",
    "SN Ib/c": "SNIbc", "SN Ib-pec": "SNIbc", "SN Ic-pec": "SNIbc", "SN I": "SNIbc",
    "SLSN-I": "SLSN", "SLSN-II": "SLSN", "SLSN-R": "SLSN",
    "TDE": "TDE", "AGN": "AGN", "Nova": "CV/Nova", "CV": "CV/Nova",
    "Kilonova": "KN", "LBV": "CV/Nova",
}


def _map_tns_type(tns_type):
    if not tns_type or tns_type.strip() == "":
        return None
    return TNS_TYPE_MAP.get(tns_type.strip())


def _parse_tns_ra(ra_str):
    if not ra_str or ra_str.strip() == "":
        return None
    try:
        return float(ra_str)
    except ValueError:
        pass
    try:
        parts = ra_str.strip().split(":")
        if len(parts) == 3:
            h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
            return (h + m / 60 + s / 3600) * 15
    except (ValueError, IndexError):
        pass
    return None


def _parse_tns_dec(dec_str):
    if not dec_str or dec_str.strip() == "":
        return None
    try:
        return float(dec_str)
    except ValueError:
        pass
    try:
        dec_str = dec_str.strip()
        sign = -1 if dec_str.startswith("-") else 1
        dec_str = dec_str.lstrip("+-")
        parts = dec_str.split(":")
        if len(parts) == 3:
            d, m, s = float(parts[0]), float(parts[1]), float(parts[2])
            return sign * (d + m / 60 + s / 3600)
    except (ValueError, IndexError):
        pass
    return None


def _parse_tns_date(date_str):
    if not date_str or date_str.strip() == "":
        return None
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"]:
        try:
            return datetime.strptime(date_str.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _clean(val):
    """Strip whitespace and surrounding quotes from CSV values."""
    if val is None:
        return ""
    return val.strip().strip('"')


class TNSIngestionService:
    def __init__(self):
        self.api_key = settings.tns_api_key
        self.bot_id = settings.tns_bot_id
        self.bot_name = settings.tns_bot_name

    def _get_headers(self):
        if settings.has_tns_bot:
            ua = f'tns_marker{{"tns_id": {settings.tns_bot_id}, "type": "bot", "name": "{settings.tns_bot_name}"}}'
        elif settings.has_tns_user:
            ua = f'tns_marker{{"tns_id": {settings.tns_user_id}, "type": "user", "name": "{settings.tns_user_name}"}}'
        else:
            logger.warning("No TNS credentials. Set TNS_USER_ID + TNS_USER_NAME in .env")
            return {}
        return {"User-Agent": ua}

    async def ingest_from_daily_csv(self, session, date=None):
        headers = self._get_headers()
        if not headers:
            return 0

        if date is None:
            date = datetime.now(timezone.utc) - timedelta(days=1)

        date_str = date.strftime("%Y%m%d")
        csv_url = f"{TNS_CSV_BASE}/tns_public_objects_{date_str}.csv.zip"
        logger.info(f"Downloading TNS daily CSV for {date_str}")

        log_entry = IngestionLog(source="tns_csv", query_params={"date": date_str, "url": csv_url})
        session.add(log_entry)
        await session.flush()

        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                response = await client.get(csv_url, headers=headers)

                if response.status_code == 404:
                    logger.warning(f"TNS CSV not found for {date_str}")
                    log_entry.status = "no_data"
                    log_entry.completed_at = datetime.now(timezone.utc)
                    await session.commit()
                    return 0

                if response.status_code in (401, 403):
                    logger.error(f"TNS {response.status_code}. Check TNS credentials in .env")
                    log_entry.status = "auth_failed"
                    log_entry.error_message = f"HTTP {response.status_code}"
                    log_entry.completed_at = datetime.now(timezone.utc)
                    await session.commit()
                    return 0

                response.raise_for_status()

            count = await self._process_csv_zip(session, response.content)
            log_entry.objects_ingested = count
            log_entry.status = "completed"
            log_entry.completed_at = datetime.now(timezone.utc)
            await session.commit()
            logger.info(f"TNS CSV ingestion: {count} objects from {date_str}")
            return count

        except Exception as e:
            log_entry.status = "failed"
            log_entry.error_message = str(e)[:500]
            log_entry.completed_at = datetime.now(timezone.utc)
            await session.commit()
            logger.error(f"TNS CSV ingestion failed: {e}", exc_info=True)
            return 0

    async def _process_csv_zip(self, session, zip_bytes):
        """
        Parse TNS CSV zip. TNS CSV format:
          Line 1: date range (e.g. "2026-04-05 00:00:00 - 23:59:59")
          Line 2: column headers ("objid","name_prefix","name","ra",...)
          Line 3+: data rows

        We scan for the header line containing "objid" and skip everything before it.
        """
        count = 0
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if not name.endswith(".csv"):
                    continue

                raw = zf.read(name).decode("utf-8", errors="replace")
                lines = raw.splitlines(keepends=True)

                # Find the header line
                header_idx = None
                for i, line in enumerate(lines):
                    if "objid" in line.lower():
                        header_idx = i
                        break

                if header_idx is None:
                    logger.warning(f"No header row with 'objid' found in {name}")
                    if lines:
                        logger.debug(f"First line of {name}: {lines[0][:200]}")
                    continue

                # Reconstruct CSV from header line onward
                csv_text = "".join(lines[header_idx:])
                reader = csv.DictReader(io.StringIO(csv_text))

                logger.info(f"CSV columns: {reader.fieldnames}")

                for row in reader:
                    ingested = await self._upsert_tns_object(session, row)
                    if ingested:
                        count += 1
                    if count % 50 == 0 and count > 0:
                        await session.flush()

        await session.flush()
        return count

    async def _upsert_tns_object(self, session, row):
        prefix = _clean(row.get("name_prefix"))
        name = _clean(row.get("name"))
        if not name:
            return False

        tns_name = f"{prefix}{name}" if prefix else name
        oid = tns_name

        ra = _parse_tns_ra(_clean(row.get("ra")))
        dec = _parse_tns_dec(_clean(row.get("declination")))
        if ra is None or dec is None:
            return False

        tns_type = _clean(row.get("type"))
        classification = _map_tns_type(tns_type)
        discovery_date = _parse_tns_date(_clean(row.get("discoverydate")))

        redshift = None
        try:
            z_str = _clean(row.get("redshift"))
            if z_str:
                redshift = float(z_str)
        except ValueError:
            pass

        confidence = 1.0 if classification else 0.5

        stmt = pg_insert(Object).values(
            oid=oid, ra=ra, dec=dec,
            first_detection=discovery_date, last_detection=discovery_date,
            n_detections=1, classification=classification,
            classification_probability=confidence,
            sub_classification=tns_type if tns_type else None,
            classifier_name="spectroscopic" if classification else None,
            broker_source="tns",
            alert_url=f"https://www.wis-tns.org/object/{name}",
            host_galaxy_redshift=redshift,
        ).on_conflict_do_update(
            index_elements=["oid"],
            set_={
                "classification": classification or Object.classification,
                "classification_probability": confidence,
                "sub_classification": tns_type if tns_type else Object.sub_classification,
                "classifier_name": "spectroscopic" if classification else Object.classifier_name,
                "host_galaxy_redshift": redshift if redshift else Object.host_galaxy_redshift,
                "updated_at": datetime.now(timezone.utc),
            },
        )
        await session.execute(stmt)

        await session.execute(
            text(
                "UPDATE objects SET position = ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography "
                "WHERE oid = :oid AND position IS NULL"
            ),
            {"ra": ra, "dec": dec, "oid": oid},
        )
        return True

    async def search_recent(self, session, days_back=7, classified_only=False):
        if not settings.has_tns_bot:
            logger.warning("TNS API search requires bot credentials in .env")
            return 0
        total = 0
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                form_data = {
                    "api_key": settings.tns_api_key,
                    "data": json.dumps({
                        "discovered_period_value": str(days_back),
                        "discovered_period_units": "days",
                        "unclassified_at": 0 if classified_only else 1,
                        "classified_sne": 1, "num_page": 50, "page": 0,
                    }),
                }
                response = await client.post(f"{TNS_API_URL}/get/search", data=form_data, headers=self._get_headers())
                response.raise_for_status()
                result = response.json()
            for obj in result.get("data", {}).get("reply", []):
                objname = obj.get("objname", "")
                if not objname:
                    continue
                detail = await self._get_object_detail(objname)
                if detail:
                    if await self._upsert_from_api(session, detail, obj.get("prefix", "AT")):
                        total += 1
            await session.commit()
        except Exception as e:
            logger.error(f"TNS API search failed: {e}", exc_info=True)
        return total

    async def _get_object_detail(self, objname):
        if not settings.has_tns_bot:
            return None
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    f"{TNS_API_URL}/get/object",
                    data={"api_key": settings.tns_api_key, "data": json.dumps({"objname": objname, "objid": "", "photometry": "0", "spectra": "0"})},
                    headers=self._get_headers(),
                )
                response.raise_for_status()
                return response.json().get("data", {}).get("reply", {})
        except Exception as e:
            logger.warning(f"TNS detail fetch failed for {objname}: {e}")
            return None

    async def _upsert_from_api(self, session, detail, prefix):
        objname = detail.get("objname", "")
        if not objname:
            return False
        oid = f"{prefix}{objname}"
        ra = _parse_tns_ra(str(detail.get("ra", "")))
        dec = _parse_tns_dec(str(detail.get("dec", "")))
        if ra is None or dec is None:
            return False
        tns_type = detail.get("type", "")
        classification = _map_tns_type(tns_type)
        discovery_date = _parse_tns_date(detail.get("discoverydate", ""))
        redshift = None
        try:
            z = detail.get("redshift")
            if z:
                redshift = float(z)
        except (ValueError, TypeError):
            pass
        confidence = 1.0 if classification else 0.5
        stmt = pg_insert(Object).values(
            oid=oid, ra=ra, dec=dec, first_detection=discovery_date, last_detection=discovery_date,
            n_detections=1, classification=classification, classification_probability=confidence,
            sub_classification=tns_type if tns_type else None,
            classifier_name="spectroscopic" if classification else None, broker_source="tns",
            alert_url=f"https://www.wis-tns.org/object/{objname}",
            host_galaxy_name=detail.get("hostname") or None, host_galaxy_redshift=redshift,
        ).on_conflict_do_update(
            index_elements=["oid"],
            set_={"classification": classification or Object.classification, "classification_probability": confidence,
                   "sub_classification": tns_type if tns_type else Object.sub_classification,
                   "updated_at": datetime.now(timezone.utc)},
        )
        await session.execute(stmt)
        await session.execute(text(
            "UPDATE objects SET position = ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography WHERE oid = :oid AND position IS NULL"
        ), {"ra": ra, "dec": dec, "oid": oid})
        return True

    async def seed_recent_days(self, session, days=7):
        if not self._get_headers():
            logger.error("Cannot seed TNS: set TNS_USER_ID + TNS_USER_NAME in .env")
            return 0
        total = 0
        today = datetime.now(timezone.utc)
        for d in range(1, days + 1):
            date = today - timedelta(days=d)
            count = await self.ingest_from_daily_csv(session, date)
            total += count
            logger.info(f"Day {date.strftime('%Y-%m-%d')}: {count} objects")
        logger.info(f"TNS seed complete: {total} objects from last {days} days")
        return total