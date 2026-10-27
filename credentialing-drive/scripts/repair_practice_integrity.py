"""Repair safe practice aliases and individual-provider practices.

Run without --apply first. The script only auto-merges names that differ by an
explicit synthetic fixture qualifier. Moving an erroneous individual-provider
affiliation requires --individual-affiliation-target to avoid guessing.
"""

import argparse
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import (
    GROUP_COLLECTION, PROVIDER_COLLECTION, PROVIDER_GROUP_MEMBERSHIP_COLLECTION,
)
from app.connections import get_firestore_client
from app.issue_summaries import delete_issue_summary, refresh_group_issue_summary
from app.practice_validation import (
    canonical_practice_name, candidate_matches_provider, compact_name,
)
from app.providers import delete_document_tree, get_entity_ref, merge_unique


def group_name_key(group):
    return canonical_practice_name(group.get("legal_name") or group.get("name")).casefold()


def copy_document_tree(source_ref, target_ref):
    """Copy child records before deleting a merged practice's source tree."""
    target_ref.set(source_ref.get().to_dict() or {}, merge=True)
    for collection in source_ref.collections():
        for snapshot in collection.stream():
            copy_document_tree(
                snapshot.reference,
                target_ref.collection(collection.id).document(snapshot.id),
            )


def move_memberships(entity_ref, source_group_id, target_group_id):
    for snapshot in entity_ref.collection(PROVIDER_GROUP_MEMBERSHIP_COLLECTION).where(
        "group_id", "==", source_group_id
    ).stream():
        membership = snapshot.to_dict() or {}
        provider_id = membership.get("provider_id")
        if not provider_id:
            delete_document_tree(snapshot.reference)
            continue

        target_ref = entity_ref.collection(PROVIDER_GROUP_MEMBERSHIP_COLLECTION).document(
            f"{provider_id}-{target_group_id}"
        )
        target = target_ref.get().to_dict() or {}
        target_ref.set(
            {
                **membership,
                "provider_id": provider_id,
                "group_id": target_group_id,
                "provider_type": target.get("provider_type") or membership.get("provider_type"),
                "location_ids": merge_unique(
                    target.get("location_ids"), membership.get("location_ids")
                ),
            },
            merge=True,
        )
        for collection in snapshot.reference.collections():
            for child in collection.stream():
                copy_document_tree(
                    child.reference,
                    target_ref.collection(collection.id).document(child.id),
                )
        delete_document_tree(snapshot.reference)


def merge_groups(
    client, entity_ref, source_snapshot, target_snapshot, include_source_name_as_alias=False
):
    source = source_snapshot.to_dict() or {}
    # The target can receive multiple merges in one run; use its latest state
    # so one merge cannot overwrite aliases created by an earlier one.
    target = target_snapshot.reference.get().to_dict() or {}
    source_name = compact_name(source.get("legal_name") or source.get("name"))
    target_name = compact_name(target.get("legal_name") or target.get("name"))
    aliases = target.get("aliases")
    if include_source_name_as_alias:
        aliases = merge_unique(aliases, source.get("aliases"))
        aliases = merge_unique(
            aliases, [source_name] if source_name and source_name != target_name else []
        )
    updates = {
        field_name: source.get(field_name)
        for field_name in ("type_2_npi", "tax_id", "dba", "w9")
        if not target.get(field_name) and source.get(field_name)
    }
    if aliases:
        updates["aliases"] = aliases
    if updates:
        target_snapshot.reference.set(updates, merge=True)

    for collection in source_snapshot.reference.collections():
        for child in collection.stream():
            copy_document_tree(
                child.reference,
                target_snapshot.reference.collection(collection.id).document(child.id),
            )
    move_memberships(entity_ref, source_snapshot.id, target_snapshot.id)
    for identity in entity_ref.collection("group_identities").where(
        "group_id", "==", source_snapshot.id
    ).stream():
        identity.reference.set({"group_id": target_snapshot.id}, merge=True)
    delete_issue_summary(entity_ref, "practice", source_snapshot.id)
    delete_document_tree(source_snapshot.reference)
    refresh_group_issue_summary(target_snapshot.reference)


def find_group_by_name(groups, name):
    desired = canonical_practice_name(name).casefold()
    matches = [
        snapshot for snapshot in groups
        if compact_name(
            (snapshot.to_dict() or {}).get("legal_name")
            or (snapshot.to_dict() or {}).get("name")
        ).casefold() == desired
    ]
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one target practice named {name!r}; found {len(matches)}")
    return matches[0]


def repair(entity_id, apply=False, individual_affiliation_target=None):
    client = get_firestore_client()
    entity_ref = get_entity_ref(client, entity_id)
    groups = list(entity_ref.collection(GROUP_COLLECTION).stream())
    providers = [
        snapshot.to_dict() or {}
        for snapshot in entity_ref.collection(PROVIDER_COLLECTION).stream()
    ]
    actions = []

    by_canonical_name = {}
    for snapshot in groups:
        group = snapshot.to_dict() or {}
        name = compact_name(group.get("legal_name") or group.get("name"))
        canonical = canonical_practice_name(name)
        if name and canonical and name != canonical:
            by_canonical_name.setdefault(canonical.casefold(), []).append(snapshot)

    for canonical, aliases in by_canonical_name.items():
        canonical_matches = [
            snapshot for snapshot in groups
            if group_name_key(snapshot.to_dict() or {}) == canonical
            and compact_name((snapshot.to_dict() or {}).get("legal_name")).casefold() == canonical
        ]
        if len(canonical_matches) != 1:
            actions.append({
                "action": "review_alias_merge",
                "canonical_name": canonical,
                "alias_group_ids": [snapshot.id for snapshot in aliases],
                "reason": "A unique canonical practice record was not found.",
            })
            continue
        target = canonical_matches[0]
        for source in aliases:
            if source.id == target.id:
                continue
            actions.append({
                "action": "merge_fixture_alias",
                "source_group_id": source.id,
                "target_group_id": target.id,
                "canonical_name": canonical,
            })
            if apply:
                merge_groups(
                    client,
                    entity_ref,
                    source,
                    target,
                    include_source_name_as_alias=True,
                )

    if individual_affiliation_target:
        target = find_group_by_name(groups, individual_affiliation_target)
        for source in groups:
            source_group = source.to_dict() or {}
            source_name = source_group.get("legal_name") or source_group.get("name")
            matching_provider = next(
                (
                    provider for provider in providers
                    if candidate_matches_provider(source_name, provider.get("provider") or {})
                ),
                None,
            )
            if not matching_provider:
                continue
            actions.append({
                "action": "remove_individual_practice",
                "source_group_id": source.id,
                "target_group_id": target.id,
                "practice_name": source_name,
            })
            if apply:
                merge_groups(client, entity_ref, source, target)

    return actions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entity", default="dummy-client")
    parser.add_argument("--apply", action="store_true", help="Persist the proposed repairs.")
    parser.add_argument(
        "--individual-affiliation-target",
        help="Canonical practice to receive memberships from individual-provider practices.",
    )
    args = parser.parse_args()

    actions = repair(
        args.entity,
        apply=args.apply,
        individual_affiliation_target=args.individual_affiliation_target,
    )
    for action in actions:
        print(action)
    print(f"{'Applied' if args.apply else 'Proposed'} {len(actions)} practice integrity action(s).")


if __name__ == "__main__":
    main()
