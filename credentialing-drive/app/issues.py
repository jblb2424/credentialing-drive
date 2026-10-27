from datetime import date

from app.dates import parse_complete_date
from app.provider_types import provider_type_values_conflict
from app.provider_names import name_values_equivalent, person_name_values_equivalent

EXPIRING_WINDOW_DAYS = 90

# Priority tiers: critical issues block credentialing progress, discrepancies need
# review next, and other absent data is follow-up work.
CRITICAL_MISSING_FIELDS = {
    "provider.npi",
    "group.type_2_npi",
    "licenses",
    "liability_insurance",
}

# These requirements cover the provider record itself. Fields that can be legitimately
# inapplicable (for example, middle name or languages) are intentionally excluded.
REQUIRED_PROVIDER_FIELDS = (
    "name",
    "first_name",
    "last_name",
    "provider_type_or_credentials",
    "gender",
    "date_of_birth",
    "npi",
    "caqh_id",
    "address",
)

# Group issues intentionally start with the identifiers and contact fields that are
# required for credentialing operations. DBA and languages remain optional.
REQUIRED_GROUP_FIELDS = ("legal_name", "type_2_npi", "tax_id")
REQUIRED_GROUP_LOCATION_FIELDS = (
    "display_name", "type", "address", "phone", "email", "practice_hours", "faxes",
)

REQUIRED_COLLECTION_FIELDS = {
    "provider_locations": {
        "required": ("display_name", "type", "address", "phone", "email", "practice_hours", "faxes"),
        "aliases": {"display_name": ("display_name", "name", "location_name")},
    },
    "licenses": {
        "required": ("type", "license_number", "issue_date", "expiration_date", "state"),
        "aliases": {
            "type": ("type", "license_type"),
            "license_number": ("license_number", "number"),
            "issue_date": ("issue_date", "issued_date"),
            "expiration_date": ("expiration_date", "expires_at", "expiry_date"),
            "state": ("state", "issued_state"),
        },
    },
    "specialties": {
        "required": ("name", "board_certified"),
        "aliases": {
            "name": ("name", "specialty"),
            "board_certified": ("board_certified", "is_board_certified"),
        },
    },
    "education": {
        "required": ("education_type", "institution_name", "degree", "specialty", "start_date", "end_date"),
        "aliases": {
            "education_type": ("education_type", "type"),
            "institution_name": ("institution_name", "institution"),
            "degree": ("degree", "degree_received"),
        },
    },
    "liability_insurance": {
        "required": (
            "insurance_type", "carrier_name", "policy_number", "effective_date", "expiration_date",
            "claim_amount", "aggregate_amount",
        ),
        "aliases": {"insurance_type": ("insurance_type", "type")},
    },
    "payer_enrollments": {
        "required": ("payer_name", "group_name", "location_names", "enrollment_status"),
        "aliases": {
            "payer_name": ("payer_name", "name"),
            "group_name": ("group_name", "practice_name"),
            "location_names": ("location_names", "locations"),
            "enrollment_status": ("enrollment_status", "status"),
        },
    },
}

FIELD_LABELS = {
    "provider_type_or_credentials": "provider type or credentials",
    "display_name": "location name",
    "practice_hours": "practice hours",
    "license_number": "license number",
    "issue_date": "issue date",
    "expiration_date": "expiration date",
    "board_certified": "board certification status",
    "certification_date": "certification date",
    "certifying_board": "certifying board",
    "education_type": "education type",
    "institution_name": "institution name",
    "start_date": "start date",
    "end_date": "end date",
    "insurance_type": "insurance type",
    "carrier_name": "carrier name",
    "policy_number": "policy number",
    "effective_date": "effective date",
    "claim_amount": "claim amount",
    "aggregate_amount": "aggregate amount",
    "payer_name": "payer name",
    "group_name": "practice",
    "location_names": "participating locations",
    "enrollment_status": "enrollment status",
    "caqh_id": "CAQH ID",
    "npi": "NPI",
    "type_2_npi": "Type 2 NPI",
    "tax_id": "tax ID",
    "legal_name": "legal name",
}

COLLECTION_LABELS = {
    "provider_locations": "provider location information",
    "licenses": "license information",
    "specialties": "specialty information",
    "education": "education information",
    "liability_insurance": "liability insurance information",
    "payer_enrollments": "payer enrollment information",
}

GROUP_COLLECTION_LABELS = {
    "locations": "practice location information",
}


def parse_expiration_date(value):
    return parse_complete_date(value)


def revision_field_changes(value, prefix=""):
    if not isinstance(value, dict):
        return
    if "current" in value and "previous" in value:
        yield prefix, value
        return
    for field_name, nested_value in value.items():
        field_path = f"{prefix}.{field_name}" if prefix else field_name
        yield from revision_field_changes(nested_value, field_path)


