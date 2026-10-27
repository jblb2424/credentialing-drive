import hashlib
import json
import logging
import os
import re
import time
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
from app.audit import safe_actor
from app.dates import normalize_date_fields, normalize_date_value
from app.facts import factual_extraction
from app.issue_summaries import (
    delete_issue_summary, list_issue_summaries, read_record_issue_summary,
    refresh_group_issue_summary, refresh_provider_issue_summary,
)
from app.issue_resolutions import remove_generated_issue
from app.provider_names import (
    enrich_provider_name, name_values_equivalent, normalize_person_name,
    split_provider_name_and_credentials,
)
from app.practice_validation import (
    canonicalize_practice_candidate, group_identity_keys, practice_candidate_decision,
)
from app.provider_types import normalize_provider_type
from app.reconciliation import reconcile_provider_records

logger = logging.getLogger(__name__)

PROVIDER_MANUAL_FIELDS = {
    "provider_type",
    "first_name",
    "middle_name",
    "last_name",
    "gender",
    "date_of_birth",
    "npi",
    "caqh_id",
    "address",
}
GROUP_MANUAL_FIELDS = {"legal_name", "dba", "type_2_npi", "tax_id"}
PROVIDER_MANUAL_COLLECTIONS = {
    "provider_locations",
    "licenses",
    "specialties",
    "education",
    "liability_insurance",
    "payer_enrollments",
}


def manual_field_values(fields, allowed_fields):
    """Validate a small, explicit manual-edit surface instead of arbitrary writes."""
    if not isinstance(fields, dict) or not fields:
        raise HTTPException(status_code=400, detail="Provide at least one field to update")

    unexpected_fields = sorted(set(fields) - allowed_fields)
    if unexpected_fields:
        raise HTTPException(
            status_code=400,
            detail=f"These fields cannot be edited here: {', '.join(unexpected_fields)}",
        )

    cleaned = {}
    for field_name, raw_value in fields.items():
        if raw_value is not None and not isinstance(raw_value, str):
            raise HTTPException(status_code=400, detail=f"{field_name} must be text")
        value = raw_value.strip() if isinstance(raw_value, str) else None
        if value and len(value) > 500:
            raise HTTPException(status_code=400, detail=f"{field_name} is too long")
        if field_name in {"npi", "type_2_npi"} and value and not valid_npi(value):
            raise HTTPException(status_code=400, detail=f"{field_name} must be a valid 10-digit NPI")
        cleaned[field_name] = value
    return cleaned


def manual_provider_changes(existing_profile, fields):
    """Return the updated profile and revision-compatible change map."""
    updated_profile = dict(existing_profile or {})
    profile_changes = {}
    for field_name, current_value in manual_field_values(fields, PROVIDER_MANUAL_FIELDS).items():
        if field_name in {"first_name", "middle_name", "last_name"}:
            current_value = normalize_person_name(current_value)
        if field_name == "date_of_birth":
            current_value = normalize_date_value(current_value)
        previous_value = updated_profile.get(field_name)
        if previous_value == current_value:
            continue
        updated_profile[field_name] = current_value
        profile_changes[field_name] = {"current": current_value, "previous": previous_value}
    return enrich_provider_name(updated_profile), {"provider": profile_changes} if profile_changes else {}


def manual_collection_value(field_name, raw_value):
    """Permit manual changes only to an explicit, bounded list of record items."""
    if not isinstance(raw_value, list) or len(raw_value) > 100:
        raise HTTPException(status_code=400, detail=f"{field_name} must contain up to 100 entries")
    cleaned = []
    for item in raw_value:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail=f"Each {field_name} entry must be an object")
        record = {}
        for key, value in item.items():
            if not isinstance(key, str) or len(key) > 100:
                raise HTTPException(status_code=400, detail=f"{field_name} contains an invalid field")
            if value is None or isinstance(value, (str, bool, int, float)):
                record[key] = value.strip() if isinstance(value, str) else value
            elif isinstance(value, list) and all(isinstance(entry, str) for entry in value):
                record[key] = [entry.strip() for entry in value if entry.strip()]
            else:
                raise HTTPException(status_code=400, detail=f"{field_name}.{key} has an unsupported value")
        cleaned.append(normalize_date_fields(record))
    return cleaned


