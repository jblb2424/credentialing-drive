"""Persist reversible user decisions for dynamically calculated issues."""

import hashlib

from google.cloud import firestore

from app.config import ISSUE_RESOLUTION_COLLECTION


def resolution_document_id(issue_id):
    """Hash generated IDs so any field-path punctuation remains safe in Firestore."""
    return hashlib.sha256(issue_id.encode("utf-8")).hexdigest()


def removed_issue_ids(record_ref):
    return {
        data.get("issue_id")
        for snapshot in record_ref.collection(ISSUE_RESOLUTION_COLLECTION).stream()
        if (data := (snapshot.to_dict() or {})).get("status") == "removed"
        and data.get("issue_id")
    }


def filter_removed_issues(record_ref, issues):
    removed_ids = removed_issue_ids(record_ref)
    return [issue for issue in issues if issue.get("id") not in removed_ids]


def remove_generated_issue(record_ref, issue, user):
    """Record a user decision without altering source credentialing data."""
    record_ref.collection(ISSUE_RESOLUTION_COLLECTION).document(
        resolution_document_id(issue["id"])
    ).set(
        {
            "issue_id": issue["id"],
            "status": "removed",
            "issue_type": issue.get("type"),
            "affected_fields": issue.get("affected_fields") or [],
            "removed_at": firestore.SERVER_TIMESTAMP,
            "removed_by": {
                "user_id": user.get("uid"),
                "display_name": user.get("display_name"),
                "role": user.get("role"),
            },
        },
        merge=True,
    )
