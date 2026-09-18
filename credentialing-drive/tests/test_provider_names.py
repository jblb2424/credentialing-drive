import unittest

from app.provider_names import enrich_provider_name, name_values_equivalent


class ProviderNameTests(unittest.TestCase):
    def test_credential_suffix_is_not_part_of_name(self):
        profile = enrich_provider_name({"name": "Avery Rowan, MD"})

        self.assertEqual(profile["name"], "Avery Rowan")
        self.assertEqual(profile["first_name"], "Avery")
        self.assertEqual(profile["last_name"], "Rowan")
        self.assertEqual(profile["credentials"], "MD")

    def test_name_variants_with_missing_middle_name_are_equivalent(self):
        self.assertTrue(name_values_equivalent("Avery Jordan Rowan", "Avery Rowan, MD"))
        self.assertTrue(name_values_equivalent("Rowan, Avery Jordan", "Avery Rowan"))

    def test_distinct_middle_names_remain_a_discrepancy(self):
        self.assertFalse(name_values_equivalent("Avery Jordan Rowan", "Avery James Rowan"))

    def test_distinct_people_remain_a_discrepancy(self):
        self.assertFalse(name_values_equivalent("Avery Rowan", "Audrey Rowan"))
