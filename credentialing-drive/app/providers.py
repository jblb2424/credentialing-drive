import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from google.cloud import bigquery, firestore

from app.config import (
    BIGQUERY_REPORTING_DATASET, BIGQUERY_REPORTING_TABLE, DEFAULT_ENTITY_ID,
    DEFAULT_ENTITY_NAME, ENTITY_COLLECTION, GROUP_COLLECTION, LOCATION_COLLECTION,
    PAYER_ENROLLMENT_COLLECTION, PROVIDER_COLLECTION, PROVIDER_GROUP_MEMBERSHIP_COLLECTION,
    PROVIDER_IDENTITY_COLLECTION,
)
from app.connections import get_firestore_client, get_project_id
from app.issues import calculate_provider_issues

logger = logging.getLogger(__name__)



def serialize_provider(snapshot):
    provider = snapshot.to_dict() or {}
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


def list_providers(limit, entity_id=DEFAULT_ENTITY_ID):
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    snapshots = entity_ref.collection(PROVIDER_COLLECTION).limit(limit).stream()
    return [serialize_provider(snapshot) for snapshot in snapshots]


def get_provider(provider_id, entity_id=DEFAULT_ENTITY_ID):
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    snapshot = entity_ref.collection(PROVIDER_COLLECTION).document(provider_id).get()
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="Provider not found")
    provider = serialize_provider(snapshot)
    provider["affiliations"] = get_provider_affiliations(entity_ref, provider_id)
    return provider


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
    npi = provider.get("npi") or extraction.get("npi")
    profile = {
        "name": name,
        "first_name": provider.get("first_name"),
        "middle_name": provider.get("middle_name"),
        "last_name": provider.get("last_name"),
        "provider_type": provider.get("provider_type") or provider.get("type"),
        "credentials": provider.get("credentials") or extraction.get("credentials"),
        "gender": provider.get("gender"),
        "date_of_birth": provider.get("date_of_birth") or provider.get("dob"),
        "npi": str(npi) if npi else None,
        "caqh_id": provider.get("caqh_id") or provider.get("caqh"),
        "address": provider.get("address") or extraction.get("provider_address"),
    }
    return {
        "entity_name": extraction.get("entity_name"),
        "group_name": extraction.get("group_name"),
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


def resolve_provider_id(entity_ref, provider):
    name_key = normalized_key(provider["provider"].get("name"))
    entity_key = normalized_key(provider.get("entity_name"))
    npi = re.sub(r"\D", "", provider["provider"].get("npi") or "")
    if not npi and not name_key:
        return None

    identity_keys = []
    if npi:
        identity_keys.append(f"npi-{npi}")
    if name_key:
        identity_keys.append(f"name-{entity_key or 'unknown'}-{name_key}")

    for identity_key in identity_keys:
        snapshot = entity_ref.collection(PROVIDER_IDENTITY_COLLECTION).document(identity_key).get()
        if snapshot.exists:
            provider_id = snapshot.to_dict()["provider_id"]
            for alias_key in identity_keys:
                entity_ref.collection(PROVIDER_IDENTITY_COLLECTION).document(alias_key).set(
                    {"provider_id": provider_id}, merge=True
                )
            return provider_id

    # Names and NPIs are aliases used to match later imports. The provider itself
    # receives an opaque Firestore-generated ID.
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
    if name:
        return name
    address = location.get("address")
    if isinstance(address, str):
        return address
    if isinstance(address, dict):
        return address.get("line1") or address.get("street")
    return None


def present_fields(values):
    return {field_name: value for field_name, value in values.items() if has_value(value)}


def resolve_group_ref(entity_ref, provider):
    group_name = provider.get("group_name") or provider.get("entity_name")
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
    group_ref.set({"legal_name": group_name}, merge=True)
    return group_ref


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
        group_ref.collection(LOCATION_COLLECTION).document(location_id).set(
            present_fields(
                {
                    "display_name": location_name,
                    "type": source.get("type") or "unknown",
                    "address": source.get("address"),
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


def record_provider_revision(provider_ref, metadata, changes, document_category):
    revision = {
        "drive_file_id": metadata["id"],
        "file_name": metadata.get("name"),
        "document_category": document_category,
        "changes": changes,
        "recorded_at": firestore.SERVER_TIMESTAMP,
    }
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
        "file_name": metadata.get("name"),
        "drive_file_id": metadata["id"],
    }


def field_provenance_id(field_path):
    return hashlib.sha256(field_path.encode("utf-8")).hexdigest()


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
    group_ref = resolve_group_ref(entity_ref, provider)
    upsert_provider_group_membership(entity_ref, provider_id, group_ref, provider)
    changes = provider_changes(existing, canonical_provider)
    if not changes:
        return provider_id

    provider_ref.set(canonical_provider, merge=True)
    record_provider_revision(provider_ref, metadata, changes, document_category)
    record_field_provenance(provider_ref, metadata, changes)
    try:
        sync_provider_to_bigquery(provider_id, canonical_provider)
    except Exception:
        # Firestore remains the system of record if reporting is temporarily unavailable.
        logger.exception("BigQuery provider sync failed for provider_id=%s", provider_id)
    return provider_id
