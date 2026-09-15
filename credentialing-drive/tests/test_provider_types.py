import unittest

from app.provider_types import normalize_provider_type, provider_type_values_conflict


class ProviderTypeTests(unittest.TestCase):
    def test_specific_designations_are_normalized(self):
        self.assertEqual(normalize_provider_type("M.D."), "MD")
        self.assertEqual(normalize_provider_type("physician assistant"), "PA")

    def test_broad_roles_are_not_provider_types(self):
        self.assertIsNone(normalize_provider_type("Physician and Surgeon"))
        self.assertIsNone(normalize_provider_type("Individual practitioner"))

    def test_only_distinct_specific_designations_conflict(self):
        self.assertFalse(provider_type_values_conflict("MD", "Physician"))
        self.assertFalse(provider_type_values_conflict("M.D.", "Medical Doctor"))
        self.assertTrue(provider_type_values_conflict("MD", "DO"))


if __name__ == "__main__":
    unittest.main()
