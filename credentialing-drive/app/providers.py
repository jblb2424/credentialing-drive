import hashlib
import json
import logging
import os
import re
import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException
from google.cloud import bigquery, firestore

from app.config import (
    BIGQUERY_REPORTING_DATASET, BIGQUERY_REPORTING_TABLE, DEFAULT_ENTITY_ID,
    DEFAULT_ENTITY_NAME, ENTITY_COLLECTION, GROUP_COLLECTION, LOCATION_COLLECTION,
    PAYER_ENROLLMENT_COLLECTION, PROVIDER_COLLECTION, PROVIDER_GROUP_MEMBERSHIP_COLLECTION,
    PROVIDER_IDENTITY_COLLECTION,
)
from app.connections import get_firestore_client, get_project_id
from app.issues import calculate_provider_issues, expiration_records
from app.provider_types import normalize_provider_type

logger = logging.getLogger(__name__)



def serialize_provider(snapshot):
    provider = snapshot.to_dict() or {}
    profile = provider.get("provider") or {}
    provider["provider"] = enrich_provider_name(profile)
    issues = calculate_provider_issues(snapshot.reference, provider)
    return {"id": snapshot.id, **provider, "issues": issues, "issue_count": len(issues)}


def get_entity_ref(client, entity_id=DEFAULT_ENTITY_ID):
    entity_ref = client.collection(ENTITY_COLLECTION).document(entity_id)
    if entity_id == DEFAULT_ENTITY_ID:
        entity_ref.set(
            {
                "name": DEFAULT_ENTITY_NAME,
                "legal_name": DEFAULT_ENTITY_NAME,
                "status": "sandbox",
            },
            merge=True,
        )
    return entity_ref


def serialize_document(snapshot):
    return {"id": snapshot.id, **(snapshot.to_dict() or {})}


def get_entity(entity_id=DEFAULT_ENTITY_ID):
    snapshot = get_entity_ref(get_firestore_client(), entity_id).get()
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="Entity not found")
    return serialize_document(snapshot)


def list_groups(limit, entity_id=DEFAULT_ENTITY_ID):
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    snapshots = entity_ref.collection(GROUP_COLLECTION).limit(limit).stream()
    return [serialize_document(snapshot) for snapshot in snapshots]


def get_group(group_id, entity_id=DEFAULT_ENTITY_ID):
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    group_ref = entity_ref.collection(GROUP_COLLECTION).document(group_id)
    snapshot = group_ref.get()
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="Practice not found")

    locations = [
        serialize_document(location_snapshot)
        for location_snapshot in group_ref.collection(LOCATION_COLLECTION).stream()
    ]
    memberships = entity_ref.collection(PROVIDER_GROUP_MEMBERSHIP_COLLECTION).where(
        "group_id", "==", group_id
    ).stream()
    provider_ids = [membership.to_dict().get("provider_id") for membership in memberships]
    return {
        **serialize_document(snapshot),
        "locations": locations,
        "provider_count": len([provider_id for provider_id in provider_ids if provider_id]),
    }


def list_providers(limit, entity_id=DEFAULT_ENTITY_ID):
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    snapshots = entity_ref.collection(PROVIDER_COLLECTION).limit(limit).stream()
    return [serialize_provider(snapshot) for snapshot in snapshots]


def list_provider_expirations(limit, entity_id=DEFAULT_ENTITY_ID):
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    records = []
    for snapshot in entity_ref.collection(PROVIDER_COLLECTION).stream():
        provider = snapshot.to_dict() or {}
        profile = enrich_provider_name(provider.get("provider") or {})
        for record in expiration_records(provider):
            records.append(
                {
                    **record,
                    "provider_id": snapshot.id,
                    "provider_name": profile.get("name") or "Unnamed provider",
                    "provider_npi": profile.get("npi"),
                }
            )

    def sort_key(record):
        expiration_date = date.fromisoformat(record["expiration_date"])
        if record["type"] == "expired":
            return (0, -expiration_date.toordinal())
        return (1, expiration_date.toordinal())

    return sorted(records, key=sort_key)[:limit]