def build_issue(issue_id, issue_type, severity, affected_fields, **details):
    return {
        "id": issue_id,
        "type": issue_type,
        "severity": severity,
        "affected_fields": affected_fields,
        **details,
    }


def provider_revisions(provider_ref):
    """Load and order a provider's revision history once per issue calculation."""
    revisions = [
        {"id": snapshot.id, "data": snapshot.to_dict() or {}}
        for snapshot in provider_ref.collection("revisions").stream()
    ]
    return sorted(
        revisions,
        key=lambda revision: revision_sort_key(revision["data"]),
        reverse=True,
    )


def discrepancy_issues(revisions):
    issues = []
    for revision_record in revisions:
        revision = revision_record["data"]
        # A human-confirmed correction is not a competing document source. Its
        # provenance remains visible in Activity, but it must not create a false
        # discrepancy against the prior imported value.
        if revision.get("source_type") == "manual":
            continue
        for field_path, change in revision_field_changes(revision.get("changes") or {}):
            if change["current"] == change["previous"]:
                continue
            if field_path == "provider.provider_type" and not provider_type_values_conflict(
                change["previous"], change["current"]
            ):
                continue
            if field_path == "provider.name" and name_values_equivalent(
                change["previous"], change["current"]
            ):
                continue
            if field_path in {
                "provider.first_name",
                "provider.middle_name",
                "provider.last_name",
            } and person_name_values_equivalent(change["previous"], change["current"]):
                continue
            previous_file = (revision.get("previous_files") or {}).get(field_path) or {}
            issues.append(
                build_issue(
                    f"discrepancy-{revision_record['id']}-{field_path}",
                    "discrepancy",
                    "high",
                    [field_path],
                    previous_value=change["previous"],
                    current_value=change["current"],
                    revision_id=revision_record["id"],
                    file_name=revision.get("file_name"),
                    drive_file_id=revision.get("drive_file_id"),
                    previous_file_name=previous_file.get("file_name"),
                    previous_drive_file_id=previous_file.get("drive_file_id"),
                )
            )
    return issues


def revision_source(revision):
    file_name = revision.get("file_name")
    if not file_name:
        return {}
    return {
        "file_name": file_name,
        "drive_file_id": revision.get("drive_file_id"),
    }


def revision_sort_key(revision):
    recorded_at = revision.get("recorded_at")
    return recorded_at.timestamp() if hasattr(recorded_at, "timestamp") else 0


def latest_revision_source(revisions):
    for revision_record in revisions:
        source = revision_source(revision_record["data"])
        if source:
            return source
    return {}


def expiration_source(revisions, candidate):
    """Find the revision that introduced the credential behind an expiration issue."""
    for revision_record in revisions:
        revision = revision_record["data"]
        additions = (revision.get("changes") or {}).get(candidate["collection_name"], {}).get(
            "added", []
        )
        if candidate["item"] in additions:
            return revision_source(revision)
    return latest_revision_source(revisions)


def first_present(item, field_names):
    for field_name in field_names:
        value = item.get(field_name)
        if value not in (None, ""):
            return value
    return None


def credential_label(category, item):
    if category == "license":
        parts = [item.get("state"), item.get("type") or item.get("license_type")]
        label = " ".join(str(part) for part in parts if part)
        number = first_present(item, ("license_number", "number"))
        return f"{label or 'License'} #{number}" if number else label or "License"

    if category == "board_certification":
        specialty = item.get("name") or item.get("specialty")
        board = item.get("certifying_board") or item.get("board")
        if specialty and board:
            return f"{specialty} ({board})"
        return specialty or board or "Board certification"

    if category == "liability_insurance":
        carrier = item.get("carrier_name") or item.get("carrier")
        policy_number = first_present(item, ("policy_number", "number"))
        if carrier and policy_number:
            return f"{carrier} policy #{policy_number}"
        return carrier or (f"Policy #{policy_number}" if policy_number else "Liability insurance")

    return "Unclassified credential"


