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

        reconciled, summaries, skipped_categories = validate_reconciliation(
            self.candidates, response
        )

        self.assertEqual(reconciled["education"], [self.candidates["education"][1]])
        self.assertEqual(summaries[0]["after_count"], 1)
        self.assertEqual(skipped_categories, [])

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

        reconciled, summaries, skipped_categories = validate_reconciliation(
            self.candidates, response
        )

        self.assertEqual(reconciled["education"], self.candidates["education"])
        self.assertEqual(summaries[0]["decisions"][0]["decision"], "needs_review")
        self.assertEqual(skipped_categories, [])

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

        reconciled, summaries, skipped_categories = validate_reconciliation(
            self.candidates, response
        )

        self.assertEqual(reconciled, {})
        self.assertEqual(summaries, [])
        self.assertEqual(skipped_categories[0]["category"], "education")
        self.assertIn("unsupported", skipped_categories[0]["reason"])

    def test_preserves_valid_categories_when_another_category_is_unsafe(self):
        candidates = {
            **self.candidates,
            "expiration_dates": ["January 1, 2027", {"date": "January 1, 2027"}],
        }
        response = {
            "categories": {
                "education": [
                    {
                        "source_record_indices": [0, 1],
                        "decision": "merge",
                        "canonical_record": self.candidates["education"][1],
                        "confidence": 0.98,
                        "reason": "Same residency.",
                    }
                ],
                "expiration_dates": [
                    {
                        "source_record_indices": [0, 1],
                        "decision": "merge",
                        "canonical_record": "2027-01-01",
                        "confidence": 0.97,
                        "reason": "Unsafe reformatted date.",
                    }
                ],
            }
        }

        reconciled, summaries, skipped_categories = validate_reconciliation(candidates, response)

        self.assertIn("education", reconciled)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(skipped_categories[0]["category"], "expiration_dates")
