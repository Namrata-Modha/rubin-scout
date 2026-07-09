"""
Unit tests for TNS parsing helpers in app/ingestion/tns_service.py.

All functions under test are pure (no I/O, no database), so no fixtures
or mocking is needed.
"""

import pytest

from app.ingestion.tns_service import (
    _map_tns_type,
    _parse_tns_date,
    _parse_tns_dec,
    _parse_tns_ra,
)

# ---------------------------------------------------------------------------
# _parse_tns_ra
# ---------------------------------------------------------------------------

def test_parse_ra_decimal():
    """A plain decimal string should be returned as a float."""
    assert _parse_tns_ra("185.73") == pytest.approx(185.73)


def test_parse_ra_sexagesimal():
    """12h 21m 45.6s → (12 + 21/60 + 45.6/3600) * 15 ≈ 185.44°."""
    result = _parse_tns_ra("12:21:45.6")
    assert result == pytest.approx(185.44, abs=0.01)


def test_parse_ra_none():
    """None input should return None."""
    assert _parse_tns_ra(None) is None


def test_parse_ra_empty():
    """Empty string input should return None."""
    assert _parse_tns_ra("") is None


# ---------------------------------------------------------------------------
# _parse_tns_dec
# ---------------------------------------------------------------------------

def test_parse_dec_positive():
    """Positive decimal declination string should be returned as a float."""
    assert _parse_tns_dec("29.37") == pytest.approx(29.37)


def test_parse_dec_negative():
    """Negative decimal declination string should be returned as a float."""
    assert _parse_tns_dec("-23.38") == pytest.approx(-23.38)


def test_parse_dec_sexagesimal():
    """+29d 22m 12s → 29 + 22/60 + 12/3600 ≈ 29.37°."""
    result = _parse_tns_dec("+29:22:12.0")
    assert result == pytest.approx(29.37, abs=0.01)


# ---------------------------------------------------------------------------
# _map_tns_type
# ---------------------------------------------------------------------------

def test_map_type_snia():
    """'SN Ia' should map to our internal 'SNIa' code."""
    assert _map_tns_type("SN Ia") == "SNIa"


def test_map_type_snii():
    """'SN II' should map to our internal 'SNII' code."""
    assert _map_tns_type("SN II") == "SNII"


def test_map_type_unknown():
    """An unrecognised TNS type should return None."""
    assert _map_tns_type("Unknown Type XYZ") is None


def test_map_type_none():
    """None input should return None."""
    assert _map_tns_type(None) is None


# ---------------------------------------------------------------------------
# _parse_tns_date
# ---------------------------------------------------------------------------

def test_parse_date_standard():
    """A standard 'YYYY-MM-DD HH:MM:SS' string should parse correctly."""
    dt = _parse_tns_date("2026-05-19 14:30:00")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 5
    assert dt.day == 19
