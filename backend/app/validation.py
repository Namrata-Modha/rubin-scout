"""
Input validation and sanitization.

Centralizes all validation rules so they're consistent across endpoints.
Follows OWASP input validation guidelines:
- Allowlist over denylist
- Strict type checking
- Length limits on all strings
- Regex patterns for structured inputs
"""

import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from enum import Enum

# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------

# Valid ALeRCE classification classes
VALID_CLASSIFICATIONS = {
    "SNIa", "SNII", "SNIbc", "SLSN",
    "TDE", "KN",
    "AGN", "Blazar", "QSO",
    "CV/Nova",
    "LPV", "DSCT", "RRL", "CEP", "EB", "Periodic-Other",
}

# Valid notification methods
VALID_NOTIFICATION_METHODS = {"email", "slack", "webhook"}

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# ZTF object IDs: "ZTF" followed by 2 digits (year) then alphanumeric
OID_PATTERN = re.compile(r"^ZTF\d{2}[a-z]{7,10}$")

# GW superevent IDs: "GW" or "S" followed by digits and optional letter
GW_EVENT_PATTERN = re.compile(r"^(GW|S)\d{6}[a-z]?$")

# Loose email validation (Pydantic EmailStr is better but this is a fallback)
EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# URL validation for webhooks (https only in production)
WEBHOOK_URL_PATTERN = re.compile(r"^https?://[a-zA-Z0-9.\-]+(:[0-9]+)?(/[^\s]*)?$")


# ---------------------------------------------------------------------------
# Validation functions
# ---------------------------------------------------------------------------

def validate_oid(oid: str) -> str:
    """
    Validate and sanitize an astronomical object ID.
    Must match ZTF naming convention.
    """
    oid = oid.strip()
    if len(oid) > 30:
        raise ValueError("Object ID too long (max 30 characters)")
    if not OID_PATTERN.match(oid):
        raise ValueError(f"Invalid object ID format: {oid}. Expected ZTFYYxxxxxxx pattern.")
    return oid


def validate_superevent_id(superevent_id: str) -> str:
    """Validate a GW superevent ID."""
    superevent_id = superevent_id.strip()
    if len(superevent_id) > 20:
        raise ValueError("Superevent ID too long")
    if not GW_EVENT_PATTERN.match(superevent_id):
        raise ValueError(f"Invalid superevent ID format: {superevent_id}")
    return superevent_id


def validate_classification(classification: Optional[str]) -> Optional[str]:
    """Validate classification against allowlist. Returns None if invalid."""
    if classification is None:
        return None
    classification = classification.strip()
    if classification not in VALID_CLASSIFICATIONS:
        return None  # Silently ignore invalid classifications rather than error
    return classification


# ---------------------------------------------------------------------------
# Pydantic models with strict validation
# ---------------------------------------------------------------------------

class SubscriptionCreateRequest(BaseModel):
    """Validated subscription creation request."""
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Human-readable name for this subscription",
    )
    user_email: str = Field(
        ...,
        max_length=254,  # RFC 5321 max email length
        description="Email address for notifications",
    )
    filter_config: dict = Field(
        default_factory=dict,
        description="Filter criteria (classification, min_probability, etc.)",
    )
    notification_method: str = Field(
        default="email",
        description="How to send notifications: email, slack, or webhook",
    )
    webhook_url: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Webhook URL (required if method is slack or webhook)",
    )
    slack_channel: Optional[str] = Field(
        default=None,
        max_length=100,
    )

    @field_validator("user_email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_PATTERN.match(v):
            raise ValueError("Invalid email address format")
        return v

    @field_validator("notification_method")
    @classmethod
    def validate_method(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in VALID_NOTIFICATION_METHODS:
            raise ValueError(f"Invalid method. Must be one of: {', '.join(VALID_NOTIFICATION_METHODS)}")
        return v

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not WEBHOOK_URL_PATTERN.match(v):
            raise ValueError("Invalid webhook URL. Must be a valid HTTP/HTTPS URL.")
        return v

    @field_validator("filter_config")
    @classmethod
    def validate_filter(cls, v: dict) -> dict:
        """Validate filter_config keys and values against allowlist."""
        allowed_keys = {"classification", "min_probability", "max_magnitude",
                        "exclude_known_variables", "max_redshift"}
        unexpected = set(v.keys()) - allowed_keys
        if unexpected:
            raise ValueError(f"Unexpected filter keys: {unexpected}. Allowed: {allowed_keys}")

        # Validate individual filter values
        if "classification" in v:
            classes = v["classification"]
            if isinstance(classes, list):
                v["classification"] = [c for c in classes if c in VALID_CLASSIFICATIONS]
            elif isinstance(classes, str) and classes in VALID_CLASSIFICATIONS:
                v["classification"] = [classes]
            else:
                del v["classification"]

        if "min_probability" in v:
            prob = v["min_probability"]
            if not isinstance(prob, (int, float)) or not (0 <= prob <= 1):
                raise ValueError("min_probability must be a number between 0 and 1")

        return v

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Nearby bright supernovae",
                "user_email": "researcher@university.edu",
                "filter_config": {
                    "classification": ["SNIa", "SNII"],
                    "min_probability": 0.8,
                },
                "notification_method": "email",
            }
        }


class SubscriptionUpdateRequest(BaseModel):
    """Validated subscription update request. All fields optional."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    filter_config: Optional[dict] = None
    notification_method: Optional[str] = None
    webhook_url: Optional[str] = Field(default=None, max_length=500)
    active: Optional[bool] = None

    @field_validator("notification_method")
    @classmethod
    def validate_method(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().lower()
        if v not in VALID_NOTIFICATION_METHODS:
            raise ValueError(f"Invalid method. Must be one of: {', '.join(VALID_NOTIFICATION_METHODS)}")
        return v

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not WEBHOOK_URL_PATTERN.match(v):
            raise ValueError("Invalid webhook URL")
        return v