def manual_provider_updates(existing, fields):
    """Build a safe profile or collection patch from a section editor submission."""
    if not isinstance(fields, dict) or not fields:
        raise HTTPException(status_code=400, detail="Provide at least one field to update")
    allowed_fields = PROVIDER_MANUAL_FIELDS | PROVIDER_MANUAL_COLLECTIONS
    unexpected_fields = sorted(set(fields) - allowed_fields)
    if unexpected_fields:
        raise HTTPException(status_code=400, detail=f"These fields cannot be edited here: {', '.join(unexpected_fields)}")

    updates = {}
    changes = {}
    profile_fields = {key: value for key, value in fields.items() if key in PROVIDER_MANUAL_FIELDS}
    if profile_fields:
        profile, profile_changes = manual_provider_changes(existing.get("provider"), profile_fields)
        if profile_changes:
            updates["provider"] = profile
            changes.update(profile_changes)

    for field_name in PROVIDER_MANUAL_COLLECTIONS & set(fields):
        current_value = manual_collection_value(field_name, fields[field_name])
        previous_value = existing.get(field_name) or []
        if previous_value == current_value:
            continue
        updates[field_name] = current_value
        changes[field_name] = {"current": current_value, "previous": previous_value}
    return updates, changes


def manual_group_changes(existing_group, fields):
    """Return canonical practice updates and a revision-compatible change map."""
    existing_group = existing_group or {}
    updates = {}
    group_changes = {}
    for field_name, current_value in manual_field_values(fields, GROUP_MANUAL_FIELDS).items():
        # Older imports used tax_id_number. Normalize manual changes to tax_id while
        # clearing that legacy duplicate so the corrected value is always displayed.
        previous_value = (
            existing_group.get("tax_id") or existing_group.get("tax_id_number")
            if field_name == "tax_id"
            else existing_group.get(field_name)
        )
        if previous_value == current_value:
            continue
        updates[field_name] = current_value
        if field_name == "tax_id" and "tax_id_number" in existing_group:
            updates["tax_id_number"] = firestore.DELETE_FIELD
        group_changes[field_name] = {"current": current_value, "previous": previous_value}
    return updates, {"group": group_changes} if group_changes else {}


def replace_group_locations(group_ref, raw_locations):
    """Replace practice locations while retaining Firestore document IDs where possible."""
    locations = manual_collection_value("locations", raw_locations)
    existing_snapshots = list(group_ref.collection(LOCATION_COLLECTION).stream())
    existing_ids = {snapshot.id for snapshot in existing_snapshots}
    incoming_ids = set()
    cleaned_locations = []
    for location in locations:
        location = dict(location)
        location_id = location.pop("id", None)
        if location_id and location_id in existing_ids:
            location_ref = group_ref.collection(LOCATION_COLLECTION).document(location_id)
            incoming_ids.add(location_id)
        else:
            location_ref = group_ref.collection(LOCATION_COLLECTION).document()
            incoming_ids.add(location_ref.id)
        location_ref.set(location)
        cleaned_locations.append({"id": location_ref.id, **location})
    for snapshot in existing_snapshots:
        if snapshot.id not in incoming_ids:
            snapshot.reference.delete()
    return cleaned_locations


def manual_revision_metadata(user):
    return {
        "id": f"manual-{uuid.uuid4()}",
        "name": "Manual update",
        "source_type": "manual",
        "actor": safe_actor(user),
    }



def serialize_provider(snapshot, include_issues=False):
    provider = snapshot.to_dict() or {}
    profile = provider.get("provider") or {}
    provider["provider"] = enrich_provider_name(profile)
    serialized = {"id": snapshot.id, **provider}
    if include_issues:
        summary = read_record_issue_summary(snapshot.reference, "provider")
        serialized["issues"] = summary.get("issues") or []
        serialized["issue_count"] = summary.get("issue_count", 0)
    return serialized


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


def serialize_group(snapshot, include_locations=False, include_issues=False):
    group = serialize_document(snapshot)
    serialized = {**group}
    if include_locations:
        serialized["locations"] = [
            serialize_document(location_snapshot)
            for location_snapshot in snapshot.reference.collection(LOCATION_COLLECTION).stream()
        ]
    if include_issues:
        summary = read_record_issue_summary(snapshot.reference, "practice")
        serialized["issues"] = summary.get("issues") or []
        serialized["issue_count"] = summary.get("issue_count", 0)
    return serialized


def get_entity(entity_id=DEFAULT_ENTITY_ID):
    snapshot = get_entity_ref(get_firestore_client(), entity_id).get()
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="Entity not found")
    return serialize_document(snapshot)


def list_groups(limit, entity_id=DEFAULT_ENTITY_ID):
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    snapshots = entity_ref.collection(GROUP_COLLECTION).limit(limit).stream()
    return [serialize_group(snapshot) for snapshot in snapshots]


def get_group(group_id, entity_id=DEFAULT_ENTITY_ID):
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    group_ref = entity_ref.collection(GROUP_COLLECTION).document(group_id)
    snapshot = group_ref.get()
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="Practice not found")

    group = serialize_group(snapshot, include_locations=True, include_issues=True)
    providers = get_group_providers(entity_ref, group_id)
    return {
        **group,
        "provider_count": len(providers),
        "providers": providers,
    }


def list_providers(limit, entity_id=DEFAULT_ENTITY_ID):
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    snapshots = entity_ref.collection(PROVIDER_COLLECTION).limit(limit).stream()
    return [serialize_provider(snapshot) for snapshot in snapshots]


