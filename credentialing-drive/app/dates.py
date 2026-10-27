"""Canonical date handling for extracted credentialing data.

Dates are stored as ISO values whenever the source provides a complete calendar date.
Month-only and year-only values retain their source precision instead of inventing a day.
"""

import re
from datetime import date, datetime


DATE_FIELD_NAMES = {
    "date",
    "date_of_birth",
    "issue_date",
    "issued_date",
    "issued_on",
    "expiration_date",
    "expires_at",
    "expires_on",
    "expiry_date",
    "expiration_dates",
    "effective_date",
    "certification_date",
    "certified_date",
    "start_date",
    "end_date",
    "signed_date",
    "renewal_date",
    "valid_from",
    "valid_to",
}

COMPLETE_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%m-%d-%Y",
    "%m.%d.%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d %Y",
    "%B %d %Y",
)
MONTH_DATE_FORMATS = ("%Y-%m", "%Y/%m", "%b %Y", "%B %Y")


def normalize_date_value(value):
    """Return a canonical ISO date when the source supplies a recognizable date."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return value

    normalized = re.sub(r"\s+", " ", value.strip())
    if not normalized:
        return value
    normalized = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", normalized, flags=re.IGNORECASE)
    normalized = normalized.replace(",", "")

    for date_format in COMPLETE_DATE_FORMATS:
        try:
            return datetime.strptime(normalized, date_format).date().isoformat()
        except ValueError:
            continue

    for date_format in MONTH_DATE_FORMATS:
        try:
            return datetime.strptime(normalized, date_format).strftime("%Y-%m")
        except ValueError:
            continue

    if re.fullmatch(r"\d{4}", normalized):
        return normalized
    return value


def parse_complete_date(value):
    """Return a date only when the value includes a full calendar day."""
    normalized = normalize_date_value(value)
    if not isinstance(normalized, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def is_date_field(field_name):
    if not isinstance(field_name, str):
        return False
    return field_name in DATE_FIELD_NAMES or field_name.endswith("_date")


def normalize_date_fields(value, field_name=None):
    """Recursively normalize only values stored in explicitly date-like fields."""
    if isinstance(value, dict):
        return {
            key: normalize_date_fields(item, key)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_date_fields(item, field_name) for item in value]
    if is_date_field(field_name):
        return normalize_date_value(value)
    return value
