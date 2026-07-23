"""
Unit tests for app/ingestion/chime_service.py.
ChimeFRBIngestionService._extract_fields is a pure function —
no database, no HTTP calls required.
"""
import pytest

from app.ingestion.chime_service import ChimeFRBIngestionService

service = ChimeFRBIngestionService()


def test_extract_valid_row():
    row = {
        "Name": "FRB20180725A",
        "RAJ2000": 106.5,
        "DEJ2000": -3.2,
        "DM": 716.6,
        "MJD400": 58324.0,
    }
    result = service._extract_fields(row)
    assert result is not None
    assert result["oid"] == "FRB20180725A"
    assert result["ra"] == pytest.approx(106.5)
    assert result["dec"] == pytest.approx(-3.2)
    assert result["dispersion_measure"] == pytest.approx(716.6)
    assert result["detection_time"] is not None


def test_extract_missing_name_returns_none():
    row = {"RAJ2000": 106.5, "DEJ2000": -3.2, "DM": 716.6, "MJD400": 58324.0}
    assert service._extract_fields(row) is None


def test_extract_zero_position_returns_none():
    row = {"Name": "FRB20180725A", "RAJ2000": 0.0, "DEJ2000": 0.0,
           "DM": 716.6, "MJD400": 58324.0}
    assert service._extract_fields(row) is None


def test_extract_nan_name_returns_none():
    row = {"Name": "nan", "RAJ2000": 106.5, "DEJ2000": -3.2,
           "DM": 716.6, "MJD400": 58324.0}
    assert service._extract_fields(row) is None


def test_extract_missing_dm_still_works():
    row = {"Name": "FRB20180725A", "RAJ2000": 106.5, "DEJ2000": -3.2,
           "MJD400": 58324.0}
    result = service._extract_fields(row)
    assert result is not None
    assert result["dispersion_measure"] is None


def test_extract_missing_mjd_still_works():
    row = {"Name": "FRB20180725A", "RAJ2000": 106.5, "DEJ2000": -3.2,
           "DM": 716.6}
    result = service._extract_fields(row)
    assert result is not None
    assert result["detection_time"] is None


# ---------------------------------------------------------------------------
# Task 4 — localization uncertainty columns (e_RAJ2000 / e_DEJ2000)
# ---------------------------------------------------------------------------

def test_extract_localization_uncertainty():
    row = {"Name": "FRB20180725A", "RAJ2000": 106.5, "DEJ2000": -3.2,
           "e_RAJ2000": 0.25, "e_DEJ2000": 0.30, "DM": 716.6, "MJD400": 58324.0}
    result = service._extract_fields(row)
    assert result["ra_err_deg"] == pytest.approx(0.25)
    assert result["dec_err_deg"] == pytest.approx(0.30)


def test_extract_missing_uncertainty_returns_none():
    row = {"Name": "FRB20180725A", "RAJ2000": 106.5, "DEJ2000": -3.2,
           "DM": 716.6, "MJD400": 58324.0}
    result = service._extract_fields(row)
    assert result["ra_err_deg"] is None
    assert result["dec_err_deg"] is None


def test_extract_nan_uncertainty_returns_none():
    row = {"Name": "FRB20180725A", "RAJ2000": 106.5, "DEJ2000": -3.2,
           "e_RAJ2000": float("nan"), "e_DEJ2000": float("nan"),
           "DM": 716.6, "MJD400": 58324.0}
    result = service._extract_fields(row)
    assert result["ra_err_deg"] is None
    assert result["dec_err_deg"] is None