def revision_changed_fields(changes):
    """Return the field paths a revision changed, including appended collections."""
    fields = []

    def add_field(field_path):
        if field_path and field_path not in fields:
            fields.append(field_path)

    for field_path, _change in scalar_field_changes(changes):
        add_field(field_path)

    def added_collection_paths(value, prefix=""):
        if not isinstance(value, dict):
            return
        if value.get("added"):
            add_field(prefix)
            return
        for field_name, child in value.items():
            if field_name in {"current", "previous", "added"}:
                continue
            child_path = f"{prefix}.{field_name}" if prefix else field_name
            added_collection_paths(child, child_path)

    added_collection_paths(changes)
    return fields


def revision_activity_entry(snapshot, record_type, record_id, record_name):
    revision = snapshot.to_dict() or {}
    changed_fields = revision_changed_fields(revision.get("changes") or {})
    return {
        "id": snapshot.id,
        "record_type": record_type,
        "record_id": record_id,
        "record_name": record_name,
        "file_name": revision.get("file_name"),
        "drive_file_id": revision.get("drive_file_id"),
        "document_category": revision.get("document_category") or "other",
        "recorded_at": revision.get("recorded_at"),
        "actor": revision.get("actor"),
        "changed_fields": changed_fields,
        "change_count": len(changed_fields),
    }


def activity_sort_key(entry):
    recorded_at = entry.get("recorded_at")
    return recorded_at.timestamp() if hasattr(recorded_at, "timestamp") else 0


def list_entity_activity(limit, record_type="all", document_category=None, entity_id=DEFAULT_ENTITY_ID):
    """Build a client-wide, newest-first audit feed from provider and practice revisions."""
    if record_type not in {"all", "provider", "practice"}:
        raise HTTPException(status_code=400, detail="record_type must be all, provider, or practice")

    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    activity = []
    if record_type in {"all", "provider"}:
        for provider_snapshot in entity_ref.collection(PROVIDER_COLLECTION).stream():
            profile = enrich_provider_name((provider_snapshot.to_dict() or {}).get("provider") or {})
            record_name = profile.get("name") or "Provider name not captured"
            for revision_snapshot in provider_snapshot.reference.collection("revisions").stream():
                activity.append(
                    revision_activity_entry(
                        revision_snapshot, "provider", provider_snapshot.id, record_name
                    )
                )

    if record_type in {"all", "practice"}:
        for group_snapshot in entity_ref.collection(GROUP_COLLECTION).stream():
            group = group_snapshot.to_dict() or {}
            record_name = group.get("legal_name") or group.get("name") or "Practice name not captured"
            for revision_snapshot in group_snapshot.reference.collection("revisions").stream():
                activity.append(
                    revision_activity_entry(
                        revision_snapshot, "practice", group_snapshot.id, record_name
                    )
                )

    if document_category:
        activity = [
            entry for entry in activity if entry["document_category"] == document_category
        ]
    return sorted(activity, key=activity_sort_key, reverse=True)[:limit]


def get_record_activity(record_id, record_type, limit, entity_id=DEFAULT_ENTITY_ID):
    """Return one record's identity and revisions without scanning the client."""
    collection_name = PROVIDER_COLLECTION if record_type == "provider" else GROUP_COLLECTION
    record_ref = get_entity_ref(get_firestore_client(), entity_id).collection(collection_name).document(
        record_id
    )
    record_snapshot = record_ref.get()
    if not record_snapshot.exists:
        label = "Provider" if record_type == "provider" else "Practice"
        raise HTTPException(status_code=404, detail=f"{label} not found")

    record = record_snapshot.to_dict() or {}
    if record_type == "provider":
        profile = enrich_provider_name(record.get("provider") or {})
        record_name = profile.get("name") or "Provider name not captured"
    else:
        record_name = record.get("legal_name") or record.get("name") or "Practice name not captured"

    activity = [
        revision_activity_entry(revision_snapshot, record_type, record_id, record_name)
        for revision_snapshot in record_ref.collection("revisions").stream()
    ]
    return {
        "record": {
            "id": record_id,
            "record_type": record_type,
            "record_name": record_name,
        },
        "activity": sorted(activity, key=activity_sort_key, reverse=True)[:limit],
    }