def get_provider(provider_id, entity_id=DEFAULT_ENTITY_ID):
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    snapshot = entity_ref.collection(PROVIDER_COLLECTION).document(provider_id).get()
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="Provider not found")
    provider = serialize_provider(snapshot)
    provider["affiliations"] = get_provider_affiliations(entity_ref, provider_id)
    return provider


def get_provider_issue(provider_id, issue_id, entity_id=DEFAULT_ENTITY_ID):
    provider = get_provider(provider_id, entity_id)
    issue = next((issue for issue in provider["issues"] if issue["id"] == issue_id), None)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    return {
        "issue": issue,
        "provider": {
            "id": provider["id"],
            "name": provider["provider"].get("name"),
            "npi": provider["provider"].get("npi"),
        },
    }


def get_provider_affiliations(entity_ref, provider_id):
    """Resolve provider-to-practice relationships for the provider detail response."""
    memberships = entity_ref.collection(PROVIDER_GROUP_MEMBERSHIP_COLLECTION).where(
        "provider_id", "==", provider_id
    ).stream()
    affiliations = []
    for membership_snapshot in memberships:
        membership = membership_snapshot.to_dict() or {}
        group_ref = entity_ref.collection(GROUP_COLLECTION).document(membership["group_id"])
        group_snapshot = group_ref.get()
        locations = []
        for location_id in membership.get("location_ids") or []:
            location_snapshot = group_ref.collection(LOCATION_COLLECTION).document(location_id).get()
            if location_snapshot.exists:
                locations.append(serialize_document(location_snapshot))
        payer_enrollments = [
            serialize_document(snapshot)
            for snapshot in membership_snapshot.reference.collection(
                PAYER_ENROLLMENT_COLLECTION
            ).stream()
        ]
        affiliations.append(
            {
                "id": membership_snapshot.id,
                **membership,
                "group": serialize_document(group_snapshot) if group_snapshot.exists else None,
                "locations": locations,
                "payer_enrollments": payer_enrollments,
            }
        )
    return affiliations


