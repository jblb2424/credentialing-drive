import unittest

from app.issue_resolutions import filter_removed_issues, remove_generated_issue, resolution_document_id


class FakeSnapshot:
    def __init__(self, data):
        self.data = data

    def to_dict(self):
        return self.data


class FakeDocument:
    def __init__(self):
        self.payload = None

    def set(self, payload, merge=False):
        self.payload = {"data": payload, "merge": merge}


class FakeCollection:
    def __init__(self, snapshots=()):
        self.snapshots = snapshots
        self.documents = {}

    def stream(self):
        return iter(self.snapshots)

    def document(self, document_id):
        return self.documents.setdefault(document_id, FakeDocument())


class FakeRecordReference:
    def __init__(self, snapshots=()):
        self.resolutions = FakeCollection(snapshots)

    def collection(self, _name):
        return self.resolutions


class IssueResolutionTests(unittest.TestCase):
    def test_removed_issues_are_filtered_but_unrelated_issues_remain(self):
        record_ref = FakeRecordReference([FakeSnapshot({"issue_id": "missing-npi", "status": "removed"})])
        issues = [{"id": "missing-npi"}, {"id": "missing-license"}]

        self.assertEqual(filter_removed_issues(record_ref, issues), [{"id": "missing-license"}])

    def test_removal_persists_a_safe_reversible_override(self):
        record_ref = FakeRecordReference()
        issue = {"id": "missing-npi", "type": "missing_data", "affected_fields": ["provider.npi"]}

        remove_generated_issue(record_ref, issue, {"uid": "user_123", "display_name": "Alex Admin", "role": "manager"})

        document = record_ref.resolutions.documents[resolution_document_id("missing-npi")]
        self.assertTrue(document.payload["merge"])
        self.assertEqual(document.payload["data"]["status"], "removed")
        self.assertEqual(document.payload["data"]["issue_id"], "missing-npi")
        self.assertEqual(document.payload["data"]["removed_by"]["user_id"], "user_123")
