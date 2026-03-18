"""
Seed the database with a batch of real alerts from ALeRCE.

This pulls a sample of recent transients across multiple classes
so you have real data to work with immediately. Run once after
setting up the database.

Usage:
    cd backend
    python -m scripts.seed_database
"""

import asyncio
import logging
import sys
import os

# sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_dir = os.path.join(project_root, "backend")
sys.path.insert(0, backend_dir)

from app.database import async_session
from app.ingestion.alerce_service import AlerceIngestionService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger(__name__)


async def seed():
    logger.info("Seeding database with real ALeRCE alerts...")

    from alerce.core import Alerce
    from astropy.time import Time
    from datetime import datetime, timezone
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.models.models import Object, Detection

    client = Alerce()
    total = 0

    classes = ["SNIa", "SNII", "AGN", "TDE"]

    async with async_session() as session:
        for cls in classes:
            try:
                objects_df = client.query_objects(
                    classifier="lc_classifier",
                    class_name=cls,
                    format="pandas",
                    page_size=25,
                    probability=0.5,
                )
            except Exception as e:
                logger.warning(f"Failed to query {cls}: {e}")
                continue

            if objects_df is None or objects_df.empty:
                logger.info(f"{cls}: no objects found")
                continue

            for _, row in objects_df.iterrows():
                oid = row.get("oid")
                if not oid:
                    continue

                ra = float(row.get("meanra", 0))
                dec_val = float(row.get("meandec", 0))
                first_mjd = row.get("firstmjd")
                last_mjd = row.get("lastmjd")

                def mjd_dt(mjd):
                    if mjd:
                        return Time(float(mjd), format="mjd").to_datetime(timezone=timezone.utc)
                    return None

                stmt = pg_insert(Object).values(
                    oid=oid,
                    ra=ra,
                    dec=dec_val,
                    first_detection=mjd_dt(first_mjd),
                    last_detection=mjd_dt(last_mjd),
                    n_detections=int(row.get("ndet", 0)),
                    classification=cls,
                    classification_probability=float(row.get("probability", 0)),
                    classifier_name="lc_classifier",
                    broker_source="alerce",
                    alert_url=f"https://alerce.online/object/{oid}",
                ).on_conflict_do_nothing()
                await session.execute(stmt)
                total += 1

                # Pull light curve
                try:
                    dets = client.query_detections(oid, format="pandas", sort="mjd")
                    if dets is not None and len(dets) > 0:
                        for _, det in dets.iterrows():
                            mjd = float(det.get("mjd", 0))
                            fid = det.get("fid")
                            band_map = {1: "g", 2: "r", 3: "i"}
                            d = Detection(
                                oid=oid,
                                candid=int(det["candid"]) if det.get("candid") else None,
                                mjd=mjd,
                                detection_time=mjd_dt(mjd),
                                fid=int(fid) if fid else None,
                                band=band_map.get(int(fid)) if fid else None,
                                magpsf=float(det["magpsf"]) if det.get("magpsf") else None,
                                sigmapsf=float(det["sigmapsf"]) if det.get("sigmapsf") else None,
                                ra=float(det["ra"]) if det.get("ra") else None,
                                dec=float(det["dec"]) if det.get("dec") else None,
                            )
                            session.add(d)
                except Exception as e:
                    logger.warning(f"Light curve fetch failed for {oid}: {e}")

            await session.commit()
            logger.info(f"{cls}: ingested {len(objects_df)} objects")

    logger.info(f"Seed complete: {total} objects ingested")


if __name__ == "__main__":
    asyncio.run(seed())
