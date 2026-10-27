"""Copy the old single-client Google Drive connection into one tenant scope."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.connections import migrate_legacy_connection  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entity_id", help="Firestore client entity ID, for example dummy-client")
    args = parser.parse_args()
    print(migrate_legacy_connection(args.entity_id))


if __name__ == "__main__":
    main()
