import unittest
from unittest.mock import MagicMock, patch

from app import providers as provider_module
from app.providers import (
    get_group_providers,
    migrate_legacy_provider_locations,
    remove_group_provider_membership,
    update_group_provider_membership,
)


class FakeSnapshot:
    def __init__(self, identifier, data=None, exists=True):
        self.id = identifier
        self._data = data or {}
        self.exists = exists

    def to_dict(self):
        return self._data


class FakeDocument:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def get(self):
        return self.snapshot


class FakeStoredDocument(FakeDocument):
    def set(self, data, merge=False):
        self.snapshot._data = {**self.snapshot.to_dict(), **data} if merge else data


class FakeMembershipCollection:
    def __init__(self, memberships):
        self.memberships = memberships

    def where(self, *_args):
        return self

    def stream(self):
        return self.memberships


class FakeProviderCollection:
    def __init__(self, providers):
        self.providers = providers

    def document(self, provider_id):
        return FakeDocument(self.providers.get(provider_id, FakeSnapshot(provider_id, exists=False)))


class FakeStoredCollection:
    def __init__(self, documents):
        self.documents = documents

    def document(self, document_id):
        return self.documents.setdefault(document_id, FakeStoredDocument(FakeSnapshot(document_id)))

    def stream(self):
        return [document.get() for document in self.documents.values()]


class FakeGroupDocument(FakeStoredDocument):
    def __init__(self, snapshot, locations):
        super().__init__(snapshot)
        self.locations = locations

    def collection(self, name):
        if name == "locations":
            return self.locations
        raise AssertionError(f"Unexpected group collection: {name}")


class FakeWritableEntityReference:
    def __init__(self, group, provider, memberships):
        self.groups = FakeStoredCollection({"group-1": group})
        self.providers = FakeStoredCollection({"provider-1": provider})
        self.memberships = FakeStoredCollection(memberships)

    def collection(self, name):
        if name == "groups":
            return self.groups
        if name == "providers":
            return self.providers
        if name == "provider_group_memberships":
            return self.memberships
        raise AssertionError(f"Unexpected collection: {name}")


class FakeEntityReference:
    def __init__(self, memberships, providers):
        self.memberships = FakeMembershipCollection(memberships)
        self.providers = FakeProviderCollection(providers)

    def collection(self, name):
        if name == "provider_group_memberships":
            return self.memberships
        if name == "providers":
            return self.providers
        raise AssertionError(f"Unexpected collection: {name}")


