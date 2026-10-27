import unittest

from app.providers import merge_license_records, normalize_provider_data


class LicenseMergingTests(unittest.TestCase):
    def test_same_type_and_number_fills_missing_credential_fields(self):
        licenses = merge_license_records(
            [{"type": "State Medical License", "license_number": "PA-123", "state": "PA"}],
            [{"type": "state medical license", "license_number": "pa 123", "issue_date": "01 JAN 2026"}],
        )

        self.assertEqual(len(licenses), 1)
        self.assertEqual(licenses[0]["state"], "PA")
        self.assertEqual(licenses[0]["issue_date"], "2026-01-01")

    def test_same_identity_does_not_replace_a_conflicting_supported_value(self):
        licenses = merge_license_records(
            [{"type": "State Medical License", "license_number": "PA-123", "state": "PA"}],
            [{"type": "State Medical License", "license_number": "PA-123", "state": "NJ"}],
        )

        self.assertEqual(len(licenses), 1)
        self.assertEqual(licenses[0]["state"], "PA")

    def test_unmatched_credentials_remain_separate(self):
        licenses = merge_license_records(
            [{"type": "State Medical License", "license_number": "PA-123"}],
            [{"type": "DEA Registration", "license_number": "PA-123"}],
        )

        self.assertEqual(len(licenses), 2)

    def test_new_ingestion_normalizes_credential_dates(self):
        normalized = normalize_provider_data({
            "provider": {"first_name": "AVERY", "last_name": "ROWAN"},
            "licenses": [{
                "type": "State Medical License",
                "license_number": "PA-123",
                "issue_date": "01 JAN 2026",
                "expiration_date": "31 DEC 2026",
            }],
        })

        self.assertEqual(normalized["licenses"][0]["issue_date"], "2026-01-01")
        self.assertEqual(normalized["licenses"][0]["expiration_date"], "2026-12-31")
