import unittest

from app.facts import factual_extraction


class FactsAndVerificationSourcesTests(unittest.TestCase):
    def test_verification_source_cannot_create_a_fact_record(self):
        extraction = {
            "provider": {
                "first_name": "Avery",
                "last_name": "Rowan",
                "npi": "9001001004",
            },
            "education": [
                {
                    "education_type": "Residency",
                    "institution_name": "Meridian Academic Medical Center",
                    "specialty": "Internal Medicine",
                }
            ],
            "verification_sources": [
                {
                    "name": "Synthetic GME office",
                    "role": "training verifier",
                    "supports_fact_domains": ["education"],
                    "notes": "Confirmation contact only",
                },
                {
                    "name": "Pennsylvania DMV",
                    "role": "identity document issuer",
                    "supports_fact_domains": ["provider"],
                    "notes": "Driver license issuer",
                },
            ],
        }

        normalized = factual_extraction(extraction)

        self.assertEqual(normalized["education"], extraction["education"])
        self.assertNotIn("verification_sources", normalized)
        self.assertNotIn("Synthetic GME office", str(normalized))
        self.assertNotIn("Pennsylvania DMV", str(normalized))
