"""Entity-scoped report previews and safe CSV exports."""

from collections import defaultdict
from datetime import datetime, timezone

from fastapi import HTTPException

from app.connections import get_firestore_client
from app.config import (
    GROUP_COLLECTION,
    LOCATION_COLLECTION,
    PAYER_ENROLLMENT_COLLECTION,
    PROVIDER_COLLECTION,
    PROVIDER_GROUP_MEMBERSHIP_COLLECTION,
)
from app.issues import calculate_provider_issues, expiration_records
from app.issue_resolutions import filter_removed_issues, removed_issue_ids
from app.provider_names import enrich_provider_name
from app.providers import get_entity_ref, serialize_document
from app.report_exports import report_csv, report_filename


REPORT_DEFINITIONS = {
    "provider_roster": {
        "title": "Provider roster",
        "description": "Provider identity, credentials, and practice affiliations.",
        "columns": (
            ("provider_name", "Provider"),
            ("npi", "NPI"),
            ("credentials", "Credentials"),
            ("caqh_id", "CAQH ID"),
            ("provider_type", "Provider type"),
            ("practices", "Practices"),
            ("locations", "Provider locations"),
        ),
    },
    "open_issues": {
        "title": "Open issues",
        "description": "Outstanding record-integrity and credentialing work.",
        "columns": (
            ("provider_name", "Provider"),
            ("npi", "NPI"),
            ("issue_type", "Issue type"),
            ("severity", "Severity"),
            ("details", "Details"),
            ("source_context", "Source context"),
        ),
    },
    "credential_expirations": {
        "title": "Credential expirations",
        "description": "Expired and upcoming credential renewal dates.",
        "columns": (
            ("provider_name", "Provider"),
            ("npi", "NPI"),
            ("credential", "Credential"),
            ("credential_type", "Credential type"),
            ("expiration_date", "Expiration date"),
            ("status", "Status"),
            ("timing", "Timing"),
        ),
    },
    "payer_enrollment": {
        "title": "Payer enrollment",
        "description": "Provider participation and enrollment status by practice.",
        "columns": (
            ("provider_name", "Provider"),
            ("npi", "NPI"),
            ("payer", "Payer"),
            ("practice", "Practice"),
            ("locations", "Locations"),
            ("enrollment_status", "Enrollment status"),
        ),
    },
    "practice_directory": {
        "title": "Practice directory",
        "description": "Practice identifiers, locations, and provider counts.",
        "columns": (
            ("practice", "Practice"),
            ("type_2_npi", "Type 2 NPI"),
            ("tax_id", "Tax ID"),
            ("dba", "DBA"),
            ("locations", "Locations"),
            ("provider_count", "Provider count"),
        ),
    },
}


def report_catalog():
    return [
        {
            "id": report_type,
            "title": definition["title"],
            "description": definition["description"],
            "columns": [{"key": key, "label": label} for key, label in definition["columns"]],
        }
        for report_type, definition in REPORT_DEFINITIONS.items()
    ]


def value_text(value, empty="Not captured"):
    if value in (None, "", [], {}):
        return empty
    if isinstance(value, list):
        return "; ".join(value_text(item, empty="") for item in value if item not in (None, "")) or empty
    if isinstance(value, dict):
        return "; ".join(f"{key}: {value_text(item, empty='')}" for key, item in value.items())
    return str(value)


def location_name(location):
    if not isinstance(location, dict):
        return value_text(location)
    return value_text(
        location.get("display_name") or location.get("name") or location.get("address"),
    )


def issue_details(issue):
    field = issue.get("display_label") or ", ".join(issue.get("affected_fields") or [])
    if issue.get("type") == "missing_data":
        return f"Missing {field or 'required information'}"
    if issue.get("type") == "discrepancy":
        return f"Conflicting {field or 'provider information'}"
    return value_text(issue.get("credential_label") or field)


