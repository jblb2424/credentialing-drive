"""Export Firestore client entities as Better Auth organization bootstrap input.

This script is intentionally read-only. It does not fabricate organization IDs
or copy Clerk credentials; create organizations through Better Auth first and
link their returned IDs with the companion linking script.
"""

import json
import re
from pathlib import Path

from app.config import ENTITY_COLLECTION
from app.connections import get_firestore_client


def slugify(value):
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "client"


def main():
    organizations = []
    for document in get_firestore_client().collection(ENTITY_COLLECTION).stream():
        entity = document.to_dict() or {}
        name = entity.get("name") or document.id.replace("-", " ").title()
        organizations.append({
            "entity_id": document.id,
            "name": name,
            "slug": slugify(f"{name}-{document.id}"),
        })
    destination = Path("better-auth-organizations.json")
    destination.write_text(json.dumps(organizations, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(organizations)} organization definitions to {destination}")


if __name__ == "__main__":
    main()
