"""Authentication, client tenancy, and role authorization helpers.

Clerk remains available only while existing environments are cut over. New
deployments use Better Auth's short-lived JWTs and its PostgreSQL organization
membership as the authorization source of truth.
"""

import base64
import os

from fastapi import Depends, Request

from app.config import ENTITY_COLLECTION, ENTITY_MEMBER_COLLECTION

USER_COLLECTION = "users"
_BETTER_AUTH_JWK_CLIENTS = {}
ROLES = {
    "super_admin",
    "customer_admin",
    "manager",
    "credentialing_specialist",
    "client",
}

CLERK_ROLE_MAP = {
    # Clerk's built-in organization roles make a useful secure default for a
    # newly provisioned customer before custom roles are configured.
    "org:admin": "customer_admin",
    "org:member": "client",
    "admin": "customer_admin",
    "member": "client",
    "org:customer_admin": "customer_admin",
    "org:manager": "manager",
    "org:credentialing_specialist": "credentialing_specialist",
    "org:client": "client",
    # Accept role keys without Clerk's org: prefix for custom JWT templates.
    "customer_admin": "customer_admin",
    "manager": "manager",
    "credentialing_specialist": "credentialing_specialist",
    "client": "client",
    "super_admin": "super_admin",
}

# Permissions describe application actions rather than Firestore collections so
# roles remain stable as the underlying credentialing data model evolves.
ROLE_PERMISSIONS = {
    "super_admin": {"*"},
    "customer_admin": {
        "activity:read",
        "documents:manage",
        "issues:read",
        "issues:manage",
        "members:manage",
        "records:read",
        "records:write",
        "reports:export",
        "reports:read",
    },
    "manager": {
        "activity:read",
        "issues:read",
        "issues:manage",
        "members:manage",
        "records:read",
        "records:write",
        "reports:export",
        "reports:read",
    },
    "credentialing_specialist": {
        "activity:read",
        "documents:manage",
        "issues:read",
        "issues:manage",
        "records:read",
        "records:write",
        "reports:read",
    },
    "client": {"activity:read", "issues:read", "records:read", "reports:read"},
    "unassigned": set(),
}


def auth_error(status_code, detail):
    # FastAPI is only needed when this helper serves a live HTTP request. Keeping
    # the import here lets role and configuration utilities run in isolation.
    from fastapi import HTTPException

    return HTTPException(status_code=status_code, detail=detail)


def auth_provider():
    """Return the explicitly selected identity provider.

    Keeping the provider explicit prevents a partially configured Better Auth
    deployment from accidentally falling back to Clerk in production.
    """
    provider = os.environ.get("AUTH_PROVIDER", "clerk").strip().lower()
    if provider not in {"clerk", "better_auth"}:
        raise RuntimeError("AUTH_PROVIDER must be either 'clerk' or 'better_auth'")
    return provider


def local_development_user(request):
    """Return a local-only administrator when an explicit bypass is enabled.

    This exists solely to keep the dashboard usable before a developer has
    provisioned a local identity service. It is opt-in and accepts requests
    only from the machine's loopback interface, so it cannot authenticate a
    Cloud Run request even if the environment variable were set accidentally.
    """
    enabled = os.environ.get("LOCAL_DEV_AUTH_BYPASS", "false").lower() == "true"
    client_host = getattr(getattr(request, "client", None), "host", None)
    if not enabled or client_host not in {"127.0.0.1", "::1", "localhost"}:
        return None
    return {
        "uid": "local-development-admin",
        "email": "developer@localhost",
        "name": "Local developer",
        "display_name": "Local developer",
        "role": "super_admin",
        "auth_provider": "local_dev_bypass",
    }


def clerk_authorized_parties():
    return [
        party.strip()
        for party in os.environ.get("CLERK_AUTHORIZED_PARTIES", "").split(",")
        if party.strip()
    ]


def clerk_allows_missing_azp():
    """Allow Clerk development tokens that omit the authorized-party claim.

    Clerk's development instances can issue otherwise valid session tokens
    without an ``azp`` claim. Keep this compatibility mode impossible to enable
    with a live secret so production requests always validate their origin.
    """
    enabled = os.environ.get("CLERK_ALLOW_MISSING_AZP", "false").lower() == "true"
    return enabled and os.environ.get("CLERK_SECRET_KEY", "").startswith("sk_test_")


def clerk_frontend_api_url(publishable_key):
    configured_url = os.environ.get("CLERK_FRONTEND_API_URL", "").rstrip("/")
    if configured_url:
        return configured_url
    try:
        encoded_domain = publishable_key.split("_", 2)[2]
        padded_domain = encoded_domain + "=" * (-len(encoded_domain) % 4)
        # Clerk's publishable key encodes its Frontend API domain with a trailing $.
        domain = base64.urlsafe_b64decode(padded_domain).decode("utf-8").removesuffix("$")
    except (IndexError, UnicodeDecodeError, ValueError):
        return None
    return f"https://{domain}" if domain else None