def load_context(entity_id):
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    groups = {
        snapshot.id: serialize_document(snapshot)
        for snapshot in entity_ref.collection(GROUP_COLLECTION).stream()
    }
    group_locations = {
        group_id: {
            snapshot.id: serialize_document(snapshot)
            for snapshot in entity_ref.collection(GROUP_COLLECTION).document(group_id).collection(
                LOCATION_COLLECTION
            ).stream()
        }
        for group_id in groups
    }
    memberships = defaultdict(list)
    for snapshot in entity_ref.collection(PROVIDER_GROUP_MEMBERSHIP_COLLECTION).stream():
        membership = serialize_document(snapshot)
        memberships[membership.get("provider_id")].append(
            {**membership, "_reference": snapshot.reference}
        )
    return entity_ref, groups, group_locations, memberships


def provider_contexts(entity_ref, groups, group_locations, memberships, provider_id=None, practice_id=None):
    contexts = []
    for snapshot in entity_ref.collection(PROVIDER_COLLECTION).stream():
        if provider_id and snapshot.id != provider_id:
            continue
        provider_memberships = memberships.get(snapshot.id, [])
        if practice_id and not any(item.get("group_id") == practice_id for item in provider_memberships):
            continue
        provider = snapshot.to_dict() or {}
        contexts.append(
            {
                "id": snapshot.id,
                "reference": snapshot.reference,
                "provider": provider,
                "profile": enrich_provider_name(provider.get("provider") or {}),
                "memberships": provider_memberships,
                "groups": groups,
                "group_locations": group_locations,
            }
        )
    return contexts


def affiliation_locations(context, membership):
    locations = context["group_locations"].get(membership.get("group_id"), {})
    return [
        location_name(locations[location_id])
        for location_id in membership.get("location_ids") or []
        if location_id in locations
    ]


def provider_roster_rows(contexts):
    rows = []
    for context in contexts:
        profile = context["profile"]
        affiliations = [
            context["groups"].get(item.get("group_id"), {})
            for item in context["memberships"]
        ]
        locations = [
            location
            for membership in context["memberships"]
            for location in affiliation_locations(context, membership)
        ]
        rows.append(
            {
                "_provider_id": context["id"],
                "provider_name": value_text(profile.get("name"), "Provider name not captured"),
                "npi": value_text(profile.get("npi"), "NPI not captured"),
                "credentials": value_text(profile.get("credentials")),
                "caqh_id": value_text(profile.get("caqh_id")),
                "provider_type": value_text(profile.get("provider_type")),
                "practices": value_text([group.get("legal_name") or group.get("name") for group in affiliations]),
                "locations": value_text(locations),
            }
        )
    return rows


def open_issue_rows(contexts, issue_type=None):
    rows = []
    for context in contexts:
        for issue in filter_removed_issues(
            context["reference"],
            calculate_provider_issues(context["reference"], context["provider"]),
        ):
            if issue_type and issue.get("type") != issue_type:
                continue
            rows.append(
                {
                    "_provider_id": context["id"],
                    "provider_name": value_text(context["profile"].get("name"), "Provider name not captured"),
                    "npi": value_text(context["profile"].get("npi"), "NPI not captured"),
                    "issue_type": value_text(issue.get("type")).replace("_", " ").title(),
                    "severity": value_text(issue.get("severity")).title(),
                    "details": issue_details(issue),
                    "source_context": (
                        "Not applicable"
                        if issue.get("type") == "missing_data"
                        else value_text(issue.get("file_name"), "Source file not captured")
                    ),
                }
            )
    return sorted(rows, key=lambda row: (row["severity"], row["provider_name"], row["details"]))


