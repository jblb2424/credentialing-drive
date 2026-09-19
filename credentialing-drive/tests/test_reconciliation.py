import unittest

from app.reconciliation import validate_reconciliation


class ReconciliationValidationTests(unittest.TestCase):
    def setUp(self):
        self.candidates = {
            "education": [
                {
                    "institution_name": "Meridian Academic Medical Center",
                    "education_type": "Residency",
                    "specialty": "Internal Medicine Residency",
                    "start_date": "2010",
                    "end_date": "2013",
                },
                {
                    "institution_name": "Meridian Academic Medical Center",
                    "education_type": "Residency",
                    "specialty": "Internal Medicine",
                    "start_date": "July 1, 2010",
                    "end_date": "June 30, 2013",
                },
            ]
        }

    def test_accepts_source_supported_model_merge(self):
        response = {
            "categories": {
                "education": [
                    {
                        "source_record_indices": [0, 1],
                        "decision": "merge",
                        "canonical_record": self.candidates["education"][1],
                        "confidence": 0.98,
                        "reason": "Same residency with more precise dates.",
                    }
                ]
            }
        }

        reconciled, summaries = validate_reconciliation(self.candidates, response)

        self.assertEqual(reconciled["education"], [self.candidates["education"][1]])
        self.assertEqual(summaries[0]["after_count"], 1)

    def test_keeps_ambiguous_records_unchanged(self):
        response = {
            "categories": {
                "education": [
                    {
                        "source_record_indices": [0, 1],
                        "decision": "needs_review",
                        "canonical_record": None,
                        "confidence": 0.62,
                        "reason": "Institution names may refer to different organizations.",
                    }
                ]
            }
        }

        reconciled, summaries = validate_reconciliation(self.candidates, response)

        self.assertEqual(reconciled["education"], self.candidates["education"])
        self.assertEqual(summaries[0]["decisions"][0]["decision"], "needs_review")

    def test_rejects_model_values_not_supported_by_sources(self):
        response = {
            "categories": {
                "education": [
                    {
                        "source_record_indices": [0, 1],
                        "decision": "merge",
                        "canonical_record": {
                            "institution_name": "Invented Medical Center",
                            "education_type": "Residency",
                        },
                        "confidence": 0.99,
                        "reason": "Invalid invented value.",
                    }
                ]
            }
        }

        with self.assertRaisesRegex(ValueError, "unsupported"):
            validate_reconciliation(self.candidates, response)