def structured_expiration_candidates(provider):
    sources = (
        ("licenses", "license", ("expiration_date", "expires_at", "expiry_date")),
        (
            "specialties",
            "board_certification",
            ("expiration_date", "expires_at", "board_expiration_date"),
        ),
        (
            "liability_insurance",
            "liability_insurance",
            ("expiration_date", "expires_at", "policy_expiration_date"),
        ),
    )
    candidates = []

    for collection_name, category, expiration_fields in sources:
        for index, item in enumerate(provider.get(collection_name) or []):
            if not isinstance(item, dict):
                continue

            raw_expiration = first_present(item, expiration_fields)
            expiration_date = parse_expiration_date(raw_expiration)
            if not expiration_date:
                continue

            expiration_field = next(
                field_name
                for field_name in expiration_fields
                if item.get(field_name) == raw_expiration
            )
            candidates.append(
                {
                    "category": category,
                    "label": credential_label(category, item),
                    "identifier": first_present(
                        item, ("license_number", "policy_number", "number")
                    ),
                    "expiration_date": expiration_date,
                    "field_path": f"{collection_name}[{index}].{expiration_field}",
                    "index": index,
                    "collection_name": collection_name,
                    "item": item,
                }
            )

    return candidates


def expiration_record(candidate):
    expiration_date = candidate["expiration_date"]
    days_until_expiration = (expiration_date - date.today()).days
    category = candidate["category"]
    issue_prefix = "expired" if days_until_expiration < 0 else "expiring"
    if category == "unclassified":
        issue_id = f"{issue_prefix}-{expiration_date.isoformat()}"
    else:
        issue_id = f"{issue_prefix}-{category}-{candidate['index']}-{expiration_date.isoformat()}"

    record = {
        "id": issue_id,
        "type": "expired" if days_until_expiration < 0 else "expiring",
        "severity": "critical" if days_until_expiration < 0 else "medium",
        "affected_fields": [candidate["field_path"]],
        "expiration_date": expiration_date.isoformat(),
        "credential_category": category,
        "credential_label": candidate["label"],
        "credential_identifier": candidate.get("identifier"),
    }
    if days_until_expiration < 0:
        record["days_past_expiration"] = abs(days_until_expiration)
    else:
        record["days_until_expiration"] = days_until_expiration
        if days_until_expiration > EXPIRING_WINDOW_DAYS:
            record["type"] = "current"
            record["severity"] = "low"
    return record


def expiration_candidates(provider):
    candidates = structured_expiration_candidates(provider)
    structured_dates = {candidate["expiration_date"] for candidate in candidates}

    for raw_value in provider.get("expiration_dates") or []:
        expiration_date = parse_expiration_date(raw_value)
        if not expiration_date or expiration_date in structured_dates:
            continue
        candidates.append(
            {
                "category": "unclassified",
                "label": "Unclassified credential",
                "expiration_date": expiration_date,
                "field_path": "expiration_dates",
                "index": 0,
                "collection_name": "expiration_dates",
                "item": raw_value,
            }
        )
    return candidates


def expiration_issue(candidate, source):
    record = expiration_record(candidate)
    if record["type"] == "current":
        return None
    return build_issue(
        record.pop("id"),
        record.pop("type"),
        record.pop("severity"),
        record.pop("affected_fields"),
        **record,
        **source,
    )


def expiration_records(provider):
    return [expiration_record(candidate) for candidate in expiration_candidates(provider)]


def expiration_issues(revisions, provider):
    issues = []
    for candidate in expiration_candidates(provider):
        issue = expiration_issue(candidate, expiration_source(revisions, candidate))
        if issue:
            issues.append(issue)
    return issues


def missing_data_issues(_revisions, provider):
    profile = provider.get("provider") or {}
    missing_fields = []

    for field_name in REQUIRED_PROVIDER_FIELDS:
        if field_name == "provider_type_or_credentials":
            if has_required_value(profile.get("provider_type")) or has_required_value(
                profile.get("credentials")
            ):
                continue
        elif has_required_value(profile.get(field_name)):
            continue
        missing_fields.append(f"provider.{field_name}")

    for collection_name, requirements in REQUIRED_COLLECTION_FIELDS.items():
        records = provider.get(collection_name) or []
        if not records:
            missing_fields.append(collection_name)
            continue
        for index, record in enumerate(records):
            for field_name in requirements["required"]:
                aliases = requirements.get("aliases", {}).get(field_name, (field_name,))
                if any(
                    has_required_value(record.get(alias))
                    for alias in aliases
                ) if isinstance(record, dict) else False:
                    continue
                missing_fields.append(f"{collection_name}[{index}].{field_name}")

            if collection_name == "specialties" and is_board_certified(record):
                for field_name in ("certification_date", "expiration_date", "certifying_board"):
                    aliases = {
                        "certification_date": ("certification_date", "certified_date"),
                        "expiration_date": ("expiration_date", "expires_at"),
                        "certifying_board": ("certifying_board", "board"),
                    }[field_name]
                    if not any(has_required_value(record.get(alias)) for alias in aliases):
                        missing_fields.append(f"{collection_name}[{index}].{field_name}")

    return [
        build_issue(
            f"missing-required-{field_path}",
            "missing_data",
            missing_data_severity(field_path),
            [field_path],
            display_label=missing_field_display_label(field_path, provider),
        )
        for field_path in missing_fields
    ]


