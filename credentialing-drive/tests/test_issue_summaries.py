import unittest

from app.issue_summaries import group_summary_payload, provider_summary_payload, summary_document_id


class FakeSnapshot:
    def __init__(self, snapshot_id, data):
        self.id = snapshot_id
        self._data = data

    def to_dict(self):
        return self._data


class FakeCollection:
    def __init__(self, snapshots=()):
        self.snapshots = list(snapshots)

    def stream(self):
        return iter(self.snapshots)


class FakeRecordReference:
    def __init__(self, record_id, revisions=(), resolutions=(), locations=()):
        self.id = record_id
        self._collections = {
            "revisions": FakeCollection(revisions),
            "issue_resolutions": FakeCollection(resolutions),
            "locations": FakeCollection(locations),
        }

    def collection(self, name):
        return self._collections[name]


class IssueSummaryTests(unittest.TestCase):
    def test_provider_summary_keeps_active_derived_issues_with_record_identity(self):
        record_ref = FakeRecordReference("provider-1")
        summary = provider_summary_payload(
            record_ref,
            {"provider": {"name": "Avery Rowan", "npi": "1234567890"}},
        )

        self.assertEqual(summary["record_type"], "provider")
        self.assertEqual(summary["record_id"], "provider-1")
        self.assertEqual(summary["record_name"], "Avery Rowan")
        self.assertEqual(summary["record_identifier"], "1234567890")
        self.assertTrue(summary["issue_count"])
        self.assertEqual(summary["issue_count"], len(summary["issues"]))

    def test_group_summary_uses_explicit_locations_without_revisions(self):
        record_ref = FakeRecordReference("practice-1")
        summary = group_summary_payload(
            record_ref,
            {"legal_name": "Example Internal Medicine"},
            [{"display_name": "Main Office"}],
        )

        self.assertEqual(summary["record_type"], "practice")
        self.assertEqual(summary["record_id"], "practice-1")
        self.assertEqual(summary["record_name"], "Example Internal Medicine")
        self.assertEqual(summary["issue_count"], len(summary["issues"]))

    def test_summary_document_ids_are_namespaced_by_record_type(self):
        self.assertEqual(summary_document_id("provider", "abc"), "provider-abc")
        self.assertEqual(summary_document_id("practice", "abc"), "practice-abc")


if __name__ == "__main__":
    unittest.main()
