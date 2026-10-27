import unittest
from datetime import date, datetime

from app.dates import normalize_date_fields, normalize_date_value, parse_complete_date
from app.issues import parse_expiration_date


class DateNormalizationTests(unittest.TestCase):
    def test_normalizes_common_complete_source_formats_to_iso(self):
        values = (
            "01 JAN 2026",
            "31 DEC 2026",
            "01 January 2026",
            "December 31, 2026",
            "01/31/2026",
            "2026/01/31",
        )

        self.assertEqual(
            [normalize_date_value(value) for value in values],
            ["2026-01-01", "2026-12-31", "2026-01-01", "2026-12-31", "2026-01-31", "2026-01-31"],
        )

    def test_preserves_partial_date_precision(self):
        self.assertEqual(normalize_date_value("January 2026"), "2026-01")
        self.assertEqual(normalize_date_value("2026"), "2026")
        self.assertIsNone(parse_complete_date("January 2026"))

    def test_normalizes_nested_recognized_date_fields(self):
        normalized = normalize_date_fields({
            "provider": {"date_of_birth": "01 JAN 1980"},
            "licenses": [{"issue_date": "01 January 2026", "expiration_date": "31 DEC 2026"}],
            "expiration_dates": ["December 31, 2026"],
            "license_number": "01 JAN 2026",
        })

        self.assertEqual(normalized["provider"]["date_of_birth"], "1980-01-01")
        self.assertEqual(normalized["licenses"][0]["issue_date"], "2026-01-01")
        self.assertEqual(normalized["licenses"][0]["expiration_date"], "2026-12-31")
        self.assertEqual(normalized["expiration_dates"], ["2026-12-31"])
        self.assertEqual(normalized["license_number"], "01 JAN 2026")

    def test_issue_calculation_uses_the_shared_parser(self):
        self.assertEqual(parse_expiration_date("31 DEC 2026"), date(2026, 12, 31))
        self.assertEqual(parse_expiration_date("31 December 2026"), date(2026, 12, 31))
        self.assertEqual(parse_expiration_date(datetime(2026, 12, 31, 12, 0)), date(2026, 12, 31))
