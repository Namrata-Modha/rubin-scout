"""
Unit tests for the ILMT follow-up recommendation engine in app/api/alerts.py.

_build_recommendation and _mjd_to_datetime are pure functions with no
database access, so no fixtures are required.
"""


from app.api.alerts import _build_recommendation, _mjd_to_datetime

# ---------------------------------------------------------------------------
# _build_recommendation
# ---------------------------------------------------------------------------

def test_priority_followup_gw_coincidence():
    """A GW-coincident position should always yield PRIORITY_FOLLOWUP."""
    code, reason = _build_recommendation(
        ztf_history=[],
        simbad=None,
        gw_coincidence=[{"superevent_id": "GW231123"}],
        visibility={"observable": True},
    )
    assert code == "PRIORITY_FOLLOWUP"
    assert reason  # non-empty explanation


def test_likely_known_known_qso():
    """Pre-existing QSO with no new activity → LIKELY_KNOWN."""
    code, reason = _build_recommendation(
        ztf_history=[{
            "pre_existing": True,
            "new_activity": False,
            "classification": "QSO",
        }],
        simbad={
            "name": "SDSS J123",
            "type": "QSO",
            "distance_arcsec": 2.1,
        },
        gw_coincidence=[],
        visibility={"observable": True},
    )
    assert code == "LIKELY_KNOWN"
    assert reason


def test_needs_more_data_no_history():
    """No ZTF history, no SIMBAD → NEEDS_MORE_DATA."""
    code, reason = _build_recommendation(
        ztf_history=[],
        simbad=None,
        gw_coincidence=[],
        visibility={"observable": True},
    )
    assert code == "NEEDS_MORE_DATA"
    assert reason


def test_priority_followup_new_kilonova():
    """New activity from a KN candidate → PRIORITY_FOLLOWUP."""
    code, reason = _build_recommendation(
        ztf_history=[{
            "pre_existing": False,
            "new_activity": True,
            "classification": "KN",
        }],
        simbad=None,
        gw_coincidence=[],
        visibility={"observable": True},
    )
    assert code == "PRIORITY_FOLLOWUP"
    assert reason


def test_needs_more_data_not_observable():
    """ZTF history present but target not observable → NEEDS_MORE_DATA."""
    code, reason = _build_recommendation(
        ztf_history=[{
            "pre_existing": True,
            "new_activity": True,
            "classification": "SNIa",
        }],
        simbad=None,
        gw_coincidence=[],
        visibility={"observable": False},
    )
    assert code == "NEEDS_MORE_DATA"
    assert reason


# ---------------------------------------------------------------------------
# _mjd_to_datetime
# ---------------------------------------------------------------------------

def test_mjd_to_datetime_gw170817():
    """
    GW170817 occurred at GPS 1187008882.4, corresponding to MJD ≈ 57982.5,
    which falls in August 2017.
    """
    dt = _mjd_to_datetime(57982.5)
    assert dt.year == 2017
    assert dt.month == 8
