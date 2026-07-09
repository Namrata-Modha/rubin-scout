"""
Unit tests for app/validation.py.

Tests validate_oid and validate_classification against the
allowlist-based validation logic without any database access.
"""

import pytest

from app.validation import validate_classification, validate_oid

# ---------------------------------------------------------------------------
# validate_oid
# ---------------------------------------------------------------------------

def test_valid_ztf_oid():
    """A well-formed ZTF ID should pass without raising."""
    validate_oid("ZTF23aadqjhu")


def test_valid_frb_oid():
    """A CHIME/FRB Catalog 1 ID with a single uppercase suffix should pass."""
    validate_oid("FRB20190701D")


def test_valid_frb_oid_two_letter():
    """A CHIME/FRB ID with a two-letter uppercase suffix should pass."""
    validate_oid("FRB20121102AA")


def test_invalid_oid_too_long():
    """An ID longer than 30 characters should raise ValueError."""
    with pytest.raises(ValueError):
        validate_oid("A" * 200)


def test_invalid_oid_sql_injection():
    """A SQL-injection string should raise ValueError."""
    with pytest.raises(ValueError):
        validate_oid("'; DROP TABLE objects; --")


def test_invalid_oid_empty():
    """An empty string should raise ValueError."""
    with pytest.raises(ValueError):
        validate_oid("")


# ---------------------------------------------------------------------------
# validate_classification
# ---------------------------------------------------------------------------

def test_valid_classification_snIa():
    """SNIa is in the allowlist and should be returned as-is."""
    assert validate_classification("SNIa") == "SNIa"


def test_valid_classification_frb():
    """FRB is in the allowlist and should be returned as-is."""
    assert validate_classification("FRB") == "FRB"


def test_invalid_classification_returns_none():
    """A value not in the allowlist should return None."""
    assert validate_classification("INVALID_CLASS") is None


def test_none_classification_returns_none():
    """None input should return None."""
    assert validate_classification(None) is None
