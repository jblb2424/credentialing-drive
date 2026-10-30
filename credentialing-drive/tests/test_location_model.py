import unittest

from app.providers import location_identity_key, normalize_provider_data


class SharedLocationModelTests(unittest.TestCase):
    def test_location_identity_is_stable_for_equivalent_location_facts(self):
        first = location_identity_key({
            "display_name": "Northstar Community Clinic",
            "address": "123 Example Street, Northstar, PA 00000",
        })
        second = location_identity_key({
            "display_name": "NORTHSTAR community-clinic",
            "address": "123 example street northstar pa 00000",
        })

        self.assertEqual(first, second)

    def test_legacy_provider_locations_normalize_into_unified_locations(self):
        normalized = normalize_provider_data({
            "provider": {"name": "Avery Rowan", "npi": "1234567893"},
            "locations": [{"display_name": "Main Clinic"}],
            "provider_locations": [{"display_name": "Satellite Clinic"}],
        })

        self.assertEqual(
            [location["display_name"] for location in normalized["locations"]],
            ["Main Clinic", "Satellite Clinic"],
        )
        self.assertNotIn("provider_locations", normalized)


if __name__ == "__main__":
    unittest.main()
