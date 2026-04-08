"""
ALeRCE enrichment cron job.
SECONDARY enrichment - adds light curves and ML classifications to existing objects.
Runs every hour.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from alerce.core import Alerce
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import async_session_maker
from app.models import Object, Detection, ClassificationProbability
from astropy.time import Time


async def enrich_recent_objects():
    """Enrich recently added objects with ALeRCE data."""
    print(f"[{datetime.utcnow()}] Enriching recent objects with ALeRCE data")
    
    client = Alerce()
    
    async with async_session_maker() as session:
        # Get objects from last 24 hours that don't have ALeRCE data yet
        cutoff = datetime.utcnow() - timedelta(hours=24)
        result = await session.execute(
            select(Object).where(
                Object.created_at >= cutoff,
                Object.broker_source == "TNS"
            ).limit(50)
        )
        objects = result.scalars().all()
        
        print(f"Found {len(objects)} objects to enrich")
        
        enriched = 0
        for obj in objects:
            # Try to find corresponding ZTF object in ALeRCE
            # TNS names like "2024abc" → search ALeRCE by coordinates
            try:
                # Query ALeRCE for objects near these coordinates
                nearby = client.query_objects(
                    ra=obj.ra,
                    dec=obj.dec,
                    radius=2,  # 2 arcsec radius
                    format="pandas",
                    page_size=5
                )
                
                if nearby is None or len(nearby) == 0:
                    continue
                
                # Take the closest match
                alerce_obj = nearby.iloc[0]
                oid = alerce_obj["oid"]
                
                print(f"  {obj.oid} → ALeRCE {oid}")
                
                # Get detections (light curve)
                detections = client.query_detections(oid, format="pandas", sort="mjd")
                if detections is not None and len(detections) > 0:
                    for _, det in detections.iterrows():
                        # Check if detection already exists
                        det_result = await session.execute(
                            select(Detection).where(
                                Detection.oid == obj.oid,
                                Detection.mjd == det["mjd"]
                            )
                        )
                        if det_result.scalar_one_or_none():
                            continue
                        
                        detection = Detection(
                            oid=obj.oid,
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
                    
                    obj.n_detections = len(detections)
                
                # Get ML classifications
                probs = client.query_probabilities(oid, format="pandas")
                if probs is not None and len(probs) > 0:
                    classifications = probs.sort_values("probability", ascending=False).to_dict('records')
                    
                    # Update object classification if we have better info
                    if classifications and not obj.sub_classification:
                        obj.sub_classification = classifications[0]["class_name"]
                        obj.classification_probability = classifications[0]["probability"]
                        obj.classifier_name = "lc_classifier"
                        obj.classifier_version = "alerce"
                    
                    # Store top 5 classifications
                    for cls in classifications[:5]:
                        # Check if exists
                        cls_result = await session.execute(
                            select(ClassificationProbability).where(
                                ClassificationProbability.oid == obj.oid,
                                ClassificationProbability.class_name == cls["class_name"]
                            )
                        )
                        if cls_result.scalar_one_or_none():
                            continue
                        
                        cls_prob = ClassificationProbability(
                            oid=obj.oid,
                            class_name=cls["class_name"],
                            probability=cls["probability"],
                            classifier_name="lc_classifier",
                            classifier_version="alerce"
                        )
                        session.add(cls_prob)
                
                obj.updated_at = datetime.utcnow()
                await session.commit()
                enriched += 1
                
            except Exception as e:
                print(f"  Error enriching {obj.oid}: {e}")
                continue
        
        print(f"Enriched {enriched}/{len(objects)} objects with ALeRCE data")


async def main():
    """Main enrichment cron job."""
    print(f"\n{'='*60}")
    print(f"ALeRCE Enrichment Cron - {datetime.utcnow()} UTC")
    print(f"{'='*60}\n")
    
    await enrich_recent_objects()
    
    print(f"\n{'='*60}")
    print(f"Enrichment complete")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())