def clerk_web_config():
    """Return the browser-safe Clerk configuration, or mark auth unconfigured."""
    publishable_key = os.environ.get("CLERK_PUBLISHABLE_KEY")
    frontend_api_url = clerk_frontend_api_url(publishable_key) if publishable_key else None
    if not (
        publishable_key
        and frontend_api_url
        and os.environ.get("CLERK_SECRET_KEY")
        and (clerk_authorized_parties() or clerk_allows_missing_azp())
    ):
        return {"enabled": False}

    return {
        "enabled": True,
        "clerk_config": {
            "publishable_key": publishable_key,
            "frontend_api_url": frontend_api_url,
        },
    }


def better_auth_web_config():
    """Return public Better Auth configuration required by the React app."""
    auth_url = os.environ.get("BETTER_AUTH_URL", "").rstrip("/")
    audience = os.environ.get("BETTER_AUTH_JWT_AUDIENCE", "").strip()
    database_url = os.environ.get("BETTER_AUTH_DATABASE_URL", "").strip()
    if not auth_url or not audience or not database_url:
        return {"enabled": False, "provider": "better_auth"}
    return {
        "enabled": True,
        "provider": "better_auth",
        "better_auth_config": {
            "base_url": auth_url,
            "google_sign_in_enabled": os.environ.get("BETTER_AUTH_GOOGLE_SIGN_IN_ENABLED", "false").lower() == "true",
        },
    }


def web_auth_config():
    """Return configuration for the provider selected in this environment."""
    return better_auth_web_config() if auth_provider() == "better_auth" else clerk_web_config()


def _clerk_authentication_state(request):
    # The official Clerk SDK verifies tokens locally with CLERK_JWT_KEY when it
    # is available and otherwise uses Clerk's cached JWKS fallback.
    try:
        from clerk_backend_api import AuthenticateRequestOptions, authenticate_request
    except ImportError as exc:
        raise auth_error(status_code=503, detail="Clerk Authentication is not installed on this service") from exc

    secret_key = os.environ.get("CLERK_SECRET_KEY")
    authorized_parties = clerk_authorized_parties()
    if not secret_key or (not authorized_parties and not clerk_allows_missing_azp()):
        raise auth_error(status_code=503, detail="Clerk Authentication is not configured")

    options = {
        "secret_key": secret_key,
        "jwt_key": os.environ.get("CLERK_JWT_KEY"),
        "accepts_token": ["session_token"],
    }
    if authorized_parties and not clerk_allows_missing_azp():
        options["authorized_parties"] = authorized_parties

    return authenticate_request(
        request,
        AuthenticateRequestOptions(**options),
    )


def _organization_claims(claims):
    organization = claims.get("o") or {}
    return {
        "id": claims.get("org_id") or organization.get("id"),
        "role": claims.get("org_role") or organization.get("rol"),
        "slug": claims.get("org_slug") or organization.get("slg"),
    }


def user_from_claims(claims):
    """Normalize Clerk session claims into the app's future authorization shape."""
    organization = _organization_claims(claims)
    metadata = claims.get("metadata") or {}
    platform_role = claims.get("platform_role") or metadata.get("platform_role")
    raw_role = platform_role or organization["role"]
    role = CLERK_ROLE_MAP.get(raw_role, "unassigned")
    user_id = claims.get("sub") or claims.get("uid")
    if not user_id:
        raise ValueError("Clerk token did not include a user ID")

    return {
        "uid": user_id,
        "auth_provider": "clerk",
        "email": claims.get("email"),
        "display_name": claims.get("name") or claims.get("email") or "Credentialing user",
        "role": role,
        "clerk_org_id": organization["id"],
        "clerk_org_slug": organization["slug"],
        "clerk_org_role": organization["role"],
        # Used only to deduplicate sign-in audit events. It is never returned
        # to the browser or persisted in client-facing API responses.
        "session_id": claims.get("sid"),
    }


def _better_auth_required_settings():
    settings = {
        "issuer": os.environ.get("BETTER_AUTH_URL", "").rstrip("/"),
        "audience": os.environ.get("BETTER_AUTH_JWT_AUDIENCE", "").strip(),
        "database_url": os.environ.get("BETTER_AUTH_DATABASE_URL", "").strip(),
    }
    if not all(settings.values()):
        raise auth_error(status_code=503, detail="Better Auth is not configured")
    return settings