def list_provider_expirations(limit, entity_id=DEFAULT_ENTITY_ID):
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    records = []
    for summary in list_issue_summaries(entity_ref):
        if summary.get("record_type") != "provider":
            continue
        for record in summary.get("issues") or []:
            if record.get("type") not in {"expired", "expiring"}:
                continue
            records.append(
                {
                    **record,
                    "provider_id": summary.get("record_id"),
                    "provider_name": summary.get("record_name") or "Unnamed provider",
                    "provider_npi": summary.get("record_identifier"),
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
    provider = serialize_provider(snapshot, include_issues=True)
    provider["affiliations"] = get_provider_affiliations(entity_ref, provider_id)
    return provider


def update_provider_manually(provider_id, fields, user, entity_id=DEFAULT_ENTITY_ID):
    """Persist an authorized correction and retain it as a manual revision."""
    provider_ref = get_entity_ref(get_firestore_client(), entity_id).collection(
        PROVIDER_COLLECTION
    ).document(provider_id)
    snapshot = provider_ref.get()
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="Provider not found")

    existing = snapshot.to_dict() or {}
    updates, changes = manual_provider_updates(existing, fields)
    if not changes:
        return get_provider(provider_id, entity_id)

    metadata = manual_revision_metadata(user)
    provider_ref.set(updates, merge=True)
    record_provider_revision(provider_ref, metadata, changes, "manual_update", {})
    record_field_provenance(provider_ref, metadata, changes)
    refresh_provider_issue_summary(provider_ref)
    return get_provider(provider_id, entity_id)


def update_group_manually(group_id, fields, user, entity_id=DEFAULT_ENTITY_ID):
    """Persist an authorized practice correction and retain its provenance."""
    group_ref = get_entity_ref(get_firestore_client(), entity_id).collection(
        GROUP_COLLECTION
    ).document(group_id)
    snapshot = group_ref.get()
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="Practice not found")

    if not isinstance(fields, dict) or not fields:
        raise HTTPException(status_code=400, detail="Provide at least one field to update")
    group_fields = {key: value for key, value in fields.items() if key in GROUP_MANUAL_FIELDS}
    unexpected_fields = sorted(set(fields) - GROUP_MANUAL_FIELDS - {"locations", "w9"})
    if unexpected_fields:
        raise HTTPException(status_code=400, detail=f"These fields cannot be edited here: {', '.join(unexpected_fields)}")

    updates, changes = manual_group_changes(snapshot.to_dict() or {}, group_fields) if group_fields else ({}, {})
    if "locations" in fields:
        previous_locations = [
            serialize_document(location_snapshot)
            for location_snapshot in group_ref.collection(LOCATION_COLLECTION).stream()
        ]
        current_locations = replace_group_locations(group_ref, fields["locations"])
        if previous_locations != current_locations:
            changes["locations"] = {"current": current_locations, "previous": previous_locations}
    if "w9" in fields:
        raw_w9 = fields["w9"]
        if not isinstance(raw_w9, dict) or set(raw_w9) != {"file_name"}:
            raise HTTPException(status_code=400, detail="W-9 updates must provide a file name")
        file_name = raw_w9["file_name"]
        if file_name is not None and not isinstance(file_name, str):
            raise HTTPException(status_code=400, detail="W-9 file name must be text")
        current_w9 = {"file_name": file_name.strip()} if file_name and file_name.strip() else None
        previous_w9 = (snapshot.to_dict() or {}).get("w9") or (snapshot.to_dict() or {}).get("w_9")
        if previous_w9 != current_w9:
            updates["w9"] = current_w9
            changes.setdefault("group", {})["w9"] = {"current": current_w9, "previous": previous_w9}
    if not changes:
        return get_group(group_id, entity_id)

    metadata = manual_revision_metadata(user)
    if updates:
        group_ref.set(updates, merge=True)
    record_group_revision(group_ref, metadata, changes, "manual_update", {})
    record_field_provenance(group_ref, metadata, changes)
    refresh_group_issue_summary(group_ref)
    return get_group(group_id, entity_id)


def get_provider_issue(provider_id, issue_id, entity_id=DEFAULT_ENTITY_ID):
    provider = get_provider(provider_id, entity_id)
    issue = next((issue for issue in provider["issues"] if issue["id"] == issue_id), None)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    record = {
        "id": provider["id"],
        "name": provider["provider"].get("name"),
        "npi": provider["provider"].get("npi"),
    }
    return {"issue": issue, "record": record, "provider": record}


def get_group_issue(group_id, issue_id, entity_id=DEFAULT_ENTITY_ID):
    group = get_group(group_id, entity_id)
    issue = next((issue for issue in group["issues"] if issue["id"] == issue_id), None)
    if not issue:
        raise HTTPException(status_code=404, detail="Practice issue not found")
    record = {
        "id": group["id"],
        "name": group.get("legal_name") or group.get("name"),
        "legal_name": group.get("legal_name") or group.get("name"),
        "type_2_npi": group.get("type_2_npi"),
    }
    return {"issue": issue, "record": record, "group": record}


def remove_provider_issue(provider_id, issue_id, user, entity_id=DEFAULT_ENTITY_ID):
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    snapshot = entity_ref.collection(PROVIDER_COLLECTION).document(provider_id).get()
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="Provider not found")
    provider = snapshot.to_dict() or {}
    summary = read_record_issue_summary(snapshot.reference, "provider")
    issue = next((item for item in summary.get("issues") or [] if item["id"] == issue_id), None)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    remove_generated_issue(snapshot.reference, issue, user)
    refresh_provider_issue_summary(snapshot.reference, provider)
    return issue


