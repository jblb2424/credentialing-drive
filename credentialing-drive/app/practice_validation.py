"""Deterministic guardrails for practice candidates returned by document extraction."""

import re

from app.provider_names import enrich_provider_name, name_values_equivalent


ORGANIZATION_SOURCE_ROLES = {
    "primary_practice",
    "employer",
    "organization_letterhead",
    "legal_business",
}
INDIVIDUAL_SOURCE_ROLES = {
    "applicant",
    "registrant",
    "named_insured",
    "subject",
    "billing_entity",
    "pay_to_entity",
}
NON_PRACTICE_SOURCE_ROLES = {
    "issuer",
    "training_institution",
    "insurer",
    "payer",
    "reviewer",
    "verification_source",
    "other",
}
ORGANIZATION_SUFFIX = re.compile(
    r"\b(?:llc|pllc|inc|corp|ltd|lp|llp|pc|p\.c\.)\b", re.IGNORECASE
)
FIXTURE_QUALIFIER = re.compile(
    r"(?:\s*[-,]\s*|\s*\()"
    r"(?:synthetic|fictional|test[ -]?only)"
    r"(?:\))?\s*$",
    re.IGNORECASE,
)


def compact_name(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def canonical_practice_name(value):
    """Remove explicit fixture-only suffixes while retaining the source name as an alias."""
    return FIXTURE_QUALIFIER.sub("", compact_name(value)).strip()


def group_identity_keys(group):
    """Build practice aliases, preferring durable Type 2 NPI or tax identities."""
    group = group or {}
    keys = []

    type_2_npi = re.sub(r"\D", "", str(group.get("type_2_npi") or ""))
    if len(type_2_npi) == 10 and len(set(type_2_npi)) > 1:
        keys.append(f"type-2-npi-{type_2_npi}")

    tax_id = re.sub(r"[^A-Za-z0-9]", "", str(group.get("tax_id") or "")).lower()
    if has_meaningful_identifier(tax_id):
        keys.append(f"tax-id-{tax_id}")

    names = [group.get("legal_name"), group.get("name"), *(group.get("aliases") or [])]
    for name in names:
        canonical_name = canonical_practice_name(name)
        normalized = re.sub(r"[^a-z0-9]+", "-", canonical_name.lower()).strip("-")
        if normalized and normalized not in keys:
            # Keep name keys backward-compatible with existing group_identities.
            keys.append(normalized)
    return keys


def has_meaningful_identifier(value):
    """Reject placeholder identifiers such as 000-00-0000."""
    identifier = re.sub(r"[^A-Za-z0-9]", "", str(value or ""))
    return bool(identifier and len(set(identifier)) > 1)


def candidate_matches_provider(group_name, provider):
    """Do not turn an individual provider or individual billing profile into a practice."""
    profile = enrich_provider_name(provider or {})
    provider_name = profile.get("name")
    candidate_name = canonical_practice_name(group_name)
    if not provider_name or not candidate_name:
        return False
    return name_values_equivalent(candidate_name, provider_name)


def practice_candidate_decision(normalized_data):
    """Return whether a parsed organization is safe to persist as a practice.

    The extractor supplies source_role/entity_kind, but this remains safe for
    older imports: no ambiguous, name-only candidate is promoted to a practice.
    """
    group = dict((normalized_data or {}).get("group") or {})
    provider = (normalized_data or {}).get("provider") or {}
    legal_name = compact_name(group.get("legal_name") or group.get("name"))
    source_role = str(group.get("source_role") or "unknown").strip().lower()
    entity_kind = str(group.get("entity_kind") or "unknown").strip().lower()

    if not legal_name:
        return {"action": "skip", "reason": "no_practice_name"}
    if candidate_matches_provider(legal_name, provider):
        return {"action": "skip", "reason": "matches_provider_identity"}
    if entity_kind == "individual" or source_role in INDIVIDUAL_SOURCE_ROLES:
        return {"action": "skip", "reason": "individual_or_billing_entity"}
    if source_role in NON_PRACTICE_SOURCE_ROLES:
        return {"action": "skip", "reason": f"non_practice_role:{source_role}"}

    type_2_npi = re.sub(r"\D", "", str(group.get("type_2_npi") or ""))
    has_organization_identifier = (
        len(type_2_npi) == 10
        and has_meaningful_identifier(type_2_npi)
    ) or has_meaningful_identifier(group.get("tax_id"))
    has_organization_name = bool(ORGANIZATION_SUFFIX.search(legal_name))
    if source_role in ORGANIZATION_SOURCE_ROLES or entity_kind == "organization":
        return {"action": "accept", "reason": "supported_organization"}
    if has_organization_identifier or has_organization_name:
        return {"action": "accept", "reason": "organization_identifier_or_suffix"}
    return {"action": "skip", "reason": "ambiguous_name_only_candidate"}


def canonicalize_practice_candidate(normalized_data):
    """Return a copy whose practice name is canonical and source spelling is retained."""
    normalized_data = dict(normalized_data or {})
    group = dict(normalized_data.get("group") or {})
    original_name = compact_name(group.get("legal_name") or group.get("name"))
    canonical_name = canonical_practice_name(original_name)
    aliases = [
        alias for alias in (group.get("aliases") or [])
        if isinstance(alias, str) and compact_name(alias)
    ]
    if original_name and original_name != canonical_name:
        aliases.append(original_name)
    group["legal_name"] = canonical_name or original_name
    group["aliases"] = list(dict.fromkeys(aliases))
    normalized_data["group"] = group
    return normalized_data
