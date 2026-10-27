"""Normalize legacy credentialing dates for a single workspace."""

import argparse
import json
import sys
from pathlib import Path

# Let the utility run directly from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DEFAULT_ENTITY_ID
from app.providers import repair_entity_date_storage


def main():
    parser = argparse.ArgumentParser(
        description="Normalize recognized credentialing dates to canonical ISO storage."
    )
    parser.add_argument("--entity", default=DEFAULT_ENTITY_ID, help="Client workspace ID")
    args = parser.parse_args()
    print(json.dumps(repair_entity_date_storage(args.entity), sort_keys=True))


if __name__ == "__main__":
    main()
