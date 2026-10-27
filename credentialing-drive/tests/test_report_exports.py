import csv
import io
import unittest

from app.report_exports import report_csv


class ReportExportTests(unittest.TestCase):
    def test_csv_uses_labeled_columns_and_report_row_values(self):
        report = {
            "columns": [
                {"key": "provider_name", "label": "Provider"},
                {"key": "npi", "label": "NPI"},
            ],
            "rows": [
                {"provider_name": "Avery Rowan", "npi": "9001001004"},
                {"provider_name": "=Formula attempt", "npi": None},
            ],
        }

        rows = list(csv.reader(io.StringIO(report_csv(report))))

        self.assertEqual(rows[0], ["Provider", "NPI"])
        self.assertEqual(rows[1], ["Avery Rowan", "9001001004"])
        self.assertEqual(rows[2], ["'=Formula attempt", ""])
