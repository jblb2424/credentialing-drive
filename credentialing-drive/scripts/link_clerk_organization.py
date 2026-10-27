"""Associate a Clerk Organization with an existing credentialing client."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import link_clerk_organization  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entity_id", help="Firestore client entity ID, for example dummy-client")
    parser.add_argument("clerk_organization_id", help="Clerk Organization ID, for example org_...")
    args = parser.parse_args()

    link_clerk_organization(args.entity_id, args.clerk_organization_id)
    print(f"Linked Clerk organization {args.clerk_organization_id} to client {args.entity_id}")


if __name__ == "__main__":
    main()
