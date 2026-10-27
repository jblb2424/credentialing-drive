"""Server-side Clerk Backend API client for tenant member administration."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from fastapi import HTTPException


CLERK_API_BASE = "https://api.clerk.com/v1"
MANAGEABLE_ROLES = {
    "customer_admin": "org:customer_admin",
    "manager": "org:manager",
    "credentialing_specialist": "org:credentialing_specialist",
    "client": "org:client",
}


def clerk_request(method, path, payload=None):
    secret_key = os.environ.get("CLERK_SECRET_KEY")
    if not secret_key:
        raise HTTPException(status_code=503, detail="Clerk member administration is not configured")

    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{CLERK_API_BASE}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Clerk's edge protection rejects urllib's default browser signature.
            "User-Agent": "CredentialingDrive/1.0",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8")
        raise HTTPException(status_code=exc.code, detail="Clerk member administration request failed") from exc
    except URLError as exc:
        raise HTTPException(status_code=503, detail="Clerk member administration is unavailable") from exc
    return json.loads(raw) if raw else {}


def organization_memberships(organization_id):
    memberships = []
    offset = 0
    while True:
        response = clerk_request(
            "GET",
            f"/organizations/{quote(organization_id, safe='')}/memberships?limit=100&offset={offset}",
        )
        page = response.get("data", [])
        memberships.extend(page)
        if not page or len(memberships) >= response.get("total_count", 0):
            return memberships
        offset += len(page)


def invitation_redirect_url():
    """Return the configured, server-controlled organization invite destination."""
    app_base_url = os.environ.get("APP_BASE_URL", "http://127.0.0.1:8080").strip().rstrip("/")
    parsed = urlsplit(app_base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise HTTPException(status_code=503, detail="The invitation redirect URL is not configured")
    return f"{app_base_url}/accept-invitation"


def invite_organization_member(organization_id, email, role, redirect_url=None):
    clerk_role = MANAGEABLE_ROLES.get(role)
    if not clerk_role:
        raise HTTPException(status_code=400, detail="Choose a valid organization role")
    payload = {"email_address": email, "role": clerk_role}
    if redirect_url:
        payload["redirect_url"] = redirect_url
    return clerk_request(
        "POST",
        f"/organizations/{quote(organization_id, safe='')}/invitations",
        payload,
    )


def update_organization_member_role(organization_id, user_id, role):
    clerk_role = MANAGEABLE_ROLES.get(role)
    if not clerk_role:
        raise HTTPException(status_code=400, detail="Choose a valid organization role")
    return clerk_request(
        "PATCH",
        f"/organizations/{quote(organization_id, safe='')}/memberships/{quote(user_id, safe='')}",
        {"role": clerk_role},
    )


def remove_organization_member(organization_id, user_id):
    return clerk_request(
        "DELETE",
        f"/organizations/{quote(organization_id, safe='')}/memberships/{quote(user_id, safe='')}",
    )


def serialize_membership(membership):
    user = membership.get("public_user_data") or {}
    first_name = user.get("first_name") or ""
    last_name = user.get("last_name") or ""
    return {
        "id": user.get("user_id"),
        "display_name": " ".join(part for part in (first_name, last_name) if part) or user.get("identifier"),
        "email": user.get("identifier"),
        "role": (membership.get("role") or "").removeprefix("org:"),
        "clerk_role": membership.get("role"),
        "joined_at": membership.get("created_at"),
    }
