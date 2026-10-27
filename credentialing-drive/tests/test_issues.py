import unittest
from datetime import date, datetime, timedelta, timezone

from app.issues import (
    calculate_group_issues,
    calculate_provider_issues,
    group_missing_field_display_label,
)


class FakeSnapshot:
    def __init__(self, revision_id, data):
        self.id = revision_id
        self._data = data

    def to_dict(self):
        return self._data


class FakeRevisionCollection:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.stream_calls = 0

    def stream(self):
        self.stream_calls += 1
        return iter(self.snapshots)


class FakeProviderReference:
    def __init__(self, snapshots):
        self.revisions = FakeRevisionCollection(snapshots)

    def collection(self, name):
        self.last_collection = name
        return self.revisions


class ProviderIssueTests(unittest.TestCase):
    def test_calculation_streams_revisions_once_for_all_issue_types(self):
        expiration_date = (date.today() + timedelta(days=30)).isoformat()
        revisions = [
            FakeSnapshot(
                "older",
                {
                    "file_name": "license.pdf",
                    "recorded_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                    "changes": {
                        "provider": {"npi": {"previous": "111", "current": "222"}},
                        "licenses": {
                            "added": [
                                {
                                    "type": "State medical license",
                                    "license_number": "PA-1",
                                    "expiration_date": expiration_date,
                                }
                            ]
                        },
                    },
                },
            ),
            FakeSnapshot(
                "newer",
                {
                    "file_name": "profile.pdf",
                    "recorded_at": datetime(2026, 2, 1, tzinfo=timezone.utc),
                    "changes": {},
                },
            ),
        ]
        provider_ref = FakeProviderReference(revisions)
        provider = {
            "provider": {"name": "Avery Rowan", "npi": "222"},
            "licenses": [
                {
                    "type": "State medical license",
                    "license_number": "PA-1",
                    "expiration_date": expiration_date,
                }
            ],
        }

        issues = calculate_provider_issues(provider_ref, provider)

        self.assertEqual(provider_ref.revisions.stream_calls, 1)
        self.assertTrue({"discrepancy", "expiring"}.issubset({issue["type"] for issue in issues}))
        discrepancy = next(issue for issue in issues if issue["type"] == "discrepancy")
        self.assertEqual(discrepancy["severity"], "high")
        expiration = next(issue for issue in issues if issue["type"] == "expiring")
        self.assertEqual(expiration["file_name"], "license.pdf")

    def test_name_casing_changes_do_not_create_discrepancies(self):
        revisions = [
            FakeSnapshot(
                "name-casing-only",
                {
                    "file_name": "credential.pdf",
                    "recorded_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                    "changes": {
                        "provider": {
                            "first_name": {"previous": "Avery", "current": "AVERY"},
                            "last_name": {"previous": "Rowan", "current": "ROWAN"},
                        }
                    },
                },
            )
        ]

        issues = calculate_provider_issues(
            FakeProviderReference(revisions), {"provider": {"name": "Avery Rowan"}}
        )

        self.assertFalse([issue for issue in issues if issue["type"] == "discrepancy"])

    def test_priority_tiers_mark_blocking_issues_critical(self):
        provider = {
            "provider": {"name": "Avery Rowan"},
            "licenses": [{
                "type": "State medical license",
                "expiration_date": "2020-01-01",
            }],
        }

        issues = calculate_provider_issues(FakeProviderReference([]), provider)
        issues_by_field = {
            issue["affected_fields"][0]: issue
            for issue in issues
            if issue["type"] == "missing_data"
        }
        expired = next(issue for issue in issues if issue["type"] == "expired")

        self.assertEqual(expired["severity"], "critical")
        self.assertEqual(issues_by_field["provider.npi"]["severity"], "critical")
        self.assertEqual(issues_by_field["liability_insurance"]["severity"], "critical")
        self.assertEqual(issues_by_field["provider.gender"]["severity"], "medium")

        missing_license_issues = calculate_provider_issues(
            FakeProviderReference([]),
            {"provider": {"name": "Avery Rowan"}},
        )
        missing_license = next(
            issue
            for issue in missing_license_issues
            if issue.get("affected_fields") == ["licenses"]
        )
        self.assertEqual(missing_license["severity"], "critical")

    def test_missing_data_covers_required_profile_and_record_fields(self):
        provider_ref = FakeProviderReference([])
        provider = {
            "provider": {
                "name": "Avery Rowan",
                "first_name": "Avery",
                "last_name": "Rowan",
                "provider_type": "MD",
                "credentials": "MD",
                "gender": "Female",
                "date_of_birth": "1980-01-01",
                "npi": "1234567890",
                "caqh_id": "12345",
                "address": "1 Main Street",
            },
            "provider_locations": [{
                "display_name": "Main Office",
                "type": "primary",
                "address": "1 Main Street",
                "phone": "555-0100",
                "email": "office@example.com",
                "practice_hours": "9-5",
                "faxes": "555-0101",
            }],
            "licenses": [{
                "type": "State medical license",
                "license_number": "PA-1",
                "issue_date": "2020-01-01",
                "expiration_date": "2027-01-01",
                "state": "PA",
            }],
            "specialties": [{"name": "Internal Medicine", "board_certified": False}],
            "education": [{
                "education_type": "medical_school",
                "institution_name": "Example University",
                "degree": "MD",
                "specialty": "Medicine",
                "start_date": "2000-08-01",
                "end_date": "2004-05-01",
            }],
            "liability_insurance": [{
                "insurance_type": "malpractice",
                "carrier_name": "Example Carrier",
                "policy_number": "POL-1",
                "effective_date": "2026-01-01",
                "expiration_date": "2027-01-01",
                "claim_amount": "1000000",
                "aggregate_amount": "3000000",
            }],
            "payer_enrollments": [{
                "payer_name": "Example Payer",
                "group_name": "Example Practice",
                "location_names": ["Main Office"],
                "enrollment_status": "enrollment_complete",
            }],
        }

        issues = calculate_provider_issues(provider_ref, provider)

        self.assertFalse([issue for issue in issues if issue["type"] == "missing_data"])

        provider["licenses"][0].pop("state")
        provider["specialties"][0]["board_certified"] = True
        issues = calculate_provider_issues(provider_ref, provider)
        missing_fields = {
            issue["affected_fields"][0]
            for issue in issues
            if issue["type"] == "missing_data"
        }
        self.assertIn("licenses[0].state", missing_fields)
        self.assertIn("specialties[0].certification_date", missing_fields)
        self.assertIn("specialties[0].expiration_date", missing_fields)
        self.assertIn("specialties[0].certifying_board", missing_fields)
        issue_by_field = {
            issue["affected_fields"][0]: issue
            for issue in issues
            if issue["type"] == "missing_data"
        }
        self.assertEqual(issue_by_field["licenses[0].state"]["display_label"], "state for State medical license")

    def test_missing_data_has_no_source_provenance(self):
        provider_ref = FakeProviderReference([
            FakeSnapshot(
                "latest",
                {
                    "file_name": "credentialing-form.pdf",
                    "drive_file_id": "drive-file-id",
                    "recorded_at": datetime(2026, 2, 1, tzinfo=timezone.utc),
                    "changes": {},
                },
            ),
        ])

        issues = calculate_provider_issues(provider_ref, {"provider": {"name": "Avery Rowan"}})
        missing_issues = [issue for issue in issues if issue["type"] == "missing_data"]

        self.assertTrue(missing_issues)
        self.assertTrue(all("file_name" not in issue for issue in missing_issues))
        self.assertTrue(all("drive_file_id" not in issue for issue in missing_issues))

    def test_manual_correction_does_not_create_a_document_discrepancy(self):
        provider_ref = FakeProviderReference([
            FakeSnapshot(
                "manual-correction",
                {
                    "file_name": "Manual update",
                    "source_type": "manual",
                    "recorded_at": datetime(2026, 2, 1, tzinfo=timezone.utc),
                    "changes": {"provider": {"npi": {"previous": "1111111111", "current": "2222222222"}}},
                },
            ),
        ])

        issues = calculate_provider_issues(provider_ref, {"provider": {"name": "Avery Rowan", "npi": "2222222222"}})

        self.assertFalse([issue for issue in issues if issue["type"] == "discrepancy"])


