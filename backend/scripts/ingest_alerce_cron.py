"""
ALeRCE ingestion cron job.
Runs every 15 minutes to pull recent transient candidates.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from alerce.core import Alerce
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import async_session_maker
from app.models import Object, Detection, ClassificationProbability
from astropy.time import Time
from astropy.coordinates import SkyCoord
import astropy.units as u


async def fetch_recent_supernovae(lookback_minutes: int = 60):
    """Fetch recent supernova candidates from ALeRCE."""
    client = Alerce()
    
    # Calculate MJD window
    now_mjd = Time.now().mjd
    start_mjd = now_mjd - (lookback_minutes / 1440)  # Convert minutes to days
    
    print(f"[{datetime.utcnow()}] Querying ALeRCE: MJD {start_mjd:.2f} to {now_mjd:.2f}")
    
    # Query recent SN candidates
    try:
        objects = client.query_objects(
            classifier="lc_classifier",
            class_name=["SNIa", "SNII", "SNIbc"],  # All SN types
            format="pandas",
            firstmjd=[start_mjd, now_mjd],
            page_size=100,
            probability=0.5  # Lower threshold for more candidates
        )
        
        print(f"Found {len(objects)} candidates")
        return objects
        
    except Exception as e:
        print(f"Error querying ALeRCE: {e}")
        return None


async def store_object(session: AsyncSession, obj_data, detections_data, classifications):
    """Store or update an object in the database."""
    oid = obj_data["oid"]
    
    # Check if object already exists
    result = await session.execute(select(Object).where(Object.oid == oid))
    existing = result.scalar_one_or_none()
    
    if existing:
        # Update last_detection and n_detections
        existing.last_detection = datetime.fromtimestamp(Time(obj_data["lastmjd"], format='mjd').unix)
        existing.n_detections = obj_data.get("ndet", existing.n_detections)
        existing.updated_at = datetime.utcnow()
        print(f"  Updated existing object: {oid}")
    else:
        # Create new object
        first_det = datetime.fromtimestamp(Time(obj_data["firstmjd"], format='mjd').unix)
        last_det = datetime.fromtimestamp(Time(obj_data["lastmjd"], format='mjd').unix)
        
        new_obj = Object(
            oid=oid,
            ra=obj_data["meanra"],
            dec=obj_data["meandec"],
            first_detection=first_det,
            last_detection=last_det,
            n_detections=obj_data.get("ndet", 0),
            classification=classifications[0]["class_name"] if classifications else None,
            classification_probability=classifications[0]["probability"] if classifications else None,
            classifier_name="lc_classifier",
            classifier_version="alerce",
            broker_source="ALeRCE"
        )
        
        session.add(new_obj)
        print(f"  Created new object: {oid}")
    
    # Store detections (only if new object or if we want to update detections)
    if not existing and detections_data is not None and len(detections_data) > 0:
        for _, det in detections_data.iterrows():
            detection = Detection(
                oid=oid,
                mjd=det["mjd"],
                detection_time=datetime.fromtimestamp(Time(det["mjd"], format='mjd').unix),
                fid=det.get("fid"),
                magpsf=det.get("magpsf_corr") or det.get("magpsf"),
                sigmapsf=det.get("sigmapsf_corr") or det.get("sigmapsf"),
                ra=det.get("ra"),
                dec=det.get("dec"),
                isdiffpos=det.get("isdiffpos")
            )
            session.add(detection)
    
    # Store classification probabilities
    if not existing and classifications:
        for cls in classifications[:5]:  # Top 5 classes
            cls_prob = ClassificationProbability(
                oid=oid,
                class_name=cls["class_name"],
                probability=cls["probability"],
                classifier_name="lc_classifier",
                classifier_version="alerce"
            )
            session.add(cls_prob)
    
    await session.commit()


async def main():
    """Main cron job execution."""
    print(f"\n{'='*60}")
    print(f"ALeRCE Ingestion Cron - {datetime.utcnow()} UTC")
    print(f"{'='*60}\n")
    
    # Fetch recent candidates
    objects = await fetch_recent_supernovae(lookback_minutes=60)
    
    if objects is None or len(objects) == 0:
        print("No new candidates found.")
        return
    
    # Process each object
    client = Alerce()
    processed = 0
    
    async with async_session_maker() as session:
        for idx, obj in objects.iterrows():
            oid = obj["oid"]
            
            try:
                # Get detections
                detections = client.query_detections(oid, format="pandas", sort="mjd")
                
                # Get probabilities
                probs = client.query_probabilities(oid, format="pandas")
                classifications = []
                if probs is not None and len(probs) > 0:
                    # Convert to list of dicts sorted by probability
                    classifications = probs.sort_values("probability", ascending=False).to_dict('records')
                
                # Store in database
                await store_object(session, obj, detections, classifications)
                processed += 1
                
            except Exception as e:
                print(f"  Error processing {oid}: {e}")
                continue
    
    print(f"\n{'='*60}")
    print(f"Ingestion complete: {processed}/{len(objects)} objects processed")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())