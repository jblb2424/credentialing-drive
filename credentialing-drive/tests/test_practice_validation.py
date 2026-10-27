import unittest

from app.practice_validation import (
    canonicalize_practice_candidate,
    group_identity_keys,
    practice_candidate_decision,
)


class PracticeValidationTests(unittest.TestCase):
    def test_individual_billing_entity_is_not_promoted_to_a_practice(self):
        decision = practice_candidate_decision({
            "provider": {"name": "Avery Rowan, MD"},
            "group": {
                "legal_name": "Avery Rowan (test-only)",
                "source_role": "billing_entity",
                "entity_kind": "individual",
            },
        })

        self.assertEqual(decision["action"], "skip")
        self.assertEqual(decision["reason"], "matches_provider_identity")

    def test_provider_name_is_not_promoted_even_when_role_is_incorrect(self):
        decision = practice_candidate_decision({
            "provider": {"first_name": "Avery", "last_name": "Rowan"},
            "group": {
                "legal_name": "Avery Rowan",
                "source_role": "primary_practice",
                "entity_kind": "organization",
            },
        })

        self.assertEqual(decision["action"], "skip")
        self.assertEqual(decision["reason"], "matches_provider_identity")

    def test_supported_affiliation_creates_a_practice(self):
        decision = practice_candidate_decision({
            "provider": {"name": "Avery Rowan"},
            "group": {
                "legal_name": "Northstar Community Clinic",
                "source_role": "primary_practice",
                "entity_kind": "organization",
            },
        })

        self.assertEqual(decision["action"], "accept")

    def test_ambiguous_name_only_candidate_is_not_persisted(self):
        decision = practice_candidate_decision({
            "provider": {"name": "Avery Rowan"},
            "group": {"legal_name": "Northstar Community Clinic"},
        })

        self.assertEqual(decision["action"], "skip")
        self.assertEqual(decision["reason"], "ambiguous_name_only_candidate")

    def test_placeholder_tax_id_does_not_promote_ambiguous_candidate(self):
        decision = practice_candidate_decision({
            "provider": {"name": "Avery Rowan"},
            "group": {
                "legal_name": "Avery Rowan Test Profile",
                "tax_id": "000-00-0000",
            },
        })

        self.assertEqual(decision["action"], "skip")
        self.assertEqual(decision["reason"], "ambiguous_name_only_candidate")

    def test_placeholder_tax_id_is_not_an_identity_key(self):
        keys = group_identity_keys({
            "legal_name": "Northstar Community Clinic",
            "tax_id": "000-00-0000",
        })

        self.assertEqual(keys, ["northstar-community-clinic"])

    def test_fixture_alias_uses_one_canonical_identity(self):
        normalized = canonicalize_practice_candidate({
            "group": {
                "legal_name": "Northstar Community Clinic - fictional",
                "source_role": "primary_practice",
                "entity_kind": "organization",
            },
        })

        self.assertEqual(normalized["group"]["legal_name"], "Northstar Community Clinic")
        self.assertEqual(normalized["group"]["aliases"], ["Northstar Community Clinic - fictional"])
        self.assertEqual(group_identity_keys(normalized["group"]), ["northstar-community-clinic"])
