import unittest

from app.prompts import load_prompt_config, prompt_template, render_prompt


class PromptConfigTests(unittest.TestCase):
    def test_all_prompt_entries_are_documented(self):
        for prompt_group in ("ingestion", "validation"):
            config = load_prompt_config(prompt_group)
            self.assertTrue(config["description"])
            self.assertTrue(config["prompts"])
            for prompt in config["prompts"].values():
                self.assertTrue(prompt["description"])
                self.assertTrue(prompt["prompt"])

    def test_document_extraction_assembles_shared_policies(self):
        prompt = render_prompt(
            "ingestion",
            "document_extraction",
            date_normalization_policy=prompt_template("ingestion", "date_normalization_policy"),
            facts_and_verification_sources_policy=prompt_template(
                "ingestion", "facts_and_verification_sources_policy"
            ),
        )

        self.assertIn("For every date field", prompt)
        self.assertIn("Facts versus verification sources", prompt)
        self.assertNotIn("{{ date_normalization_policy }}", prompt)

    def test_category_prompt_renders_runtime_values(self):
        prompt = render_prompt(
            "ingestion",
            "document_category_classification",
            document_categories="license, other",
            file_name="'license.pdf'",
        )

        self.assertIn("license, other", prompt)
        self.assertIn("'license.pdf'", prompt)

    def test_missing_template_value_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "missing variables: document_categories, file_name"):
            render_prompt("ingestion", "document_category_classification")


if __name__ == "__main__":
    unittest.main()