def expiration_rows(contexts, expiration_status=None):
    rows = []
    for context in contexts:
        removed_ids = removed_issue_ids(context["reference"])
        for record in expiration_records(context["provider"]):
            if record["id"] in removed_ids:
                continue
            if expiration_status and record.get("type") != expiration_status:
                continue
            timing = (
                f"{record['days_past_expiration']} days past due"
                if record.get("type") == "expired"
                else f"{record.get('days_until_expiration', 0)} days remaining"
            )
            rows.append(
                {
                    "_provider_id": context["id"],
                    "provider_name": value_text(context["profile"].get("name"), "Provider name not captured"),
                    "npi": value_text(context["profile"].get("npi"), "NPI not captured"),
                    "credential": value_text(record.get("credential_label"), "Credential not captured"),
                    "credential_type": value_text(record.get("credential_category")).replace("_", " ").title(),
                    "expiration_date": value_text(record.get("expiration_date")),
                    "status": value_text(record.get("type")).title(),
                    "timing": timing,
                }
            )
    return sorted(rows, key=lambda row: row["expiration_date"])


def payer_enrollment_rows(contexts):
    rows = []
    for context in contexts:
        for membership in context["memberships"]:
            group = context["groups"].get(membership.get("group_id"), {})
            locations = affiliation_locations(context, membership)
            for snapshot in membership["_reference"].collection(PAYER_ENROLLMENT_COLLECTION).stream():
                enrollment = serialize_document(snapshot)
                rows.append(
                    {
                        "_provider_id": context["id"],
                        "provider_name": value_text(context["profile"].get("name"), "Provider name not captured"),
                        "npi": value_text(context["profile"].get("npi"), "NPI not captured"),
                        "payer": value_text(enrollment.get("payer_name")),
                        "practice": value_text(group.get("legal_name") or group.get("name")),
                        "locations": value_text(locations),
                        "enrollment_status": value_text(enrollment.get("status")).title(),
                    }
                )
    return sorted(rows, key=lambda row: (row["practice"], row["provider_name"], row["payer"]))


def practice_directory_rows(groups, group_locations, memberships, practice_id=None):
    provider_counts = defaultdict(int)
    for provider_memberships in memberships.values():
        for membership in provider_memberships:
            provider_counts[membership.get("group_id")] += 1
    rows = []
    for group_id, group in groups.items():
        if practice_id and group_id != practice_id:
            continue
        rows.append(
            {
                "practice": value_text(group.get("legal_name") or group.get("name"), "Practice name not captured"),
                "type_2_npi": value_text(group.get("type_2_npi")),
                "tax_id": value_text(group.get("tax_id")),
                "dba": value_text(group.get("dba")),
                "locations": value_text([location_name(location) for location in group_locations.get(group_id, {}).values()]),
                "provider_count": provider_counts[group_id],
            }
        )
    return sorted(rows, key=lambda row: row["practice"])


def build_report(report_type, entity_id, practice_id=None, provider_id=None, issue_type=None, expiration_status=None):
    definition = REPORT_DEFINITIONS.get(report_type)
    if not definition:
        raise HTTPException(status_code=404, detail="Unknown report type")
    if issue_type not in (None, "", "all", "missing_data", "discrepancy", "expired", "expiring"):
        raise HTTPException(status_code=400, detail="Unknown issue type")
    if expiration_status not in (None, "", "all", "expired", "expiring"):
        raise HTTPException(status_code=400, detail="Unknown expiration status")

    entity_ref, groups, group_locations, memberships = load_context(entity_id)
    if report_type == "practice_directory":
        rows = practice_directory_rows(groups, group_locations, memberships, practice_id)
    else:
        contexts = provider_contexts(
            entity_ref, groups, group_locations, memberships, provider_id, practice_id
        )
        if report_type == "provider_roster":
            rows = provider_roster_rows(contexts)
        elif report_type == "open_issues":
            rows = open_issue_rows(contexts, issue_type if issue_type != "all" else None)
        elif report_type == "credential_expirations":
            rows = expiration_rows(contexts, expiration_status if expiration_status != "all" else None)
        else:
            rows = payer_enrollment_rows(contexts)

    return {
        "report_type": report_type,
        "title": definition["title"],
        "description": definition["description"],
        "columns": [{"key": key, "label": label} for key, label in definition["columns"]],
        "rows": rows,
        "row_count": len(rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": {
            "practice_id": practice_id,
            "provider_id": provider_id,
            "issue_type": issue_type,
            "expiration_status": expiration_status,
        },
    }