def remove_group_issue(group_id, issue_id, user, entity_id=DEFAULT_ENTITY_ID):
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    snapshot = entity_ref.collection(GROUP_COLLECTION).document(group_id).get()
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="Practice not found")
    group = snapshot.to_dict() or {}
    summary = read_record_issue_summary(snapshot.reference, "practice")
    issue = next((item for item in summary.get("issues") or [] if item["id"] == issue_id), None)
    if not issue:
        raise HTTPException(status_code=404, detail="Practice issue not found")
    remove_generated_issue(snapshot.reference, issue, user)
    refresh_group_issue_summary(snapshot.reference, group)
    return issue


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


def get_group_providers(entity_ref, group_id):
    """Resolve the active provider records linked to a practice."""
    memberships = entity_ref.collection(PROVIDER_GROUP_MEMBERSHIP_COLLECTION).where(
        "group_id", "==", group_id
    ).stream()
    linked_providers = []
    seen_provider_ids = set()
    provider_collection = entity_ref.collection(PROVIDER_COLLECTION)

    for membership_snapshot in memberships:
        membership = membership_snapshot.to_dict() or {}
        provider_id = membership.get("provider_id")
        if not provider_id or provider_id in seen_provider_ids:
            continue
        seen_provider_ids.add(provider_id)

        provider_snapshot = provider_collection.document(provider_id).get()
        if not provider_snapshot.exists:
            continue
        provider = serialize_provider(provider_snapshot)
        profile = provider.get("provider") or {}
        linked_providers.append(
            {
                "id": provider["id"],
                "name": profile.get("name"),
                "npi": profile.get("npi"),
                "provider_type": profile.get("provider_type"),
            }
        )

    return sorted(linked_providers, key=lambda provider: (provider.get("name") or "").casefold())


def normalized_key(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


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
        "aliases": group.get("aliases") if isinstance(group.get("aliases"), list) else [],
        "source_role": group.get("source_role") or group.get("relationship"),
        "entity_kind": group.get("entity_kind"),
    }


def normalize_provider_data(extraction):
    # Verification sources are extraction-only context. The uploaded Drive file remains
    # the durable provenance in revisions and field_provenance; verifier names must not
    # become provider facts or standalone Firestore records.
    extraction = factual_extraction(extraction)
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
    name, name_credentials = split_provider_name_and_credentials(name)
    npi = valid_npi(provider.get("npi") or extraction.get("npi"))
    credentials = provider.get("credentials") or extraction.get("credentials") or name_credentials
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
    return normalize_date_fields({
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
    })


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


def license_identity(record):
    """Return a stable credential identity only when both supported keys exist."""
    if not isinstance(record, dict):
        return None
    credential_type = normalized_key(record.get("type") or record.get("license_type"))
    license_number = normalized_key(record.get("license_number") or record.get("number"))
    return (credential_type, license_number) if credential_type and license_number else None


def merge_missing_values(existing, incoming):
    """Fill gaps from another copy of the same credential without replacing evidence."""
    merged = dict(existing or {})
    for field_name, incoming_value in (incoming or {}).items():
        existing_value = merged.get(field_name)
        if isinstance(existing_value, dict) and isinstance(incoming_value, dict):
            merged[field_name] = merge_missing_values(existing_value, incoming_value)
        elif not has_value(existing_value) and has_value(incoming_value):
            merged[field_name] = incoming_value
    return merged


def merge_license_records(existing, incoming):
    """Merge duplicate licenses only when normalized type and number both match."""
    values = []
    indexes = {}
    for record in [*(existing or []), *(incoming or [])]:
        if not record:
            continue
        normalized_record = normalize_date_fields(record)
        identity = license_identity(normalized_record)
        if not identity or identity not in indexes:
            if identity:
                indexes[identity] = len(values)
            values.append(normalized_record)
            continue
        index = indexes[identity]
        values[index] = merge_missing_values(values[index], normalized_record)
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
    identity_keys = group_identity_keys(group)
    if not identity_keys:
        return None

    identity_refs = [
        entity_ref.collection("group_identities").document(identity_key)
        for identity_key in identity_keys
    ]
    group_ids = {
        identity.to_dict().get("group_id")
        for identity_ref in identity_refs
        if (identity := identity_ref.get()).exists and identity.to_dict().get("group_id")
    }
    if len(group_ids) > 1:
        logger.warning("Ambiguous practice identity aliases: %s", identity_keys)
        return None

    group_id = next(iter(group_ids), None)
    if not group_id:
        group_id = entity_ref.collection(GROUP_COLLECTION).document().id
    for identity_ref in identity_refs:
        identity_ref.set({"group_id": group_id}, merge=True)

    return entity_ref.collection(GROUP_COLLECTION).document(group_id)


