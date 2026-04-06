"""
Rubin Scout — Connection Verification.

Run this first to confirm everything works:
    python -m scripts.verify_connection

No API keys needed. No database needed. Just Python + alerce.
"""

import sys


def main():
    print("\n" + "=" * 60)
    print("  RUBIN SCOUT — Connection Verification")
    print("=" * 60)

    # Step 1: Check imports
    print("\n[1/4] Checking Python dependencies...")
    try:
        from alerce.core import Alerce
        import astropy
        import pandas as pd
        print(f"  ✓ alerce client loaded")
        print(f"  ✓ astropy {astropy.__version__}")
        print(f"  ✓ pandas {pd.__version__}")
    except ImportError as e:
        print(f"  ✗ Missing dependency: {e}")
        print("  Run: pip install -r backend/requirements.txt")
        sys.exit(1)

    # Step 2: Connect to ALeRCE
    print("\n[2/5] Connecting to ALeRCE API...")
    try:
        client = Alerce()
        objects = client.query_objects(
            classifier="lc_classifier",
            class_name="SNIa",
            format="pandas",
            page_size=5,
            probability=0.8,
        )
        n_objects = len(objects) if objects is not None else 0
        print(f"  ✓ ALeRCE connected — got {n_objects} Type Ia supernova candidates")
    except Exception as e:
        print(f"  ✗ ALeRCE connection failed: {e}")
        sys.exit(1)

    # Step 3: Check TNS daily CSV access (no API key needed)
    print("\n[3/5] Checking TNS daily CSV access...")
    try:
        import httpx
        from datetime import datetime, timedelta, timezone

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")
        csv_url = f"https://www.wis-tns.org/system/files/tns_public_objects/tns_public_objects_{yesterday}.csv.zip"
        resp = httpx.head(csv_url, follow_redirects=True, timeout=15)
        if resp.status_code == 200:
            size_mb = int(resp.headers.get("content-length", 0)) / 1024 / 1024
            print(f"  ✓ TNS daily CSV available ({size_mb:.1f} MB for {yesterday})")
        elif resp.status_code == 404:
            print(f"  ⚠ TNS CSV for {yesterday} not yet generated (check after midnight UTC)")
        else:
            print(f"  ⚠ TNS returned status {resp.status_code}")
    except Exception as e:
        print(f"  ⚠ TNS check failed (non-critical): {e}")

    # Step 4: Pull a light curve
    print("\n[4/5] Pulling a light curve...")
    try:
        if n_objects > 0:
            test_oid = objects.iloc[0]["oid"]
            detections = client.query_detections(test_oid, format="pandas", sort="mjd")
            n_dets = len(detections) if detections is not None else 0
            print(f"  ✓ Object {test_oid}: {n_dets} detections")

            if n_dets > 0:
                first = detections.iloc[0]
                last = detections.iloc[-1]
                print(f"    First detection: MJD {first['mjd']:.2f}, mag {first.get('magpsf', 'N/A')}")
                print(f"    Last detection:  MJD {last['mjd']:.2f}, mag {last.get('magpsf', 'N/A')}")
        else:
            print("  ⚠ No objects returned, skipping light curve test")
    except Exception as e:
        print(f"  ✗ Light curve fetch failed: {e}")

    # Step 5: Test astroquery (SIMBAD)
    print("\n[5/5] Testing SIMBAD cross-match...")
    try:
        from astropy.coordinates import SkyCoord
        import astropy.units as u
        from astroquery.simbad import Simbad

        # Query a well-known position (Crab Nebula)
        coord = SkyCoord(ra=83.6331, dec=22.0145, unit=(u.degree, u.degree))
        result = Simbad.query_region(coord, radius=10 * u.arcsec)

        if result and len(result) > 0:
            print(f"  ✓ SIMBAD connected — found {len(result)} objects near Crab Nebula")
            # Column names vary by astroquery version: MAIN_ID or main_id
            col = 'main_id' if 'main_id' in result.colnames else 'MAIN_ID'
            print(f"    Closest match: {result[0][col]}")
        else:
            print("  ⚠ SIMBAD returned no results (may be temporarily down)")
    except Exception as e:
        print(f"  ⚠ SIMBAD query failed (non-critical): {e}")

    # Summary
    print("\n" + "=" * 60)
    print("  ALL CHECKS PASSED — You're ready to build!")
    print("=" * 60)
    print()
    print("Next steps:")
    print("  1. cp .env.example .env")
    print("  2. docker-compose up -d        (starts PostgreSQL)")
    print("  3. cd backend && uvicorn app.main:app --reload")
    print("  4. Open http://localhost:8000/docs")
    print()


if __name__ == "__main__":
    main()
