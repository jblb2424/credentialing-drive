import unittest

from app.providers import get_group_providers


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
                FakeSnapshot("membership-b", {"provider_id": "provider-b"}),
                FakeSnapshot("membership-a", {"provider_id": "provider-a"}),
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
        self.assertEqual(providers[1]["provider_type"], "NP")


if __name__ == "__main__":
    unittest.main()
