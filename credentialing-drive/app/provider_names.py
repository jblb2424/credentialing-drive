"""Provider-name parsing and comparison helpers shared by ingestion and issues."""

import re

from app.provider_types import normalize_provider_type


def credential_segment(value):
    """Return whether a trailing name segment is a credential designation."""
    normalized = str(value or "").strip()
    if not normalized:
        return False
    if normalize_provider_type(normalized):
        return True

    # Preserve non-clinical post-nominals such as FACP without treating ordinary
    # title-cased surname components as credentials.
    return bool(re.fullmatch(r"[A-Z]{2,8}(?:\s*,\s*[A-Z]{2,8})*", normalized))


def split_provider_name_and_credentials(value):
    """Separate a supported credential suffix from a human name."""
    name = str(value or "").strip()
    if not name:
        return None, None

    parts = [part.strip() for part in name.split(",")]
    suffixes = []
    while len(parts) > 1 and credential_segment(parts[-1]):
        suffixes.insert(0, parts.pop())

    # Some documents omit the comma before a controlled designation.
    if not suffixes:
        words = name.split()
        if len(words) > 2 and credential_segment(words[-1]):
            suffixes.append(words.pop())
            parts = [" ".join(words)]

    return ", ".join(part for part in parts if part).strip() or None, ", ".join(suffixes) or None


def name_parts(name):
    """Parse conventional credentialing name formats without guessing ambiguous names."""
    cleaned_name, _ = split_provider_name_and_credentials(name)
    if not cleaned_name:
        return {}
    if "," in cleaned_name:
        last_name, given_names = (part.strip() for part in cleaned_name.split(",", 1))
        parts = given_names.split()
        return {
            "first_name": parts[0] if parts else None,
            "middle_name": " ".join(parts[1:]) or None,
            "last_name": last_name or None,
        }
    parts = cleaned_name.split()
    if len(parts) >= 2:
        return {
            "first_name": parts[0],
            "middle_name": " ".join(parts[1:-1]) or None,
            "last_name": parts[-1],
        }
    return {}


def canonical_provider_name(profile):
    """Build the display name from structured identity fields, never credentials."""
    return " ".join(
        str(value).strip()
        for value in (
            profile.get("first_name"),
            profile.get("middle_name"),
            profile.get("last_name"),
        )
        if value and str(value).strip()
    ) or None


def enrich_provider_name(profile):
    profile = dict(profile or {})
    cleaned_name, name_credentials = split_provider_name_and_credentials(profile.get("name"))
    if cleaned_name:
        profile["name"] = cleaned_name
    if not profile.get("credentials") and name_credentials:
        profile["credentials"] = name_credentials

    for field_name, parsed_value in name_parts(cleaned_name).items():
        if not profile.get(field_name) and parsed_value:
            profile[field_name] = parsed_value

    canonical_name = canonical_provider_name(profile)
    if canonical_name:
        profile["name"] = canonical_name
    return profile


def normalized_name_identity(value):
    parts = name_parts(value)
    return {
        field_name: re.sub(r"[^a-z0-9]+", "", str(parts.get(field_name) or "").lower())
        for field_name in ("first_name", "middle_name", "last_name")
    }


def name_values_equivalent(previous_value, current_value):
    """Ignore formatting-only name changes while retaining actual identity conflicts."""
    previous = normalized_name_identity(previous_value)
    current = normalized_name_identity(current_value)
    if not all((previous["first_name"], previous["last_name"], current["first_name"], current["last_name"])):
        return False
    if (previous["first_name"], previous["last_name"]) != (
        current["first_name"], current["last_name"]
    ):
        return False

    # A middle name can be absent in a source document, but two distinct middle
    # names remain a meaningful conflict.
    return (
        previous["middle_name"] == current["middle_name"]
        or not previous["middle_name"]
        or not current["middle_name"]
    )