def group_changes(existing, incoming, location_changes):
    group_changes = {}
    for field_name, current_value in incoming.items():
        previous_value = existing.get(field_name)
        if previous_value == current_value:
            continue
        change = {"current": current_value}
        if previous_value is not None:
            change["previous"] = previous_value
        group_changes[field_name] = change

    changes = {"group": group_changes} if group_changes else {}
    if location_changes:
        changes["locations"] = location_changes
    return changes


def upsert_normalized_group(normalized_data, metadata=None, document_category="other", entity_id=DEFAULT_ENTITY_ID):
    """Persist a practice even when its source document contains no provider."""
    decision = practice_candidate_decision(normalized_data)
    if decision["action"] != "accept":
        logger.info("Skipped practice candidate: %s", decision["reason"])
        return None

    normalized_data = canonicalize_practice_candidate(normalized_data)
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    group_ref = resolve_group_ref(entity_ref, normalized_data.get("group"))
    if not group_ref:
        return None
    existing = group_ref.get().to_dict() or {}
    group = normalized_data.get("group") or {}
    incoming = present_fields(
        {
            "legal_name": group.get("legal_name") or group.get("name"),
            "type_2_npi": group.get("type_2_npi"),
            "tax_id": group.get("tax_id"),
            "dba": group.get("dba"),
            "w9": group.get("w9"),
            "aliases": merge_unique(existing.get("aliases"), group.get("aliases")),
        }
    )
    _, location_changes = upsert_group_locations(
        group_ref, normalized_data.get("locations"), collect_changes=True
    )
    changes = group_changes(existing, incoming, location_changes)
    if incoming:
        group_ref.set(incoming, merge=True)
    if metadata and changes:
        previous_files = previous_file_sources(group_ref, changes)
        record_group_revision(group_ref, metadata, changes, document_category, previous_files)
        record_field_provenance(group_ref, metadata, changes)
    if incoming or location_changes or existing:
        refresh_group_issue_summary(group_ref)
    return group_ref.id


def upsert_group_locations(group_ref, locations, collect_changes=False):
    location_ids = []
    changes = {}
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
        location_ref = group_ref.collection(LOCATION_COLLECTION).document(location_id)
        existing = location_ref.get().to_dict() or {}
        incoming = present_fields(
            {
                "display_name": location_name,
                "type": source.get("type"),
                "address": source.get("address") or embedded_address,
                "phone": source.get("phone"),
                "email": source.get("email"),
                "practice_hours": source.get("practice_hours"),
                "faxes": source.get("faxes"),
                "languages": source.get("languages"),
            }
        )
        location_changes = {}
        for field_name, current_value in incoming.items():
            previous_value = existing.get(field_name)
            if previous_value == current_value:
                continue
            change = {"current": current_value}
            if previous_value is not None:
                change["previous"] = previous_value
            location_changes[field_name] = change
        if incoming:
            location_ref.set(incoming, merge=True)
        if location_changes:
            changes[location_id] = location_changes
        location_ids.append(location_id)
    return (location_ids, changes) if collect_changes else location_ids


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
        if field_name == "name" and name_values_equivalent(previous_value, current_value):
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