def group_missing_data_issues(group):
    missing_fields = []
    for field_name in REQUIRED_GROUP_FIELDS:
        if not has_group_required_value(group.get(field_name)):
            missing_fields.append(f"group.{field_name}")

    locations = group.get("locations") or []
    if not locations:
        missing_fields.append("locations")
    else:
        for index, location in enumerate(locations):
            for field_name in REQUIRED_GROUP_LOCATION_FIELDS:
                if isinstance(location, dict) and has_group_required_value(location.get(field_name)):
                    continue
                missing_fields.append(f"locations[{index}].{field_name}")

    return [
        build_issue(
            f"missing-required-{field_path}",
            "missing_data",
            missing_data_severity(field_path),
            [field_path],
            display_label=group_missing_field_display_label(field_path, group),
        )
        for field_path in missing_fields
    ]


def missing_data_severity(field_path):
    return "critical" if field_path in CRITICAL_MISSING_FIELDS else "medium"


def has_required_value(value):
    return value not in (None, "", [], {})


def has_group_required_value(value):
    if not has_required_value(value):
        return False
    return not isinstance(value, str) or value.strip().lower() not in {"unknown", "not captured"}


def is_board_certified(record):
    if not isinstance(record, dict):
        return False
    value = record.get("board_certified", record.get("is_board_certified"))
    return value is True or str(value).strip().lower() in {"true", "yes", "y"}


def missing_field_display_label(field_path, provider):
    """Create an operator-facing label while retaining the precise API field path."""
    if field_path.startswith("provider."):
        field_name = field_path.removeprefix("provider.")
        return field_label(field_name)

    if "[" not in field_path:
        return COLLECTION_LABELS.get(field_path, field_label(field_path))

    collection_name, indexed_field = field_path.split("[", maxsplit=1)
    index_text, field_name = indexed_field.split("].", maxsplit=1)
    records = provider.get(collection_name) or []
    try:
        record = records[int(index_text)]
    except (IndexError, ValueError):
        record = None
    record_label = missing_record_label(collection_name, record)
    return f"{field_label(field_name)} for {record_label}"


def missing_record_label(collection_name, record):
    if not isinstance(record, dict):
        return COLLECTION_LABELS.get(collection_name, "this record")

    label_keys = {
        "provider_locations": ("display_name", "name", "location_name", "address"),
        "licenses": ("type", "license_type", "license_number", "number"),
        "specialties": ("name", "specialty"),
        "education": ("institution_name", "institution", "education_type", "type"),
        "liability_insurance": ("carrier_name", "policy_number", "insurance_type", "type"),
        "payer_enrollments": ("payer_name", "name", "group_name", "practice_name"),
    }
    for key in label_keys.get(collection_name, ()):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return COLLECTION_LABELS.get(collection_name, "this record")


def field_label(field_name):
    return FIELD_LABELS.get(field_name, field_name.replace("_", " "))


def group_missing_field_display_label(field_path, group):
    if field_path.startswith("group."):
        return field_label(field_path.removeprefix("group."))
    if field_path == "locations":
        return GROUP_COLLECTION_LABELS["locations"]

    collection_name, indexed_field = field_path.split("[", maxsplit=1)
    index_text, field_name = indexed_field.split("].", maxsplit=1)
    locations = group.get(collection_name) or []
    try:
        location = locations[int(index_text)]
    except (IndexError, ValueError):
        location = None
    location_name = group_location_label(location)
    return f"{field_label(field_name)} for {location_name or 'this practice location'}"


def group_location_label(location):
    """Use a readable address when older imports stored it in display_name."""
    if not isinstance(location, dict):
        return None
    for field_name in ("display_name", "name", "location_name"):
        value = location.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    address = location.get("address") or location.get("display_name")
    if isinstance(address, str) and address.strip():
        return address.strip()
    if isinstance(address, dict):
        street = address.get("line1") or address.get("street") or address.get("address")
        locality = ", ".join(
            str(value) for value in (address.get("city"), address.get("state")) if value
        )
        return ", ".join(value for value in (street, locality) if value)
    return None


def calculate_provider_issues(provider_ref, provider):
    revisions = provider_revisions(provider_ref)
    return (
        discrepancy_issues(revisions)
        + expiration_issues(revisions, provider)
        + missing_data_issues(revisions, provider)
    )


def calculate_group_issues(group_ref, group):
    revisions = provider_revisions(group_ref)
    return discrepancy_issues(revisions) + group_missing_data_issues(group)