class ProviderRelationshipTests(unittest.TestCase):
    def test_group_providers_returns_active_records_once_in_name_order(self):
        entity = FakeEntityReference(
            [
                FakeSnapshot("membership-b", {"provider_id": "provider-b", "location_ids": ["location-b"]}),
                FakeSnapshot("membership-a", {"provider_id": "provider-a", "location_ids": ["location-a"]}),
                FakeSnapshot("membership-duplicate", {"provider_id": "provider-b"}),
                FakeSnapshot("membership-deleted", {"provider_id": "provider-deleted"}),
            ],
            {
                "provider-a": FakeSnapshot("provider-a", {"provider": {"name": "Avery Rowan", "npi": "1234567893", "provider_type": "MD"}}),
                "provider-b": FakeSnapshot("provider-b", {"provider": {"name": "Blair Smith", "npi": "2345678901", "provider_type": "NP"}}),
            },
        )

        providers = get_group_providers(entity, "group-1")

        self.assertEqual([provider["id"] for provider in providers], ["provider-a", "provider-b"])
        self.assertEqual(providers[0]["name"], "Avery Rowan")
        self.assertEqual(providers[0]["location_ids"], ["location-a"])
        self.assertEqual(providers[1]["provider_type"], "NP")

    def test_manual_provider_location_link_requires_practice_locations(self):
        locations = FakeStoredCollection(
            {
                "location-a": FakeStoredDocument(FakeSnapshot("location-a")),
                "location-b": FakeStoredDocument(FakeSnapshot("location-b")),
            }
        )
        group = FakeGroupDocument(FakeSnapshot("group-1"), locations)
        provider = FakeStoredDocument(
            FakeSnapshot("provider-1", {"provider": {"provider_type": "MD"}})
        )
        membership = FakeStoredDocument(
            FakeSnapshot("provider-1-group-1", {"location_ids": ["location-a"]})
        )
        entity = FakeWritableEntityReference(
            group, provider, {"provider-1-group-1": membership}
        )

        with patch.object(provider_module, "get_firestore_client"), patch.object(
            provider_module, "get_entity_ref", return_value=entity
        ), patch.object(provider_module, "get_group", return_value={"id": "group-1"}), patch.object(
            provider_module, "record_group_revision"
        ) as group_revision, patch.object(provider_module, "record_provider_revision") as provider_revision:
            result = update_group_provider_membership(
                "group-1", "provider-1", ["location-b", "location-a", "location-b"],
                {"uid": "user-1"}, "entity-1"
            )

            self.assertEqual(result, {"id": "group-1"})
            self.assertEqual(membership.get().to_dict()["location_ids"], ["location-b", "location-a"])
            group_revision.assert_called_once()
            provider_revision.assert_called_once()

        with patch.object(provider_module, "get_firestore_client"), patch.object(
            provider_module, "get_entity_ref", return_value=entity
        ):
            with self.assertRaisesRegex(Exception, "do not belong to this practice"):
                update_group_provider_membership(
                    "group-1", "provider-1", ["unknown-location"], {"uid": "user-1"}, "entity-1"
                )

    def test_legacy_provider_locations_migrate_only_to_the_affiliated_group(self):
        provider_snapshot = MagicMock(id="provider-1")
        provider_snapshot.to_dict.return_value = {
            "provider_locations": [{"display_name": "Main Clinic"}],
        }
        membership_snapshot = MagicMock()
        membership_snapshot.to_dict.return_value = {
            "provider_id": "provider-1",
            "group_id": "group-1",
            "location_ids": ["location-existing"],
        }
        group_snapshot = MagicMock(exists=True)
        group_ref = MagicMock()
        group_ref.get.return_value = group_snapshot
        provider_collection = MagicMock()
        provider_collection.stream.return_value = [provider_snapshot]
        membership_collection = MagicMock()
        membership_collection.where.return_value.stream.return_value = [membership_snapshot]
        group_collection = MagicMock()
        group_collection.document.return_value = group_ref
        entity = MagicMock()
        entity.collection.side_effect = lambda name: {
            "providers": provider_collection,
            "provider_group_memberships": membership_collection,
            "groups": group_collection,
        }[name]

        with patch.object(provider_module, "get_firestore_client"), patch.object(
            provider_module, "get_entity_ref", return_value=entity
        ), patch.object(
            provider_module, "upsert_group_locations", return_value=["location-new"]
        ) as upsert_locations, patch.object(
            provider_module, "record_provider_revision"
        ), patch.object(provider_module, "record_group_revision"), patch.object(
            provider_module, "refresh_provider_issue_summary"
        ), patch.object(provider_module, "refresh_group_issue_summary"):
            result = migrate_legacy_provider_locations("entity-1")

        upsert_locations.assert_called_once_with(
            group_ref, [{"display_name": "Main Clinic"}]
        )
        membership_snapshot.reference.set.assert_called_once_with(
            {"location_ids": ["location-existing", "location-new"]}, merge=True
        )
        provider_snapshot.reference.set.assert_called_once_with(
            {"provider_locations": provider_module.firestore.DELETE_FIELD}, merge=True
        )
        self.assertEqual(result["providers_migrated"], 1)
        self.assertEqual(result["locations_migrated"], 1)
        self.assertEqual(result["unresolved"], [])

    def test_unlinking_provider_removes_only_the_membership(self):
        group_snapshot = MagicMock(exists=True)
        provider_snapshot = MagicMock(exists=True)
        provider_snapshot.to_dict.return_value = {"provider": {"name": "Avery Rowan"}}
        membership_snapshot = MagicMock(exists=True)
        membership_snapshot.to_dict.return_value = {
            "provider_id": "provider-1",
            "group_id": "group-1",
            "location_ids": ["location-a"],
        }
        membership_ref = MagicMock()
        membership_ref.get.return_value = membership_snapshot
        membership_ref.collection.return_value.stream.return_value = []
        group_ref = MagicMock()
        group_ref.get.return_value = group_snapshot
        provider_ref = MagicMock()
        provider_ref.get.return_value = provider_snapshot
        group_collection = MagicMock()
        group_collection.document.return_value = group_ref
        provider_collection = MagicMock()
        provider_collection.document.return_value = provider_ref
        membership_collection = MagicMock()
        membership_collection.document.return_value = membership_ref
        entity = MagicMock()
        entity.collection.side_effect = lambda name: {
            "groups": group_collection,
            "providers": provider_collection,
            "provider_group_memberships": membership_collection,
        }[name]

        with patch.object(provider_module, "get_firestore_client"), patch.object(
            provider_module, "get_entity_ref", return_value=entity
        ), patch.object(provider_module, "get_group", return_value={"id": "group-1"}), patch.object(
            provider_module, "record_group_revision"
        ) as group_revision, patch.object(
            provider_module, "record_provider_revision"
        ) as provider_revision, patch.object(
            provider_module, "refresh_group_issue_summary"
        ), patch.object(provider_module, "refresh_provider_issue_summary"):
            result = remove_group_provider_membership(
                "group-1", "provider-1", {"uid": "user-1"}, "entity-1"
            )

        membership_ref.delete.assert_called_once()
        group_revision.assert_called_once()
        provider_revision.assert_called_once()
        self.assertEqual(result, {"id": "group-1"})


if __name__ == "__main__":
    unittest.main()