def _better_auth_membership(user_id, organization_id):
    """Load the active organization role directly from Better Auth's database.

    The JWT intentionally carries identity and active organization but not a
    role. Looking up the membership on every API request makes a removal or
    role change effective immediately instead of waiting for a token refresh.
    """
    if not organization_id:
        return None
    settings = _better_auth_required_settings()
    try:
        import psycopg
    except ImportError as exc:
        raise auth_error(status_code=503, detail="Better Auth database support is not installed") from exc

    try:
        with psycopg.connect(settings["database_url"], connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT "role" FROM "member" WHERE "userId" = %s AND "organizationId" = %s LIMIT 1',
                    (user_id, organization_id),
                )
                row = cursor.fetchone()
    except Exception as exc:
        raise auth_error(status_code=503, detail="Workspace authorization is temporarily unavailable") from exc
    return row[0] if row else None


def _better_auth_platform_admin(email):
    configured = {
        value.strip().lower()
        for value in os.environ.get("COZEMA_PLATFORM_ADMIN_EMAILS", "").split(",")
        if value.strip()
    }
    return bool(email and email.lower() in configured)


def user_from_better_auth_claims(claims):
    """Normalize a Better Auth JWT into the application authorization shape."""
    user_id = claims.get("sub")
    if not user_id:
        raise ValueError("Better Auth token did not include a user ID")
    email = claims.get("email")
    organization_id = claims.get("activeOrganizationId")
    role = "super_admin" if _better_auth_platform_admin(email) else _better_auth_membership(user_id, organization_id)
    return {
        "uid": user_id,
        "auth_provider": "better_auth",
        "email": email,
        "display_name": claims.get("name") or email or "Credentialing user",
        "role": role or "unassigned",
        "better_auth_org_id": organization_id,
        "session_id": claims.get("sid"),
    }


def _better_auth_authentication_state(request):
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise auth_error(status_code=401, detail="A Better Auth session token is required")

    try:
        import jwt
        from jwt import PyJWKClient
    except ImportError as exc:
        raise auth_error(status_code=503, detail="Better Auth JWT verification is not installed") from exc

    settings = _better_auth_required_settings()
    token = authorization.removeprefix("Bearer ").strip()
    try:
        jwks_client = _BETTER_AUTH_JWK_CLIENTS.get(settings["issuer"])
        if jwks_client is None:
            jwks_client = PyJWKClient(f'{settings["issuer"]}/api/auth/jwks')
            _BETTER_AUTH_JWK_CLIENTS[settings["issuer"]] = jwks_client
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["EdDSA"],
            audience=settings["audience"],
            issuer=settings["issuer"],
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except Exception as exc:
        raise auth_error(status_code=401, detail="Invalid or expired Better Auth session token") from exc


def verify_session_token(request):
    if auth_provider() == "better_auth":
        return user_from_better_auth_claims(_better_auth_authentication_state(request))
    state = _clerk_authentication_state(request)
    if not state.is_signed_in:
        raise auth_error(status_code=401, detail="Invalid or expired Clerk session token")
    if state.payload.get("sts") == "pending":
        raise auth_error(status_code=403, detail="Select or join an organization before continuing")
    return user_from_claims(state.payload)


def require_authenticated_user(request: Request):
    """Verify the selected identity provider for browser-facing API routes."""
    user = local_development_user(request) or verify_session_token(request)
    request.state.authenticated_user = user
    return user


def optional_authenticated_user(request):
    local_user = local_development_user(request)
    if local_user:
        return local_user
    if not request.headers.get("authorization") and not request.cookies.get("__session"):
        return None
    return verify_session_token(request)


def _entity_id_for_clerk_organization(clerk_org_id):
    if not clerk_org_id:
        return None

    from app.connections import get_firestore_client

    matches = (
        get_firestore_client()
        .collection(ENTITY_COLLECTION)
        .where("clerk_organization_id", "==", clerk_org_id)
        .limit(1)
        .stream()
    )
    return next((document.id for document in matches), None)


def _entity_id_for_better_auth_organization(organization_id):
    if not organization_id:
        return None

    from app.connections import get_firestore_client

    matches = (
        get_firestore_client()
        .collection(ENTITY_COLLECTION)
        .where("better_auth_organization_id", "==", organization_id)
        .limit(1)
        .stream()
    )
    return next((document.id for document in matches), None)


def role_allows(role, permission):
    """Return whether a normalized role can perform an application action."""
    permissions = ROLE_PERMISSIONS.get(role, set())
    return "*" in permissions or permission in permissions


