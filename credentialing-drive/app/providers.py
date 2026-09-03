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
    BIGQUERY_REPORTING_DATASET, BIGQUERY_REPORTING_TABLE, PROVIDER_COLLECTION,
    PROVIDER_IDENTITY_COLLECTION,
)
from app.connections import get_firestore_client, get_project_id
from app.issues import calculate_provider_issues

logger = logging.getLogger(__name__)



def serialize_provider(snapshot):
    provider = snapshot.to_dict() or {}
    issues = calculate_provider_issues(snapshot.reference, provider)
    return {"id": snapshot.id, **provider, "issues": issues, "issue_count": len(issues)}


def list_providers(limit):
    snapshots = get_firestore_client().collection(PROVIDER_COLLECTION).limit(limit).stream()
    return [serialize_provider(snapshot) for snapshot in snapshots]


def get_provider(provider_id):
    snapshot = (
        get_firestore_client().collection(PROVIDER_COLLECTION).document(provider_id).get()
    )
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="Provider not found")
    return serialize_provider(snapshot)


def normalized_key(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def normalize_provider_data(extraction):
    provider = extraction.get("provider") or {}
    if not isinstance(provider, dict):
        provider = {"name": str(provider)}

    name = provider.get("name") or extraction.get("provider_name")
    npi = provider.get("npi") or extraction.get("npi")
    return {
        "entity_name": extraction.get("entity_name"),
        "group_name": extraction.get("group_name"),
        "provider": {
            "name": name,
            "npi": str(npi) if npi else None,
            "credentials": provider.get("credentials") or extraction.get("credentials"),
        },
        "locations": extraction.get("locations") or [],
        "payers": extraction.get("payers") or [],
        "licenses": extraction.get("licenses") or [],
        "expiration_dates": extraction.get("expiration_dates") or [],
        "summary": extraction.get("summary"),
    }


def resolve_provider_id(client, provider):
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
        snapshot = client.collection(PROVIDER_IDENTITY_COLLECTION).document(identity_key).get()
        if snapshot.exists:
            provider_id = snapshot.to_dict()["provider_id"]
            for alias_key in identity_keys:
                client.collection(PROVIDER_IDENTITY_COLLECTION).document(alias_key).set(
                    {"provider_id": provider_id}, merge=True
                )
            return provider_id

    # Names and NPIs are aliases used to match later imports. The provider itself
    # receives an opaque Firestore-generated ID.
    provider_id = client.collection(PROVIDER_COLLECTION).document().id
    for identity_key in identity_keys:
        client.collection(PROVIDER_IDENTITY_COLLECTION).document(identity_key).set(
            {"provider_id": provider_id}, merge=True
        )
    return provider_id


def merge_unique(existing, incoming):
    values = list(existing or [])
    for value in incoming or []:
        if value and value not in values:
            values.append(value)
    return values


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
        "entity_name": provider.get("entity_name"),
        "group_name": provider.get("group_name"),
        "provider_name": profile.get("name"),
        "npi": profile.get("npi"),
        "credentials": profile.get("credentials"),
        "locations": reporting_values(provider.get("locations")),
        "payers": reporting_values(provider.get("payers")),
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
    for field_name in ("entity_name", "group_name"):
        previous_value = existing.get(field_name)
        current_value = updated.get(field_name)
        if current_value is None or previous_value == current_value:
            continue
        changes[field_name] = {"current": current_value}
        if previous_value is not None:
            changes[field_name]["previous"] = previous_value

    existing_profile = existing.get("provider") or {}
    updated_profile = updated.get("provider") or {}
    profile_changes = {}
    for field_name in ("name", "npi", "credentials"):
        previous_value = existing_profile.get(field_name)
        current_value = updated_profile.get(field_name)
        if current_value is None or previous_value == current_value:
            continue
        profile_changes[field_name] = {"current": current_value}
        if previous_value is not None:
            profile_changes[field_name]["previous"] = previous_value
    if profile_changes:
        changes["provider"] = profile_changes

    for field_name in ("locations", "payers", "licenses", "expiration_dates"):
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
    for field_name in ("entity_name", "group_name"):
        change = changes.get(field_name)
        if change and "current" in change:
            yield field_name, change

    for field_name, change in (changes.get("provider") or {}).items():
        if "current" in change:
            yield f"provider.{field_name}", change


def scalar_revision_changes(changes):
    for field_path, change in scalar_field_changes(changes):
        if "previous" in change:
            yield field_path, change


def source_metadata(metadata):
    return {
        "file_name": metadata.get("name"),
        "drive_file_id": metadata["id"],
    }


def field_provenance_id(field_path):
    return hashlib.sha256(field_path.encode("utf-8")).hexdigest()


def revision_change_for_field(revision, field_path):
    if field_path.startswith("provider."):
        return (revision.get("changes", {}).get("provider", {}) or {}).get(
            field_path.removeprefix("provider.")
        )
    return (revision.get("changes") or {}).get(field_path)


def previous_field_source(provider_ref, field_path, previous_value):
    provenance_ref = provider_ref.collection("field_provenance").document(
        field_provenance_id(field_path)
    )
    provenance = provenance_ref.get().to_dict() or {}
    if provenance.get("value") == previous_value and provenance.get("source"):
        return provenance["source"]

    for revision_snapshot in provider_ref.collection("revisions").stream():
        revision = revision_snapshot.to_dict() or {}
        change = revision_change_for_field(revision, field_path) or {}
        if change.get("current") == previous_value:
            return {
                "file_name": revision.get("file_name"),
                "drive_file_id": revision.get("drive_file_id"),
            }
    return {"source_status": "unavailable"}


def issue_id(field_path, previous_value, current_value):
    values = json.dumps(
        [field_path, previous_value, current_value], sort_keys=True, default=str
    )
    return hashlib.sha256(values.encode("utf-8")).hexdigest()


def record_discrepancies_and_provenance(provider_ref, metadata, changes):
    current_source = source_metadata(metadata)
    for field_path, change in scalar_revision_changes(changes):
        previous_value = change["previous"]
        current_value = change["current"]
        previous_source = previous_field_source(provider_ref, field_path, previous_value)
        provider_ref.collection("issues").document(
            issue_id(field_path, previous_value, current_value)
        ).set(
            {
                "status": "open",
                "field_path": field_path,
                "previous_value": previous_value,
                "previous_source": previous_source,
                "current_value": current_value,
                "current_source": current_source,
                "detected_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )

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
    provider_id = resolve_provider_id(client, provider)
    if not provider_id:
        return None

    provider_ref = client.collection(PROVIDER_COLLECTION).document(provider_id)
    existing = provider_ref.get().to_dict() or {}
    existing_profile = existing.get("provider") or {}
    incoming_profile = provider["provider"]
    merged_profile = {
        key: incoming_profile.get(key) or existing_profile.get(key)
        for key in ("name", "npi", "credentials")
    }
    canonical_provider = {
        # Remove the legacy single-document classification from the provider record.
        "document_type": firestore.DELETE_FIELD,
        "entity_name": provider.get("entity_name") or existing.get("entity_name"),
        "group_name": provider.get("group_name") or existing.get("group_name"),
        "provider": merged_profile,
        "locations": merge_unique(existing.get("locations"), provider.get("locations")),
        "payers": merge_unique(existing.get("payers"), provider.get("payers")),
        "licenses": merge_unique(existing.get("licenses"), provider.get("licenses")),
        "expiration_dates": merge_unique(
            existing.get("expiration_dates"), provider.get("expiration_dates")
        ),
    }
    changes = provider_changes(existing, canonical_provider)
    if not changes:
        return provider_id

    provider_ref.set(canonical_provider, merge=True)
    record_provider_revision(provider_ref, metadata, changes, document_category)
    record_discrepancies_and_provenance(provider_ref, metadata, changes)
    try:
        sync_provider_to_bigquery(provider_id, canonical_provider)
    except Exception:
        # Firestore remains the system of record if reporting is temporarily unavailable.
        logger.exception("BigQuery provider sync failed for provider_id=%s", provider_id)
    return provider_id
