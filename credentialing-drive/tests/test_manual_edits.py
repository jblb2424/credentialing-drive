import unittest

from fastapi import HTTPException

from app.providers import manual_group_changes, manual_provider_changes, manual_provider_updates


class ManualEditTests(unittest.TestCase):
    def test_provider_manual_edit_records_prior_value(self):
        updated, changes = manual_provider_changes(
            {"first_name": "Avery", "last_name": "Rowan", "npi": "9001001004"},
            {"npi": "1234567893", "gender": "Female"},
        )
        self.assertEqual(updated["npi"], "1234567893")
        self.assertEqual(
            changes["provider"]["npi"],
            {"current": "1234567893", "previous": "9001001004"},
        )
        self.assertEqual(
            changes["provider"]["gender"],
            {"current": "Female", "previous": None},
        )

    def test_provider_manual_edit_normalizes_person_name_case(self):
        updated, changes = manual_provider_changes(
            {"first_name": "Avery", "last_name": "Rowan"},
            {"first_name": "AVERY", "last_name": "ROWAN"},
        )

        self.assertEqual(updated["first_name"], "Avery")
        self.assertEqual(updated["last_name"], "Rowan")
        self.assertEqual(changes, {})

    def test_group_manual_edit_normalizes_legacy_tax_id(self):
        updates, changes = manual_group_changes(
            {"legal_name": "Carter Group", "tax_id_number": "12-3456789"},
            {"tax_id": "98-7654321"},
        )
        self.assertEqual(updates["tax_id"], "98-7654321")
        self.assertIn("tax_id_number", updates)
        self.assertEqual(changes["group"]["tax_id"]["previous"], "12-3456789")

    def test_manual_edits_reject_unknown_fields_and_invalid_npis(self):
        with self.assertRaises(HTTPException):
            manual_provider_changes({}, {"licenses": "not supported"})
        with self.assertRaises(HTTPException):
            manual_group_changes({}, {"type_2_npi": "123"})

    def test_provider_collection_updates_are_limited_to_known_collections(self):
        updates, changes = manual_provider_updates(
            {"licenses": [{"type": "Old license"}]},
            {"licenses": [{"type": "Medical license", "license_number": "PA-123"}]},
        )
        self.assertEqual(updates["licenses"][0]["license_number"], "PA-123")
        self.assertEqual(changes["licenses"]["previous"], [{"type": "Old license"}])
        with self.assertRaises(HTTPException):
            manual_provider_updates({}, {"unknown_collection": []})