def entity_for_user(user):
    """Resolve the single client linked to the user's active organization."""
    if user.get("role") == "super_admin":
        return None

    organization_id = user.get("better_auth_org_id") if user.get("auth_provider") == "better_auth" else user.get("clerk_org_id")
    if not organization_id:
        raise auth_error(status_code=403, detail="Select a client organization before continuing")

    entity_id = (
        _entity_id_for_better_auth_organization(organization_id)
        if user.get("auth_provider") == "better_auth"
        else _entity_id_for_clerk_organization(organization_id)
    )
    if not entity_id:
        raise auth_error(status_code=403, detail="This organization is not linked to a credentialing client")
    return entity_id


def authorize_entity_access(entity_id, user, permission):
    """Ensure a request is both role-authorized and confined to its client."""
    if not role_allows(user.get("role"), permission):
        from app.audit import safely_record_security_event

        safely_record_security_event(
            "access_denied",
            user,
            {"permission": permission, "requested_entity_id": entity_id, "reason": "role"},
        )
        raise auth_error(status_code=403, detail="Your role does not have permission for this action")

    allowed_entity_id = entity_for_user(user)
    if allowed_entity_id and allowed_entity_id != entity_id:
        from app.audit import safely_record_security_event

        safely_record_security_event(
            "access_denied",
            user,
            {"permission": permission, "requested_entity_id": entity_id, "reason": "tenant"},
        )
        raise auth_error(status_code=403, detail="You do not have access to this credentialing client")
    return user


def require_entity_permission(permission):
    """Create a FastAPI dependency for an entity-scoped application permission."""
    def dependency(entity_id: str, user=Depends(require_authenticated_user)):
        return authorize_entity_access(entity_id, user, permission)

    return dependency


def require_workspace_permission(permission):
    """Authorize a non-entity route against the caller's active client role."""
    def dependency(user=Depends(require_authenticated_user)):
        if not role_allows(user.get("role"), permission):
            from app.audit import safely_record_security_event

            safely_record_security_event(
                "access_denied",
                user,
                {"permission": permission, "reason": "role"},
            )
            raise auth_error(status_code=403, detail="Your role does not have permission for this action")
        entity_for_user(user)
        return user

    return dependency


def save_user_profile(user):
    """Mirror safe identity metadata; Better Auth remains role authority."""
    from google.cloud import firestore

    from app.connections import get_firestore_client

    is_better_auth = user.get("auth_provider") == "better_auth"
    organization_id = user.get("better_auth_org_id") if is_better_auth else user.get("clerk_org_id")
    client_id = (
        _entity_id_for_better_auth_organization(organization_id)
        if is_better_auth
        else _entity_id_for_clerk_organization(organization_id)
    )
    profile = {
        **{key: value for key, value in user.items() if key != "session_id"},
        "client_id": client_id,
        "last_seen_at": firestore.SERVER_TIMESTAMP,
    }
    client = get_firestore_client()
    user_ref = client.collection(USER_COLLECTION).document(user["uid"])
    previous_profile = user_ref.get().to_dict() or {}
    user_ref.set(profile, merge=True)
    if client_id:
        # This Firestore document is an audit/UI mirror. The identity provider's
        # organization membership remains the source of truth for authorization.
        client.collection(ENTITY_COLLECTION).document(client_id).collection(
            ENTITY_MEMBER_COLLECTION
        ).document(user["uid"]).set(
            {
                "auth_user_id": user["uid"],
                "email": user.get("email"),
                "display_name": user.get("display_name"),
                "role": user.get("role"),
                "auth_provider": user.get("auth_provider"),
                "auth_organization_id": organization_id,
                "last_seen_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        if user.get("session_id") and previous_profile.get("last_seen_session_id") != user["session_id"]:
            from app.audit import safely_record_audit_event

            safely_record_audit_event(client_id, "sign_in", user)
            user_ref.set({"last_seen_session_id": user["session_id"]}, merge=True)
    return profile


def public_user_context(user, client_id):
    """Exclude session-only state from the browser workspace context."""
    return {
        key: value
        for key, value in {**user, "client_id": client_id}.items()
        if key != "session_id"
    }


def workspace_access_status(user, client_id):
    """Describe why a signed-in user can or cannot enter a client workspace."""
    if user.get("role") == "super_admin" or client_id:
        return "ready"
    if user.get("better_auth_org_id") or user.get("clerk_org_id"):
        return "unlinked_organization"
    return "no_client_organization"


def link_clerk_organization(entity_id, clerk_organization_id):
    """Associate one Clerk Organization with one client entity for future scoping."""
    if not entity_id or not clerk_organization_id:
        raise ValueError("entity_id and clerk_organization_id are required")

    from app.connections import get_firestore_client

    get_firestore_client().collection(ENTITY_COLLECTION).document(entity_id).set(
        {"clerk_organization_id": clerk_organization_id},
        merge=True,
    )
