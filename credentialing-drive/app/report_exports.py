"""Dependency-free CSV rendering for report responses."""

import csv
import io
from datetime import datetime, timezone


def value_text(value, empty=""):
    if value in (None, "", [], {}):
        return empty
    if isinstance(value, list):
        return "; ".join(value_text(item) for item in value if item not in (None, "")) or empty
    if isinstance(value, dict):
        return "; ".join(f"{key}: {value_text(item)}" for key, item in value.items())
    return str(value)


def safe_csv_value(value):
    value = value_text(value)
    # Prevent spreadsheet programs from interpreting client-provided values as formulas.
    return f"'{value}" if value.startswith(("=", "+", "-", "@")) else value


def report_csv(report):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    columns = report["columns"]
    writer.writerow([column["label"] for column in columns])
    for row in report["rows"]:
        writer.writerow([safe_csv_value(row.get(column["key"])) for column in columns])
    return output.getvalue()


def report_filename(report_type):
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"credentialing-{report_type.replace('_', '-')}-{generated_at}.csv"