def merge_change_maps(existing, incoming):
    """Retain every change when one spreadsheet updates a group more than once."""
    merged = dict(existing or {})
    for key, value in (incoming or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_change_maps(merged[key], value)
        else:
            merged[key] = value
    return merged


def record_revision(record_ref, metadata, changes, document_category, previous_files):
    revision_ref = record_ref.collection("revisions").document(f"drive-{metadata['id']}")
    existing_revision = revision_ref.get().to_dict() or {}
    revision = {
        "drive_file_id": metadata["id"],
        "file_name": metadata.get("name"),
        "document_category": document_category,
        "source_type": metadata.get("source_type", "document"),
        "changes": merge_change_maps(existing_revision.get("changes"), changes),
        "recorded_at": firestore.SERVER_TIMESTAMP,
    }
    if metadata.get("actor"):
        revision["actor"] = metadata["actor"]
    merged_previous_files = {
        **(existing_revision.get("previous_files") or {}),
        **(previous_files or {}),
    }
    if merged_previous_files:
        # A revision can update multiple fields whose previous values came from
        # different uploads, so retain lineage by field path.
        revision["previous_files"] = merged_previous_files
    revision_ref.set(revision, merge=True)


def record_provider_revision(provider_ref, metadata, changes, document_category, previous_files):
    record_revision(provider_ref, metadata, changes, document_category, previous_files)


def record_group_revision(group_ref, metadata, changes, document_category, previous_files):
    record_revision(group_ref, metadata, changes, document_category, previous_files)


def scalar_field_changes(changes, prefix=""):
    """Yield scalar changes from either a provider or group revision tree."""
    if not isinstance(changes, dict):
        return
    if "current" in changes:
        yield prefix, changes
        return
    for field_name, value in changes.items():
        if field_name == "added":
            continue
        field_path = f"{prefix}.{field_name}" if prefix else field_name
        yield from scalar_field_changes(value, field_path)


def source_metadata(metadata):
    source = {
        "file_name": metadata.get("name") or metadata.get("file_name"),
        "drive_file_id": metadata.get("id") or metadata.get("drive_file_id"),
    }
    if metadata.get("source_type"):
        source["source_type"] = metadata["source_type"]
    if metadata.get("actor"):
        source["actor"] = metadata["actor"]
    return source


def field_provenance_id(field_path):
    return hashlib.sha256(field_path.encode("utf-8")).hexdigest()


def previous_file_sources(record_ref, changes):
    """Look up the source file for every scalar value this import replaces."""
    previous_files = {}
    for field_path, change in scalar_field_changes(changes):
        if "previous" not in change:
            continue
        provenance = record_ref.collection("field_provenance").document(
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


def record_field_provenance(record_ref, metadata, changes):
    current_source = source_metadata(metadata)
    for field_path, change in scalar_field_changes(changes):
        record_ref.collection("field_provenance").document(
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


DATE_REPAIR_PROVIDER_FIELDS = (
    "provider",
    "provider_locations",
    "payer_enrollments",
    "licenses",
    "specialties",
    "education",
    "liability_insurance",
    "expiration_dates",
)
DATE_REPAIR_GROUP_FIELDS = ("w9",)
DATE_NORMALIZATION_METADATA = {
    "id": "date-normalization-v1",
    "name": "Canonical date normalization",
    # This deterministic storage repair is not competing source evidence.
    "source_type": "manual",
}


def date_repair_updates(record, fields):
    """Return a minimal patch for legacy records with non-canonical dates."""
    normalized = normalize_date_fields({
        field_name: record.get(field_name)
        for field_name in fields
        if field_name in record
    })
    if "licenses" in normalized:
        normalized["licenses"] = merge_license_records([], normalized["licenses"])
    return {
        field_name: value
        for field_name, value in normalized.items()
        if value != record.get(field_name)
    }


def date_repair_changes(existing, updates):
    return {
        field_name: {
            "previous": existing.get(field_name),
            "current": current_value,
        }
        for field_name, current_value in updates.items()
    }


def repair_entity_date_storage(entity_id=DEFAULT_ENTITY_ID):
    """Canonicalize legacy dates and safely collapse exact duplicate licenses."""
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    results = {
        "entity_id": entity_id,
        "providers_updated": 0,
        "groups_updated": 0,
        "locations_updated": 0,
        "issue_summaries_refreshed": 0,
        "bigquery_sync_failures": 0,
        "bigquery_sync_skipped": 0,
    }

    for provider_snapshot in entity_ref.collection(PROVIDER_COLLECTION).stream():
        provider = provider_snapshot.to_dict() or {}
        updates = date_repair_updates(provider, DATE_REPAIR_PROVIDER_FIELDS)
        if not updates:
            continue
        provider_snapshot.reference.set(updates, merge=True)
        record_provider_revision(
            provider_snapshot.reference,
            DATE_NORMALIZATION_METADATA,
            date_repair_changes(provider, updates),
            "manual_update",
            {},
        )
        repaired_provider = {**provider, **updates}
        refresh_provider_issue_summary(provider_snapshot.reference, repaired_provider)
        results["providers_updated"] += 1
        results["issue_summaries_refreshed"] += 1
        if not os.environ.get("GCP_PROJECT_ID"):
            results["bigquery_sync_skipped"] += 1
        else:
            try:
                sync_provider_to_bigquery(provider_snapshot.id, repaired_provider)
            except Exception:
                logger.exception(
                    "BigQuery provider sync failed during date repair for provider_id=%s",
                    provider_snapshot.id,
                )
                results["bigquery_sync_failures"] += 1

    for group_snapshot in entity_ref.collection(GROUP_COLLECTION).stream():
        group = group_snapshot.to_dict() or {}
        updates = date_repair_updates(group, DATE_REPAIR_GROUP_FIELDS)
        location_updates = 0
        for location_snapshot in group_snapshot.reference.collection(LOCATION_COLLECTION).stream():
            location = location_snapshot.to_dict() or {}
            normalized_location = normalize_date_fields(location)
            location_patch = {
                field_name: value
                for field_name, value in normalized_location.items()
                if value != location.get(field_name)
            }
            if location_patch:
                location_snapshot.reference.set(location_patch, merge=True)
                location_updates += 1
                results["locations_updated"] += 1
        if not updates and not location_updates:
            continue
        if updates:
            group_snapshot.reference.set(updates, merge=True)
            record_group_revision(
                group_snapshot.reference,
                DATE_NORMALIZATION_METADATA,
                date_repair_changes(group, updates),
                "manual_update",
                {},
            )
        refresh_group_issue_summary(group_snapshot.reference, {**group, **updates})
        results["groups_updated"] += 1
        results["issue_summaries_refreshed"] += 1

    return results


def upsert_normalized_provider(provider, metadata, document_category="other", entity_id=DEFAULT_ENTITY_ID):
    client = get_firestore_client()
    entity_ref = get_entity_ref(client, entity_id)
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
        "licenses": merge_license_records(existing.get("licenses"), provider.get("licenses")),
        "specialties": merge_unique(existing.get("specialties"), provider.get("specialties")),
        "education": merge_unique(existing.get("education"), provider.get("education")),
        "liability_insurance": merge_unique(
            existing.get("liability_insurance"), provider.get("liability_insurance")
        ),
        "expiration_dates": merge_unique(
            existing.get("expiration_dates"), provider.get("expiration_dates")
        ),
    }
    practice_decision = practice_candidate_decision(provider)
    group_ref = None
    if practice_decision["action"] == "accept":
        provider = canonicalize_practice_candidate(provider)
        group_ref = resolve_group_ref(entity_ref, provider.get("group"))
    else:
        logger.info("Skipped provider practice affiliation: %s", practice_decision["reason"])
    upsert_provider_group_membership(entity_ref, provider_id, group_ref, provider)
    changes = provider_changes(existing, canonical_provider)
    if not changes:
        if group_ref:
            refresh_group_issue_summary(group_ref)
        return provider_id

    previous_files = previous_file_sources(provider_ref, changes)
    provider_ref.set(canonical_provider, merge=True)
    record_provider_revision(
        provider_ref, metadata, changes, document_category, previous_files
    )
    record_field_provenance(provider_ref, metadata, changes)
    reconciliation = reconcile_provider_records(provider_ref)
    reconciled_provider = reconciliation.get("provider", canonical_provider)
    refresh_provider_issue_summary(provider_ref, reconciled_provider)
    if group_ref:
        refresh_group_issue_summary(group_ref)
    try:
        sync_provider_to_bigquery(provider_id, reconciled_provider)
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
            merge_function = merge_license_records if field_name == "licenses" else merge_unique
            merged[field_name] = merge_function(merged.get(field_name), source.get(field_name))

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
        delete_issue_summary(entity_ref, "provider", source_snapshot.id)

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
    refresh_provider_issue_summary(target_ref, merged)
    return {"target_provider_id": target_provider_id, "merged_provider_ids": duplicate_provider_ids}


def delete_document_tree(document_ref):
    for collection in document_ref.collections():
        for snapshot in collection.stream():
            delete_document_tree(snapshot.reference)
    document_ref.delete()


def collect_document_tree(document_ref, document_refs=None):
    """Collect a Firestore document tree with children before their parent."""
    document_refs = document_refs if document_refs is not None else []
    for collection in document_ref.collections():
        for snapshot in collection.stream():
            collect_document_tree(snapshot.reference, document_refs)
    document_refs.append(document_ref)
    return document_refs


def delete_document_references(client, document_refs, batch_size=500):
    """Delete references in Firestore batches to avoid one network call per doc."""
    for start in range(0, len(document_refs), batch_size):
        batch = client.batch()
        for document_ref in document_refs[start:start + batch_size]:
            batch.delete(document_ref)
        batch.commit()


def delete_provider(provider_id, entity_id=DEFAULT_ENTITY_ID):
    """Remove a provider and every provider-scoped record for a clean re-import."""
    started_at = time.monotonic()
    client = get_firestore_client()
    entity_ref = get_entity_ref(client, entity_id)
    provider_ref = entity_ref.collection(PROVIDER_COLLECTION).document(provider_id)
    if not provider_ref.get().exists:
        raise HTTPException(status_code=404, detail="Provider not found")

    document_refs = []
    for membership in entity_ref.collection(PROVIDER_GROUP_MEMBERSHIP_COLLECTION).where(
        "provider_id", "==", provider_id
    ).stream():
        collect_document_tree(membership.reference, document_refs)
    for identity in entity_ref.collection(PROVIDER_IDENTITY_COLLECTION).where(
        "provider_id", "==", provider_id
    ).stream():
        document_refs.append(identity.reference)
    collect_document_tree(provider_ref, document_refs)

    delete_issue_summary(entity_ref, "provider", provider_id)
    delete_document_references(client, document_refs)

    duration_ms = round((time.monotonic() - started_at) * 1000)
    logger.info(
        "Deleted provider_id=%s entity_id=%s documents=%s duration_ms=%s",
        provider_id,
        entity_id,
        len(document_refs) + 1,
        duration_ms,
    )
    return {
        "deleted_provider_id": provider_id,
        "deleted_document_count": len(document_refs) + 1,
        "deletion_duration_ms": duration_ms,
    }
