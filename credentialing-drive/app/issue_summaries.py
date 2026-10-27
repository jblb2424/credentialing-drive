"""Persist and read derived record-integrity summaries for responsive workspaces."""

from datetime import date

from google.cloud import firestore

from app.config import GROUP_COLLECTION, ISSUE_SUMMARY_COLLECTION, LOCATION_COLLECTION, PROVIDER_COLLECTION
from app.issues import calculate_group_issues, calculate_provider_issues
from app.issue_resolutions import filter_removed_issues
from app.provider_names import enrich_provider_name

SUMMARY_STATUS_DOCUMENT_ID = "_status"


def summary_document_id(record_type, record_id):
    return f"{record_type}-{record_id}"


def summary_ref(record_ref, record_type):
    return record_ref.parent.parent.collection(ISSUE_SUMMARY_COLLECTION).document(
        summary_document_id(record_type, record_ref.id)
    )


def provider_summary_payload(record_ref, provider):
    profile = enrich_provider_name(provider.get("provider") or {})
    issues = filter_removed_issues(
        record_ref, calculate_provider_issues(record_ref, provider)
    )
    return {
        "record_type": "provider",
        "record_id": record_ref.id,
        "record_name": profile.get("name") or "Provider name not captured",
        "record_identifier": profile.get("npi"),
        "issues": issues,
        "issue_count": len(issues),
        "evaluated_on": date.today().isoformat(),
    }


def group_summary_payload(record_ref, group, locations):
    issues = filter_removed_issues(
        record_ref,
        calculate_group_issues(record_ref, {**group, "locations": locations}),
    )
    return {
        "record_type": "practice",
        "record_id": record_ref.id,
        "record_name": group.get("legal_name") or group.get("name") or "Practice name not captured",
        "record_identifier": group.get("type_2_npi"),
        "issues": issues,
        "issue_count": len(issues),
        "evaluated_on": date.today().isoformat(),
    }


def write_issue_summary(record_ref, record_type, payload):
    summary_ref(record_ref, record_type).set(
        {**payload, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True
    )
    record_ref.set(
        {
            "issue_count": payload["issue_count"],
            "issue_summary_evaluated_on": payload["evaluated_on"],
            "issue_summary_updated_at": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )
    return payload


def refresh_provider_issue_summary(record_ref, provider=None):
    provider = provider if provider is not None else (record_ref.get().to_dict() or {})
    return write_issue_summary(
        record_ref, "provider", provider_summary_payload(record_ref, provider)
    )


def refresh_group_issue_summary(record_ref, group=None, locations=None):
    group = group if group is not None else (record_ref.get().to_dict() or {})
    locations = locations if locations is not None else [
        {"id": snapshot.id, **(snapshot.to_dict() or {})}
        for snapshot in record_ref.collection(LOCATION_COLLECTION).stream()
    ]
    return write_issue_summary(
        record_ref, "practice", group_summary_payload(record_ref, group, locations)
    )


def read_record_issue_summary(record_ref, record_type):
    snapshot = summary_ref(record_ref, record_type).get()
    payload = snapshot.to_dict() if snapshot.exists else None
    if payload and payload.get("evaluated_on") == date.today().isoformat():
        return payload
    if record_type == "provider":
        return refresh_provider_issue_summary(record_ref)
    return refresh_group_issue_summary(record_ref)


def refresh_entity_issue_summaries(entity_ref):
    status_ref = entity_ref.collection(ISSUE_SUMMARY_COLLECTION).document(
        SUMMARY_STATUS_DOCUMENT_ID
    )
    status = status_ref.get()
    today = date.today().isoformat()
    if status.exists and (status.to_dict() or {}).get("evaluated_on") == today:
        return 0

    summaries = {
        snapshot.id: snapshot.to_dict() or {}
        for snapshot in entity_ref.collection(ISSUE_SUMMARY_COLLECTION).stream()
    }
    refreshed = 0
    for snapshot in entity_ref.collection(PROVIDER_COLLECTION).stream():
        key = summary_document_id("provider", snapshot.id)
        if summaries.get(key, {}).get("evaluated_on") != today:
            refresh_provider_issue_summary(snapshot.reference, snapshot.to_dict() or {})
            refreshed += 1
    for snapshot in entity_ref.collection(GROUP_COLLECTION).stream():
        key = summary_document_id("practice", snapshot.id)
        if summaries.get(key, {}).get("evaluated_on") != today:
            refresh_group_issue_summary(snapshot.reference, snapshot.to_dict() or {})
            refreshed += 1
    status_ref.set(
        {"evaluated_on": today, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True
    )
    return refreshed


def list_issue_summaries(entity_ref):
    # A new client or a new deployment is backfilled once; normal reads only
    # stream compact summaries and never touch every record's revisions.
    refresh_entity_issue_summaries(entity_ref)
    return [
        {"id": snapshot.id, **(snapshot.to_dict() or {})}
        for snapshot in entity_ref.collection(ISSUE_SUMMARY_COLLECTION).stream()
        if snapshot.id != SUMMARY_STATUS_DOCUMENT_ID
    ]


def delete_issue_summary(entity_ref, record_type, record_id):
    entity_ref.collection(ISSUE_SUMMARY_COLLECTION).document(
        summary_document_id(record_type, record_id)
    ).delete()