class GroupIssueTests(unittest.TestCase):
    def test_group_issues_detect_discrepancies_and_missing_fields(self):
        group_ref = FakeProviderReference([
            FakeSnapshot(
                "group-change",
                {
                    "file_name": "updated-w9.pdf",
                    "recorded_at": datetime(2026, 2, 1, tzinfo=timezone.utc),
                    "changes": {
                        "group": {
                            "tax_id": {"previous": "11-1111111", "current": "22-2222222"},
                        },
                    },
                    "previous_files": {
                        "group.tax_id": {"file_name": "original-w9.pdf"},
                    },
                },
            ),
        ])
        group = {
            "legal_name": "Example Practice",
            "type_2_npi": "1234567890",
            "tax_id": "22-2222222",
            "locations": [{
                "display_name": "Main Office",
                "type": "primary",
                "address": "1 Main Street",
            }],
        }

        issues = calculate_group_issues(group_ref, group)

        self.assertEqual(group_ref.revisions.stream_calls, 1)
        discrepancy = next(issue for issue in issues if issue["type"] == "discrepancy")
        self.assertEqual(discrepancy["affected_fields"], ["group.tax_id"])
        self.assertEqual(discrepancy["severity"], "high")
        self.assertEqual(discrepancy["previous_file_name"], "original-w9.pdf")
        missing_fields = {
            issue["affected_fields"][0]
            for issue in issues
            if issue["type"] == "missing_data"
        }
        self.assertIn("locations[0].phone", missing_fields)
        self.assertIn("locations[0].email", missing_fields)

    def test_group_type_2_npi_is_critical_but_other_missing_group_data_is_medium(self):
        issues = calculate_group_issues(
            FakeProviderReference([]),
            {"legal_name": "Example Practice", "locations": []},
        )
        issues_by_field = {
            issue["affected_fields"][0]: issue
            for issue in issues
            if issue["type"] == "missing_data"
        }

        self.assertEqual(issues_by_field["group.type_2_npi"]["severity"], "critical")
        self.assertEqual(issues_by_field["group.tax_id"]["severity"], "medium")

    def test_group_location_labels_handle_legacy_address_objects(self):
        label = group_missing_field_display_label(
            "locations[0].phone",
            {"locations": [{"display_name": {
                "address": "100 Stewart Avenue",
                "city": "Garden City",
                "state": "NY",
            }}]},
        )

        self.assertEqual(label, "phone for 100 Stewart Avenue, Garden City, NY")


if __name__ == "__main__":
    unittest.main()