def normalized_key(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def name_parts(name):
    """Parse conventional credentialing name formats without guessing ambiguous names."""
    if not isinstance(name, str) or not name.strip():
        return {}
    normalized_name = name.strip()
    if "," in normalized_name:
        last_name, given_names = (part.strip() for part in normalized_name.split(",", 1))
        parts = given_names.split()
        return {
            "first_name": parts[0] if parts else None,
            "middle_name": " ".join(parts[1:]) or None,
            "last_name": last_name or None,
        }
    parts = normalized_name.split()
    if len(parts) >= 2:
        return {
            "first_name": parts[0],
            "middle_name": " ".join(parts[1:-1]) or None,
            "last_name": parts[-1],
        }
    return {}


def enrich_provider_name(profile):
    profile = dict(profile or {})
    parsed_parts = name_parts(profile.get("name"))
    for field_name, parsed_value in parsed_parts.items():
        if not profile.get(field_name) and parsed_value:
            profile[field_name] = parsed_value
    return profile


def normalize_group_data(extraction):
    group = extraction.get("group") or {}
    if not isinstance(group, dict):
        group = {"legal_name": str(group)}

    type_2_npi = (
        group.get("type_2_npi") or group.get("npi") or extraction.get("type_2_npi")
    )
    tax_id = (
        group.get("tax_id")
        or group.get("tax_identification_number")
        or extraction.get("tax_id")
    )
    return {
        "legal_name": group.get("legal_name") or group.get("name") or extraction.get("group_name"),
        "type_2_npi": str(type_2_npi) if type_2_npi else None,
        "tax_id": str(tax_id) if tax_id else None,
        "dba": group.get("dba") or group.get("doing_business_as") or extraction.get("dba"),
        "w9": group.get("w9") if isinstance(group.get("w9"), dict) else None,
    }


def normalize_provider_data(extraction):
    provider = extraction.get("provider") or {}
    if not isinstance(provider, dict):
        provider = {"name": str(provider)}

    name = (
        provider.get("name")
        or extraction.get("provider_name")
        or " ".join(
            part
            for part in (provider.get("first_name"), provider.get("middle_name"), provider.get("last_name"))
            if part
        )
        or None
    )
    npi = valid_npi(provider.get("npi") or extraction.get("npi"))
    credentials = provider.get("credentials") or extraction.get("credentials")
    profile = enrich_provider_name({
        "name": name,
        "first_name": provider.get("first_name"),
        "middle_name": provider.get("middle_name"),
        "last_name": provider.get("last_name"),
        # A document may call someone a "Physician" or "Practitioner". Those
        # are roles, not a stable credential type, so only retain a controlled
        # designation and fall back to the document's credentials when present.
        "provider_type": normalize_provider_type(
            provider.get("provider_type") or provider.get("type")
        ) or normalize_provider_type(credentials),
        "credentials": credentials,
        "gender": provider.get("gender"),
        "date_of_birth": provider.get("date_of_birth") or provider.get("dob"),
        "npi": npi,
        "caqh_id": provider.get("caqh_id") or provider.get("caqh"),
        "address": provider.get("address") or extraction.get("provider_address"),
    })
    return {
        "entity_name": extraction.get("entity_name"),
        "group_name": extraction.get("group_name"),
        "group": normalize_group_data(extraction),
        "provider": profile,
        "locations": as_list(extraction.get("locations")),
        "provider_locations": as_list(extraction.get("provider_locations")),
        "payers": as_list(extraction.get("payers")),
        "payer_enrollments": as_list(extraction.get("payer_enrollments")),
        "licenses": as_list(extraction.get("licenses")),
        "specialties": as_list(extraction.get("specialties")),
        "education": as_list(extraction.get("education")),
        "liability_insurance": as_list(extraction.get("liability_insurance")),
        "expiration_dates": as_list(extraction.get("expiration_dates")),
        "summary": extraction.get("summary"),
    }


def valid_npi(value):
    digits = re.sub(r"\D", "", str(value or ""))
    # Do not let placeholders such as 0000000000 become durable identity aliases.
    return digits if len(digits) == 10 and len(set(digits)) > 1 else None


def provider_identity_keys(provider):
    profile = enrich_provider_name(provider.get("provider") or {})
    keys = []
    npi = valid_npi(profile.get("npi"))
    caqh_id = normalized_key(profile.get("caqh_id"))
    first_name = normalized_key(profile.get("first_name"))
    middle_name = normalized_key(profile.get("middle_name"))
    last_name = normalized_key(profile.get("last_name"))

    if npi:
        keys.append(f"npi-{npi}")
    if caqh_id:
        keys.append(f"caqh-{caqh_id}")
    if first_name and last_name:
        # The client is already the identity boundary. Source entity names vary by
        # document and must not split the same clinician into separate records.
        keys.append(f"name-{first_name}-{last_name}")
        if middle_name:
            keys.append(f"name-{first_name}-{middle_name}-{last_name}")
    return keys


def resolve_provider_id(entity_ref, provider):
    identity_keys = provider_identity_keys(provider)
    if not identity_keys:
        return None

    provider_ids = {
        snapshot.to_dict()["provider_id"]
        for identity_key in identity_keys
        if (snapshot := entity_ref.collection(PROVIDER_IDENTITY_COLLECTION).document(identity_key).get()).exists
    }
    if len(provider_ids) > 1:
        logger.warning("Ambiguous provider identity aliases: %s", identity_keys)
        return None

    # Names, NPIs, and CAQH IDs are aliases used to match later imports. The
    # provider itself receives an opaque Firestore-generated ID.
    provider_id = next(iter(provider_ids), None)
    if not provider_id:
        provider_id = entity_ref.collection(PROVIDER_COLLECTION).document().id
    for identity_key in identity_keys:
        entity_ref.collection(PROVIDER_IDENTITY_COLLECTION).document(identity_key).set(
            {"provider_id": provider_id}, merge=True
        )
    return provider_id


def merge_unique(existing, incoming):
    values = list(existing or [])
    for value in incoming or []:
        if value and value not in values:
            values.append(value)
    return values


def as_list(value):
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def has_value(value):
    return value not in (None, "", [], {})


def merge_profile(existing, incoming):
    profile = {}
    for field_name in set(existing) | set(incoming):
        incoming_value = incoming.get(field_name)
        existing_value = existing.get(field_name)
        if isinstance(existing_value, dict) and isinstance(incoming_value, dict):
            profile[field_name] = merge_profile(existing_value, incoming_value)
        else:
            profile[field_name] = incoming_value if has_value(incoming_value) else existing_value
    return profile


def location_display_name(location):
    if isinstance(location, str):
        return location
    if not isinstance(location, dict):
        return None
    name = (
        location.get("display_name")
        or location.get("name")
        or location.get("location_name")
    )
    if isinstance(name, str) and name.strip():
        return name
    if isinstance(name, dict):
        return (
            name.get("display_name")
            or name.get("name")
            or name.get("line1")
            or name.get("street")
            or name.get("address")
        )
    address = location.get("address")
    if isinstance(address, str):
        return address
    if isinstance(address, dict):
        return address.get("line1") or address.get("street")
    return None


def present_fields(values):
    return {field_name: value for field_name, value in values.items() if has_value(value)}


def resolve_group_ref(entity_ref, group):
    group = group or {}
    group_name = group.get("legal_name") or group.get("name")
    if not group_name:
        return None

    group_key = normalized_key(group_name)
    identity_ref = entity_ref.collection("group_identities").document(group_key)
    identity = identity_ref.get()
    if identity.exists:
        group_id = identity.to_dict()["group_id"]
    else:
        group_id = entity_ref.collection(GROUP_COLLECTION).document().id
        identity_ref.set({"group_id": group_id})

    group_ref = entity_ref.collection(GROUP_COLLECTION).document(group_id)
    group_ref.set(
        present_fields(
            {
                "legal_name": group_name,
                "type_2_npi": group.get("type_2_npi"),
                "tax_id": group.get("tax_id"),
                "dba": group.get("dba"),
                "w9": group.get("w9"),
            }
        ),
        merge=True,
    )
    return group_ref


def upsert_normalized_group(normalized_data):
    """Persist a practice even when its source document contains no provider."""
    entity_ref = get_entity_ref(get_firestore_client())
    group_ref = resolve_group_ref(entity_ref, normalized_data.get("group"))
    if not group_ref:
        return None
    upsert_group_locations(group_ref, normalized_data.get("locations"))
    return group_ref.id


def upsert_group_locations(group_ref, locations):
    location_ids = []
    for location in locations or []:
        location_name = location_display_name(location)
        location_key = normalized_key(location_name)
        if not location_key:
            continue
        identity_ref = group_ref.collection("location_identities").document(location_key)
        identity = identity_ref.get()
        if identity.exists:
            location_id = identity.to_dict()["location_id"]
        else:
            location_id = group_ref.collection(LOCATION_COLLECTION).document().id
            identity_ref.set({"location_id": location_id})
        source = location if isinstance(location, dict) else {}
        embedded_address = source.get("display_name") if isinstance(source.get("display_name"), dict) else None
        group_ref.collection(LOCATION_COLLECTION).document(location_id).set(
            present_fields(
                {
                    "display_name": location_name,
                    "type": source.get("type") or "unknown",
                    "address": source.get("address") or embedded_address,
                    "phone": source.get("phone"),
                    "email": source.get("email"),
                    "practice_hours": source.get("practice_hours"),
                    "faxes": source.get("faxes"),
                    "languages": source.get("languages"),
                }
            ),
            merge=True,
        )
        location_ids.append(location_id)
    return location_ids


def upsert_provider_group_membership(entity_ref, provider_id, group_ref, provider):
    if not group_ref:
        return None

    membership_ref = entity_ref.collection(PROVIDER_GROUP_MEMBERSHIP_COLLECTION).document(
        f"{provider_id}-{group_ref.id}"
    )
    location_ids = upsert_group_locations(group_ref, provider.get("locations"))
    membership_ref.set(
        {
            "provider_id": provider_id,
            "group_id": group_ref.id,
            "provider_type": provider["provider"].get("credentials"),
            "location_ids": location_ids,
        },
        merge=True,
    )
    payer_entries = [{"payer_name": payer_name} for payer_name in provider.get("payers") or []]
    payer_entries.extend(provider.get("payer_enrollments") or [])
    for payer in payer_entries:
        payer = payer if isinstance(payer, dict) else {"payer_name": payer}
        payer_name = payer.get("payer_name") or payer.get("name")
        payer_key = normalized_key(payer_name)
        if payer_key:
            membership_ref.collection(PAYER_ENROLLMENT_COLLECTION).document(payer_key).set(
                present_fields(
                    {
                        "payer_name": payer_name,
                        "payer_key": payer_key,
                        "status": payer.get("enrollment_status") or payer.get("status") or "unknown",
                        "participating_location_ids": location_ids,
                        "source_status": payer.get("source_status"),
                    }
                ),
                merge=True,
            )
    return membership_ref


def reporting_values(values):
    return [
        json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
        for value in values or []
        if value is not None
    ]


def sync_provider_to_bigquery(provider_id, provider):
    project_id = get_project_id()
    dataset = os.environ.get("BIGQUERY_REPORTING_DATASET", BIGQUERY_REPORTING_DATASET)
    table_id = f"{project_id}.{dataset}.{BIGQUERY_REPORTING_TABLE}"
    profile = provider["provider"]
    row = {
        "provider_id": provider_id,
        "provider_name": profile.get("name"),
        "npi": profile.get("npi"),
        "credentials": profile.get("credentials"),
        "licenses": reporting_values(provider.get("licenses")),
        "expiration_dates": reporting_values(provider.get("expiration_dates")),
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
    errors = bigquery.Client(project=project_id).insert_rows_json(
        table_id, [row], row_ids=[f"{provider_id}-{uuid.uuid4()}"]
    )
    if errors:
        raise RuntimeError(f"BigQuery provider sync failed: {errors}")


def provider_changes(existing, updated):
    changes = {}
    existing_profile = existing.get("provider") or {}
    updated_profile = updated.get("provider") or {}
    profile_changes = {}
    for field_name in sorted(set(existing_profile) | set(updated_profile)):
        previous_value = existing_profile.get(field_name)
        current_value = updated_profile.get(field_name)
        if not has_value(current_value) or previous_value == current_value:
            continue
        profile_changes[field_name] = {"current": current_value}
        if previous_value is not None:
            profile_changes[field_name]["previous"] = previous_value
    if profile_changes:
        changes["provider"] = profile_changes

    for field_name in (
        "provider_locations",
        "payer_enrollments",
        "licenses",
        "specialties",
        "education",
        "liability_insurance",
        "expiration_dates",
    ):
        added_values = [
            value for value in updated.get(field_name, []) if value not in existing.get(field_name, [])
        ]
        if added_values:
            changes[field_name] = {"added": added_values}
    return changes


def record_provider_revision(provider_ref, metadata, changes, document_category, previous_files):
    revision = {
        "drive_file_id": metadata["id"],
        "file_name": metadata.get("name"),
        "document_category": document_category,
        "changes": changes,
        "recorded_at": firestore.SERVER_TIMESTAMP,
    }
    if previous_files:
        # A revision can update multiple fields whose previous values came from
        # different uploads, so retain lineage by field path.
        revision["previous_files"] = previous_files
    provider_ref.collection("revisions").document(f"drive-{metadata['id']}").set(
        revision,
        merge=True,
    )


def scalar_field_changes(changes):
    for field_name, change in (changes.get("provider") or {}).items():
        if "current" in change:
            yield f"provider.{field_name}", change


def source_metadata(metadata):
    return {
        "file_name": metadata.get("name") or metadata.get("file_name"),
        "drive_file_id": metadata.get("id") or metadata.get("drive_file_id"),
    }


def field_provenance_id(field_path):
    return hashlib.sha256(field_path.encode("utf-8")).hexdigest()


def previous_file_sources(provider_ref, changes):
    """Look up the source file for every scalar value this import replaces."""
    previous_files = {}
    for field_path, change in scalar_field_changes(changes):
        if "previous" not in change:
            continue
        provenance = provider_ref.collection("field_provenance").document(
            field_provenance_id(field_path)
        ).get()
        if not provenance.exists:
            continue
        provenance_data = provenance.to_dict() or {}
        # Do not attach an inaccurate file if provenance became stale.
        if provenance_data.get("value") != change["previous"]:
            logger.warning("No matching provenance for replaced field %s", field_path)
            continue
        source = provenance_data.get("source")
        if isinstance(source, dict) and source.get("file_name"):
            previous_files[field_path] = source
    return previous_files


def record_field_provenance(provider_ref, metadata, changes):
    current_source = source_metadata(metadata)
    for field_path, change in scalar_field_changes(changes):
        provider_ref.collection("field_provenance").document(
            field_provenance_id(field_path)
        ).set(
            {
                "field_path": field_path,
                "value": change["current"],
                "source": current_source,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )


def revision_sort_key(snapshot):
    recorded_at = (snapshot.to_dict() or {}).get("recorded_at")
    if hasattr(recorded_at, "timestamp"):
        return (recorded_at.timestamp(), snapshot.id)
    return (0, snapshot.id)


def backfill_revision_previous_files(entity_id=DEFAULT_ENTITY_ID):
    """Reconstruct prior-file lineage from ordered revision history where possible."""
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    revised_count = 0
    source_count = 0

    for provider_snapshot in entity_ref.collection(PROVIDER_COLLECTION).stream():
        known_values = {}
        revisions = sorted(
            provider_snapshot.reference.collection("revisions").stream(),
            key=revision_sort_key,
        )
        for revision_snapshot in revisions:
            revision = revision_snapshot.to_dict() or {}
            derived_sources = {}
            for field_path, change in scalar_field_changes(revision.get("changes") or {}):
                previous = known_values.get(field_path)
                if "previous" in change and previous and previous["value"] == change["previous"]:
                    derived_sources[field_path] = previous["source"]
                known_values[field_path] = {
                    "value": change["current"],
                    "source": source_metadata(revision),
                }

            if not derived_sources:
                continue
            # Preserve any lineage already recorded during normal ingestion.
            previous_files = {**derived_sources, **(revision.get("previous_files") or {})}
            revision_snapshot.reference.set({"previous_files": previous_files}, merge=True)
            revised_count += 1
            source_count += len(derived_sources)

    return {"revisions_updated": revised_count, "previous_files_added": source_count}


def upsert_normalized_provider(provider, metadata, document_category="other"):
    client = get_firestore_client()
    entity_ref = get_entity_ref(client)
    provider_id = resolve_provider_id(entity_ref, provider)
    if not provider_id:
        return None

    provider_ref = entity_ref.collection(PROVIDER_COLLECTION).document(provider_id)
    existing = provider_ref.get().to_dict() or {}
    existing_profile = existing.get("provider") or {}
    incoming_profile = provider["provider"]
    merged_profile = merge_profile(existing_profile, incoming_profile)
    canonical_provider = {
        "provider": merged_profile,
        "provider_locations": merge_unique(
            existing.get("provider_locations"), provider.get("provider_locations")
        ),
        "payer_enrollments": merge_unique(
            existing.get("payer_enrollments"), provider.get("payer_enrollments")
        ),
        "licenses": merge_unique(existing.get("licenses"), provider.get("licenses")),
        "specialties": merge_unique(existing.get("specialties"), provider.get("specialties")),
        "education": merge_unique(existing.get("education"), provider.get("education")),
        "liability_insurance": merge_unique(
            existing.get("liability_insurance"), provider.get("liability_insurance")
        ),
        "expiration_dates": merge_unique(
            existing.get("expiration_dates"), provider.get("expiration_dates")
        ),
    }
    group_ref = resolve_group_ref(entity_ref, provider.get("group"))
    upsert_provider_group_membership(entity_ref, provider_id, group_ref, provider)
    changes = provider_changes(existing, canonical_provider)
    if not changes:
        return provider_id

    previous_files = previous_file_sources(provider_ref, changes)
    provider_ref.set(canonical_provider, merge=True)
    record_provider_revision(
        provider_ref, metadata, changes, document_category, previous_files
    )
    record_field_provenance(provider_ref, metadata, changes)
    try:
        sync_provider_to_bigquery(provider_id, canonical_provider)
    except Exception:
        # Firestore remains the system of record if reporting is temporarily unavailable.
        logger.exception("BigQuery provider sync failed for provider_id=%s", provider_id)
    return provider_id


def normalized_profile_name(profile):
    profile = enrich_provider_name(profile)
    return " ".join(
        value for value in (
            profile.get("first_name"), profile.get("middle_name"), profile.get("last_name")
        ) if value
    ) or profile.get("name")


def ensure_compatible_provider_merge(provider_data):
    profiles = [enrich_provider_name(data.get("provider") or {}) for data in provider_data]
    name_keys = {
        f"{normalized_key(profile.get('first_name'))}-{normalized_key(profile.get('last_name'))}"
        for profile in profiles
        if profile.get("first_name") and profile.get("last_name")
    }
    if len(name_keys) != 1:
        raise HTTPException(status_code=409, detail="Providers do not share a canonical first and last name")

    for field_name, normalizer in (
        ("npi", valid_npi),
        ("caqh_id", normalized_key),
        ("date_of_birth", normalized_key),
        ("middle_name", normalized_key),
    ):
        values = {
            normalized
            for profile in profiles
            if (normalized := normalizer(profile.get(field_name)))
        }
        if len(values) > 1:
            raise HTTPException(status_code=409, detail=f"Providers have conflicting {field_name} values")


def copy_provider_subcollection(source_ref, target_ref, collection_name):
    for snapshot in source_ref.collection(collection_name).stream():
        target_ref.collection(collection_name).document(snapshot.id).set(
            snapshot.to_dict() or {}, merge=True
        )
        snapshot.reference.delete()


def merge_provider_memberships(entity_ref, source_provider_id, target_provider_id):
    memberships = entity_ref.collection(PROVIDER_GROUP_MEMBERSHIP_COLLECTION).where(
        "provider_id", "==", source_provider_id
    ).stream()
    for source_snapshot in memberships:
        source_membership = source_snapshot.to_dict() or {}
        group_id = source_membership.get("group_id")
        if not group_id:
            source_snapshot.reference.delete()
            continue
        target_ref = entity_ref.collection(PROVIDER_GROUP_MEMBERSHIP_COLLECTION).document(
            f"{target_provider_id}-{group_id}"
        )
        target_membership = target_ref.get().to_dict() or {}
        target_ref.set(
            {
                "provider_id": target_provider_id,
                "group_id": group_id,
                "provider_type": target_membership.get("provider_type") or source_membership.get("provider_type"),
                "location_ids": merge_unique(
                    target_membership.get("location_ids"), source_membership.get("location_ids")
                ),
            },
            merge=True,
        )
        copy_provider_subcollection(source_snapshot.reference, target_ref, PAYER_ENROLLMENT_COLLECTION)
        source_snapshot.reference.delete()


def merge_duplicate_providers(target_provider_id, duplicate_provider_ids, entity_id=DEFAULT_ENTITY_ID):
    duplicate_provider_ids = list(dict.fromkeys(duplicate_provider_ids))
    if not duplicate_provider_ids or target_provider_id in duplicate_provider_ids:
        raise HTTPException(status_code=400, detail="Provide one target and at least one distinct duplicate")

    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    target_ref = entity_ref.collection(PROVIDER_COLLECTION).document(target_provider_id)
    target_snapshot = target_ref.get()
    source_snapshots = [
        entity_ref.collection(PROVIDER_COLLECTION).document(provider_id).get()
        for provider_id in duplicate_provider_ids
    ]
    if not target_snapshot.exists or not all(snapshot.exists for snapshot in source_snapshots):
        raise HTTPException(status_code=404, detail="One or more provider records were not found")

    provider_data = [target_snapshot.to_dict() or {}] + [
        snapshot.to_dict() or {} for snapshot in source_snapshots
    ]
    ensure_compatible_provider_merge(provider_data)

    merged = provider_data[0]
    for source in provider_data[1:]:
        merged["provider"] = merge_profile(merged.get("provider") or {}, source.get("provider") or {})
        for field_name in (
            "provider_locations", "payer_enrollments", "licenses", "specialties",
            "education", "liability_insurance", "expiration_dates",
        ):
            merged[field_name] = merge_unique(merged.get(field_name), source.get(field_name))

    merged_profile = enrich_provider_name(merged.get("provider") or {})
    merged_profile["name"] = normalized_profile_name(merged_profile)
    merged_profile["npi"] = valid_npi(merged_profile.get("npi"))
    merged["provider"] = merged_profile
    target_ref.set(merged, merge=True)

    for source_snapshot in source_snapshots:
        copy_provider_subcollection(source_snapshot.reference, target_ref, "revisions")
        copy_provider_subcollection(source_snapshot.reference, target_ref, "field_provenance")
        merge_provider_memberships(entity_ref, source_snapshot.id, target_provider_id)
        source_snapshot.reference.delete()

    for identity_snapshot in entity_ref.collection(PROVIDER_IDENTITY_COLLECTION).stream():
        identity = identity_snapshot.to_dict() or {}
        if identity.get("provider_id") in duplicate_provider_ids:
            identity_snapshot.reference.set({"provider_id": target_provider_id}, merge=True)
    for identity_key in provider_identity_keys({"provider": merged_profile}):
        entity_ref.collection(PROVIDER_IDENTITY_COLLECTION).document(identity_key).set(
            {"provider_id": target_provider_id}, merge=True
        )

    try:
        sync_provider_to_bigquery(target_provider_id, merged)
    except Exception:
        logger.exception("BigQuery provider sync failed after duplicate reconciliation")
    return {"target_provider_id": target_provider_id, "merged_provider_ids": duplicate_provider_ids}


def delete_document_tree(document_ref):
    for collection in document_ref.collections():
        for snapshot in collection.stream():
            delete_document_tree(snapshot.reference)
    document_ref.delete()


def delete_provider(provider_id, entity_id=DEFAULT_ENTITY_ID):
    """Remove a provider and every provider-scoped record for a clean re-import."""
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    provider_ref = entity_ref.collection(PROVIDER_COLLECTION).document(provider_id)
    if not provider_ref.get().exists:
        raise HTTPException(status_code=404, detail="Provider not found")

    for membership in entity_ref.collection(PROVIDER_GROUP_MEMBERSHIP_COLLECTION).where(
        "provider_id", "==", provider_id
    ).stream():
        delete_document_tree(membership.reference)
    for identity in entity_ref.collection(PROVIDER_IDENTITY_COLLECTION).where(
        "provider_id", "==", provider_id
    ).stream():
        identity.reference.delete()
    delete_document_tree(provider_ref)
    return {"deleted_provider_id": provider_id}
