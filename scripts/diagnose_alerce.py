"""Quick diagnostic: what can ALeRCE give us right now?"""
from alerce.core import Alerce
client = Alerce()

print("Testing ALeRCE without time filter...")
for cls in ["SNIa", "SNII", "AGN", "TDE"]:
    try:
        objects = client.query_objects(
            classifier="lc_classifier",
            class_name=cls,
            format="pandas",
            page_size=5,
            probability=0.5,
        )
        n = len(objects) if objects is not None else 0
        print(f"  {cls}: {n} objects found")
        if n > 0:
            row = objects.iloc[0]
            print(f"    Example: {row['oid']}  RA={row.get('meanra',0):.3f}  ndet={row.get('ndet',0)}")
    except Exception as e:
        print(f"  {cls}: error - {e}")

print("\nDone.")
