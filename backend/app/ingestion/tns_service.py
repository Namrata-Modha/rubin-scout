"""
IAU Transient Name Server (TNS) Ingestion Service.

TNS is the official IAU registry for new astronomical transients.
Discoveries from ZTF, ATLAS, LSST, GOTO, WFST all land here first.

Two ingestion modes:
1. Daily CSV staging (requires TNS user credentials in env vars)
2. Search API (requires TNS bot credentials in env vars)

All credentials loaded from environment variables via Settings.
Nothing is hardcoded. See .env.example for the variable names.
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

# TNS URLs
TNS_BASE_URL = "https://www.wis-tns.org"
TNS_API_URL = f"{TNS_BASE_URL}/api"
TNS_CSV_BASE = f"{TNS_BASE_URL}/system/files/tns_public_objects"

# Map TNS classification types to our internal taxonomy
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


def _map_tns_type(tns_type: str) -> Optional[str]:
    if not tns_type or tns_type.strip() == "":
        return None
    return TNS_TYPE_MAP.get(tns_type.strip())


def _parse_tns_ra(ra_str: str) -> Optional[float]:
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


def _parse_tns_dec(dec_str: str) -> Optional[float]:
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


def _parse_tns_date(date_str: str) -> Optional[datetime]:
    if not date_str or date_str.strip() == "":
        return None
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"]:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class TNSIngestionService:
    """
    Pulls transient discoveries from the IAU Transient Name Server.
    All credentials loaded from environment variables via Settings.
    """

    def __init__(self):
        self.api_key = settings.tns_api_key
        self.bot_id = settings.tns_bot_id
        self.bot_name = settings.tns_bot_name

    def _get_headers(self) -> dict:
        """
        Build TNS user-agent header from environment variables.

        TNS requires a specific user-agent for ALL requests including CSV downloads.
        Uses bot credentials if available, falls back to user credentials.
        Returns empty dict if no credentials configured (requests will fail with 403).
        """
        if settings.has_tns_bot:
            ua = f'tns_marker{{"tns_id": {settings.tns_bot_id}, "type": "bot", "name": "{settings.tns_bot_name}"}}'
        elif settings.has_tns_user:
            ua = f'tns_marker{{"tns_id": {settings.tns_user_id}, "type": "user", "name": "{settings.tns_user_name}"}}'
        else:
            logger.warning("No TNS credentials configured. Set TNS_USER_ID and TNS_USER_NAME in .env")
            return {}
        return {"User-Agent": ua}

    # ------------------------------------------------------------------
    # CSV-based ingestion (requires at minimum TNS user credentials)
    # ------------------------------------------------------------------

    async def ingest_from_daily_csv(
        self,
        session: AsyncSession,
        date: Optional[datetime] = None,
    ) -> int:
        """
        Download and ingest the TNS daily delta CSV.
        Requires TNS_USER_ID and TNS_USER_NAME in environment variables.
        """
        headers = self._get_headers()
        if not headers:
            logger.warning("Skipping TNS CSV ingestion: no credentials configured")
            return 0

        if date is None:
            date = datetime.now(timezone.utc) - timedelta(days=1)

        date_str = date.strftime("%Y%m%d")
        csv_url = f"{TNS_CSV_BASE}/tns_public_objects_{date_str}.csv.zip"

        logger.info(f"Downloading TNS daily CSV for {date_str}")

        log_entry = IngestionLog(
            source="tns_csv",
            query_params={"date": date_str, "url": csv_url},
        )
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
                    logger.error(f"TNS returned {response.status_code}. Check TNS_USER_ID and TNS_USER_NAME in .env")
                    log_entry.status = "auth_failed"
                    log_entry.error_message = f"HTTP {response.status_code}: check TNS credentials in .env"
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

    async def _process_csv_zip(self, session: AsyncSession, zip_bytes: bytes) -> int:
        count = 0
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if not name.endswith(".csv"):
                    continue
                with zf.open(name) as f:
                    text_stream = io.TextIOWrapper(f, encoding="utf-8")
                    reader = csv.DictReader(text_stream)
                    for row in reader:
                        ingested = await self._upsert_tns_object(session, row)
                        if ingested:
                            count += 1
                        if count % 50 == 0 and count > 0:
                            await session.flush()
        await session.flush()
        return count

    async def _upsert_tns_object(self, session: AsyncSession, row: dict) -> bool:
        """Upsert a single TNS object from a CSV row."""
        prefix = (row.get("name_prefix") or "").strip()
        name = (row.get("name") or "").strip()
        if not name:
            return False

        tns_name = f"{prefix}{name}" if prefix else name
        oid = tns_name

        ra = _parse_tns_ra(row.get("ra", ""))
        dec = _parse_tns_dec(row.get("declination", ""))
        if ra is None or dec is None:
            return False

        tns_type = (row.get("type") or "").strip()
        classification = _map_tns_type(tns_type)
        discovery_date = _parse_tns_date(row.get("discoverydate", ""))

        discovery_mag = None
        try:
            mag_str = (row.get("discoverymag") or "").strip()
            if mag_str:
                discovery_mag = float(mag_str)
        except ValueError:
            pass

        redshift = None
        try:
            z_str = (row.get("redshift") or "").strip()
            if z_str:
                redshift = float(z_str)
        except ValueError:
            pass

        confidence = 1.0 if classification else 0.5

        stmt = pg_insert(Object).values(
            oid=oid,
            ra=ra,
            dec=dec,
            first_detection=discovery_date,
            last_detection=discovery_date,
            n_detections=1,
            classification=classification,
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

    # ------------------------------------------------------------------
    # API-based ingestion (requires TNS bot credentials)
    # ------------------------------------------------------------------

    async def search_recent(
        self,
        session: AsyncSession,
        days_back: int = 7,
        classified_only: bool = False,
    ) -> int:
        """Search TNS API. Requires TNS_API_KEY, TNS_BOT_ID, TNS_BOT_NAME in env."""
        if not settings.has_tns_bot:
            logger.warning("TNS API search requires bot credentials. Set TNS_API_KEY, TNS_BOT_ID, TNS_BOT_NAME in .env")
            return 0

        search_params = {
            "discovered_period_value": str(days_back),
            "discovered_period_units": "days",
            "unclassified_at": 0 if classified_only else 1,
            "classified_sne": 1,
            "num_page": 50,
            "page": 0,
        }

        log_entry = IngestionLog(source="tns_api", query_params=search_params)
        session.add(log_entry)
        await session.flush()

        total_ingested = 0

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                form_data = {
                    "api_key": settings.tns_api_key,
                    "data": json.dumps(search_params),
                }
                response = await client.post(
                    f"{TNS_API_URL}/get/search",
                    data=form_data,
                    headers=self._get_headers(),
                )
                response.raise_for_status()
                result = response.json()

            reply = result.get("data", {}).get("reply", [])

            for obj_summary in reply:
                objname = obj_summary.get("objname", "")
                prefix = obj_summary.get("prefix", "AT")
                if not objname:
                    continue

                detail = await self._get_object_detail(objname)
                if detail:
                    ingested = await self._upsert_from_api(session, detail, prefix)
                    if ingested:
                        total_ingested += 1

            log_entry.objects_ingested = total_ingested
            log_entry.status = "completed"
            log_entry.completed_at = datetime.now(timezone.utc)
            await session.commit()

        except Exception as e:
            log_entry.status = "failed"
            log_entry.error_message = str(e)[:500]
            log_entry.completed_at = datetime.now(timezone.utc)
            await session.commit()
            logger.error(f"TNS API search failed: {e}", exc_info=True)

        return total_ingested

    async def _get_object_detail(self, objname: str) -> Optional[dict]:
        if not settings.has_tns_bot:
            return None
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                form_data = {
                    "api_key": settings.tns_api_key,
                    "data": json.dumps({
                        "objname": objname, "objid": "",
                        "photometry": "0", "spectra": "0",
                    }),
                }
                response = await client.post(
                    f"{TNS_API_URL}/get/object",
                    data=form_data,
                    headers=self._get_headers(),
                )
                response.raise_for_status()
                result = response.json()
            return result.get("data", {}).get("reply", {})
        except Exception as e:
            logger.warning(f"TNS detail fetch failed for {objname}: {e}")
            return None

    async def _upsert_from_api(self, session: AsyncSession, detail: dict, prefix: str) -> bool:
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

        hostname = detail.get("hostname", "")
        confidence = 1.0 if classification else 0.5

        stmt = pg_insert(Object).values(
            oid=oid, ra=ra, dec=dec,
            first_detection=discovery_date, last_detection=discovery_date,
            n_detections=1, classification=classification,
            classification_probability=confidence,
            sub_classification=tns_type if tns_type else None,
            classifier_name="spectroscopic" if classification else None,
            broker_source="tns",
            alert_url=f"https://www.wis-tns.org/object/{objname}",
            host_galaxy_name=hostname if hostname else None,
            host_galaxy_redshift=redshift,
        ).on_conflict_do_update(
            index_elements=["oid"],
            set_={
                "classification": classification or Object.classification,
                "classification_probability": confidence,
                "sub_classification": tns_type if tns_type else Object.sub_classification,
                "host_galaxy_name": hostname if hostname else Object.host_galaxy_name,
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

    # ------------------------------------------------------------------
    # Seed
    # ------------------------------------------------------------------

    async def seed_recent_days(self, session: AsyncSession, days: int = 7) -> int:
        """Seed database with TNS discoveries from last N days."""
        if not self._get_headers():
            logger.error("Cannot seed TNS: no credentials. Set TNS_USER_ID and TNS_USER_NAME in .env")
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
