"""Fresh-model reconciliation for repeated provider record categories."""

import json
import logging
from copy import deepcopy

from app.config import RECONCILIATION_COLLECTION, reconciliation_enabled

logger = logging.getLogger(__name__)

RECONCILABLE_CATEGORIES = (
    "provider_locations",
    "payer_enrollments",
    "licenses",
    "specialties",
    "education",
    "liability_insurance",
    "expiration_dates",
)
VALID_DECISIONS = {"merge", "keep_separate", "needs_review"}


def candidate_categories(provider):
    """Only send repeated categories to the second-pass model."""
    return {
        category: deepcopy(provider.get(category) or [])
        for category in RECONCILABLE_CATEGORIES
        if len(provider.get(category) or []) > 1
    }


def json_equal(left, right):
    return json.dumps(left, sort_keys=True, default=str) == json.dumps(
        right, sort_keys=True, default=str
    )


def source_supports_value(value, source_records):
    """Reject model output that contains a value absent from its cited sources."""
    if isinstance(value, dict):
        if not all(isinstance(record, dict) for record in source_records):
            return False
        return all(
            any(json_equal(record.get(field_name), field_value) for record in source_records)
            for field_name, field_value in value.items()
        )
    return any(json_equal(record, value) for record in source_records)


def validate_reconciliation(candidates, response):
    """Validate model decisions before any provider record is modified."""
    categories = response.get("categories") if isinstance(response, dict) else None
    if not isinstance(categories, dict) or set(categories) != set(candidates):
        raise ValueError("Reconciliation model did not return every requested category")

    reconciled = {}
    summaries = []
    for category, source_records in candidates.items():
        groups = categories.get(category)
        if not isinstance(groups, list) or not groups:
            raise ValueError(f"Reconciliation model omitted decisions for {category}")

        seen_indices = set()
        canonical_records = []
        category_summary = []
        for group in groups:
            if not isinstance(group, dict):
                raise ValueError(f"Reconciliation model returned invalid {category} group")
            source_indices = group.get("source_record_indices")
            if (
                not isinstance(source_indices, list)
                or not source_indices
                or any(not isinstance(index, int) for index in source_indices)
                or len(set(source_indices)) != len(source_indices)
                or any(index < 0 or index >= len(source_records) for index in source_indices)
                or seen_indices.intersection(source_indices)
            ):
                raise ValueError(f"Reconciliation model returned invalid source indices for {category}")
            seen_indices.update(source_indices)

            decision = group.get("decision")
            if decision not in VALID_DECISIONS:
                raise ValueError(f"Reconciliation model returned invalid decision for {category}")
            cited_records = [source_records[index] for index in source_indices]
            confidence = group.get("confidence")
            if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                raise ValueError(f"Reconciliation model returned invalid confidence for {category}")

            if decision == "needs_review":
                canonical_records.extend(cited_records)
            else:
                canonical_record = group.get("canonical_record")
                if not source_supports_value(canonical_record, cited_records):
                    raise ValueError(f"Reconciliation model introduced unsupported {category} data")
                canonical_records.append(canonical_record)

            category_summary.append(
                {
                    "source_record_indices": source_indices,
                    "decision": decision,
                    "confidence": float(confidence),
                    "reason": str(group.get("reason") or "")[:500],
                }
            )

        if seen_indices != set(range(len(source_records))):
            raise ValueError(f"Reconciliation model did not account for every {category} record")
        reconciled[category] = canonical_records
        summaries.append(
            {
                "category": category,
                "before_count": len(source_records),
                "after_count": len(canonical_records),
                "decisions": category_summary,
            }
        )
    return reconciled, summaries


def reconcile_provider_records(provider_ref):
    """Reconcile repeated records and retain an auditable model-decision history."""
    if not reconciliation_enabled():
        return {"status": "disabled", "updated_categories": []}

    snapshot = provider_ref.get()
    if not snapshot.exists:
        raise ValueError("Provider record not found")
    provider = snapshot.to_dict() or {}
    candidates = candidate_categories(provider)
    if not candidates:
        return {"status": "not_needed", "updated_categories": []}

    try:
        from app.gemini import reconcile_provider_records_with_gemini

        response, model = reconcile_provider_records_with_gemini(candidates)
        reconciled, summaries = validate_reconciliation(candidates, response)
    except Exception:
        # An unavailable or invalid second-pass response must never block ingestion
        # or mutate structured data.
        logger.exception("Provider reconciliation failed for provider_id=%s", provider_ref.id)
        return {"status": "failed", "updated_categories": []}

    updates = {
        category: records
        for category, records in reconciled.items()
        if not json_equal(records, candidates[category])
    }
    review_count = sum(
        1
        for category in summaries
        for decision in category["decisions"]
        if decision["decision"] == "needs_review"
    )

    from google.cloud import firestore

    reconciliation_ref = provider_ref.collection(RECONCILIATION_COLLECTION).document()
    reconciliation_ref.set(
        {
            "revision_type": "model_reconciliation",
            "model": model,
            "categories": summaries,
            "updated_categories": sorted(updates),
            "review_count": review_count,
            "recorded_at": firestore.SERVER_TIMESTAMP,
        }
    )
    if updates:
        provider_ref.set(updates, merge=True)
    return {
        "status": "reconciled" if updates else "no_changes",
        "updated_categories": sorted(updates),
        "review_count": review_count,
        "reconciliation_id": reconciliation_ref.id,
        "provider": {**provider, **updates},
    }
