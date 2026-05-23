"""
Unit tests for GW-related pure functions.

_classify_from_masses lives in app/enrichment/gw_crossmatch.py.
_gw_sky_separation_deg lives in app/api/alerts.py.

Neither function touches the database, so no fixtures are required.
"""

import pytest

from app.enrichment.gw_crossmatch import _classify_from_masses
from app.api.alerts import _gw_sky_separation_deg


# ---------------------------------------------------------------------------
# _classify_from_masses
# ---------------------------------------------------------------------------

def test_classify_bbh():
    """Two heavy black holes (35+30 solar masses) -> BBH."""
    assert _classify_from_masses(35.0, 30.0) == {"BBH": 1.0}


def test_classify_bns():
    """Two neutron-star-mass components (1.4+1.2 solar masses, both < 3) -> BNS."""
    assert _classify_from_masses(1.4, 1.2) == {"BNS": 1.0}


def test_classify_nsbh():
    """Heavy primary (9 solar masses) + light secondary (1.9, < 3) -> NSBH."""
    assert _classify_from_masses(9.0, 1.9) == {"NSBH": 1.0}


def test_classify_none_masses():
    """Unknown masses should default to the most common class, BBH."""
    assert _classify_from_masses(None, None) == {"BBH": 1.0}


# ---------------------------------------------------------------------------
# _gw_sky_separation_deg
# ---------------------------------------------------------------------------

def test_sky_separation_same_point():
    """A point separated from itself should have zero angular distance."""
    assert _gw_sky_separation_deg(0.0, 0.0, 0.0, 0.0) == 0.0


def test_sky_separation_known_value():
    """Two points 90 degrees apart in RA along the equator should give approx 90 degrees."""
    sep = _gw_sky_separation_deg(0.0, 0.0, 90.0, 0.0)
    assert abs(sep - 90.0) < 0.01


def test_sky_separation_gw170817():
    """GW170817 and host galaxy NGC 4993 share the same sky coordinates.

    Separation must be < 1e-4 degrees (< 0.4 arcsec). A tiny floating-point
    residual can appear because sin^2 + cos^2 != exactly 1.0 in IEEE 754
    arithmetic for non-zero angles.
    """
    # GW170817: RA 197.45, Dec -23.38 / NGC 4993: RA 197.45, Dec -23.38
    sep = _gw_sky_separation_deg(197.45, -23.38, 197.45, -23.38)
    assert sep == pytest.approx(0.0, abs=1e-4)
