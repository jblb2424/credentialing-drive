import unittest

from app.provider_names import (
    enrich_provider_name, name_values_equivalent, normalize_person_name,
    person_name_values_equivalent,
)


class ProviderNameTests(unittest.TestCase):
    def test_credential_suffix_is_not_part_of_name(self):
        profile = enrich_provider_name({"name": "Avery Rowan, MD"})

        self.assertEqual(profile["name"], "Avery Rowan")
        self.assertEqual(profile["first_name"], "Avery")
        self.assertEqual(profile["last_name"], "Rowan")
        self.assertEqual(profile["credentials"], "MD")

    def test_uppercase_name_is_normalized_for_display_and_storage(self):
        profile = enrich_provider_name({"name": "AVERY ROWAN", "first_name": "AVERY", "last_name": "ROWAN"})

        self.assertEqual(profile["name"], "Avery Rowan")
        self.assertEqual(profile["first_name"], "Avery")
        self.assertEqual(profile["last_name"], "Rowan")

    def test_name_normalizer_preserves_common_suffixes_and_initials(self):
        self.assertEqual(normalize_person_name("avery j. rowan iii"), "Avery J. Rowan III")

    def test_name_value_comparison_ignores_capitalization_only(self):
        self.assertTrue(person_name_values_equivalent("AVERY", "Avery"))
        self.assertFalse(person_name_values_equivalent("Avery", "Audrey"))

    def test_name_variants_with_missing_middle_name_are_equivalent(self):
        self.assertTrue(name_values_equivalent("Avery Jordan Rowan", "Avery Rowan, MD"))
        self.assertTrue(name_values_equivalent("Rowan, Avery Jordan", "Avery Rowan"))

    def test_distinct_middle_names_remain_a_discrepancy(self):
        self.assertFalse(name_values_equivalent("Avery Jordan Rowan", "Avery James Rowan"))

    def test_distinct_people_remain_a_discrepancy(self):
        self.assertFalse(name_values_equivalent("Avery Rowan", "Audrey Rowan"))
