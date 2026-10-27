"""Build current issue summaries for an existing client workspace."""

import argparse
import sys
from pathlib import Path

# Let the utility run directly from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DEFAULT_ENTITY_ID
from app.connections import get_firestore_client
from app.issue_summaries import refresh_entity_issue_summaries
from app.providers import get_entity_ref


def main():
    parser = argparse.ArgumentParser(
        description="Create cache-backed issue summaries for a client workspace."
    )
    parser.add_argument("--entity", default=DEFAULT_ENTITY_ID, help="Client workspace ID")
    args = parser.parse_args()

    entity_ref = get_entity_ref(get_firestore_client(), args.entity)
    refreshed = refresh_entity_issue_summaries(entity_ref)
    print(f"Refreshed {refreshed} issue summaries for {args.entity}.")


if __name__ == "__main__":
    main()
