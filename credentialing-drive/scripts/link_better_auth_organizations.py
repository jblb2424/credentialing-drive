"""Link returned Better Auth organization IDs to Firestore client entities."""

import json
import sys
from pathlib import Path

from app.config import ENTITY_COLLECTION
from app.connections import get_firestore_client


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/link_better_auth_organizations.py <organization-mapping.json>")
    mappings = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if not isinstance(mappings, list):
        raise SystemExit("Mapping input must be a JSON array")

    client = get_firestore_client()
    for mapping in mappings:
        entity_id = mapping.get("entity_id")
        organization_id = mapping.get("better_auth_organization_id")
        if not entity_id or not organization_id:
            raise SystemExit("Every mapping needs entity_id and better_auth_organization_id")
        reference = client.collection(ENTITY_COLLECTION).document(entity_id)
        if not reference.get().exists:
            raise SystemExit(f"Unknown client entity: {entity_id}")
        reference.set({"better_auth_organization_id": organization_id}, merge=True)
        print(f"Linked {entity_id} to {organization_id}")


if __name__ == "__main__":
    main()
