import os
import secrets
import uuid
import logging
import hashlib
import hmac
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud import firestore
from google.oauth2 import id_token

from app.config import (
    DEFAULT_ENTITY_ID,
    ENTITY_COLLECTION,
    ENTITY_MEMBER_COLLECTION,
    EVENT_COLLECTION,
    PROVIDER_COLLECTION,
    SCOPES,
    allow_duplicate_drive_imports,
    drive_watch_owner,
)
from app.auth import (
    entity_for_user,
    local_development_user,
    optional_authenticated_user,
    public_user_context,
    require_entity_permission,
    require_authenticated_user,
    require_workspace_permission,
    save_user_profile,
    web_auth_config,
    workspace_access_status,
)
from app.connections import (
    connection_status, create_flow, credentials_to_dict, delete_connection_lookup, find_connection_by_field,
    get_connection, get_drive_service, get_firestore_client, get_webhook_url, list_entity_connections,
    save_connection_lookup, update_connection,
)
from app.audit import safely_record_audit_event
from app.clerk import (
    invitation_redirect_url, invite_organization_member, organization_memberships, remove_organization_member,
    serialize_membership, update_organization_member_role,
)
from app.processing import (
    process_drive_changes, process_drive_document_in_memory, process_queued_drive_file,
)
from app.task_queue import verify_task_request
from app.providers import (
    delete_provider, get_entity, get_group, get_group_issue, get_provider, get_provider_issue, list_groups,
    list_provider_expirations, list_providers, list_issue_summaries, list_entity_activity, get_record_activity,
    merge_duplicate_providers, remove_group_issue, remove_provider_issue,
    backfill_revision_previous_files, get_entity_ref,
    update_group_manually, update_provider_manually,
)
from app.reconciliation import reconcile_provider_records
from app.report_exports import report_csv, report_filename
from app.reports import build_report, report_catalog

router = APIRouter()
authenticated_router = APIRouter(dependencies=[Depends(require_authenticated_user)])
logger = logging.getLogger(__name__)
PROJECT_DIRECTORY = Path(__file__).resolve().parent.parent
REACT_DASHBOARD_INDEX = PROJECT_DIRECTORY / "react-dashboard" / "dist" / "index.html"
ACTIVE_DRIVE_INTAKE_EVENT_STATUSES = ("detected", "queued", "processing", "retrying")


def react_dashboard_view():
    """Serve the compiled React SPA for every browser-facing dashboard route."""
    if not REACT_DASHBOARD_INDEX.is_file():
        raise HTTPException(
            status_code=503,
            detail="The React dashboard has not been built. Run npm run build in react-dashboard.",
        )
    return FileResponse(REACT_DASHBOARD_INDEX, headers={"Cache-Control": "no-store"})


@router.get("/auth/config")
def get_auth_config(request: Request):
    if local_development_user(request):
        return {"enabled": True, "provider": "local_dev_bypass"}
    return web_auth_config()


def _entity_id_for_better_auth_event(organization_id):
    if not organization_id:
        return None
    matches = (
        get_firestore_client()
        .collection(ENTITY_COLLECTION)
        .where("better_auth_organization_id", "==", organization_id)
        .limit(1)
        .stream()
    )
    return next((document.id for document in matches), None)


@router.post("/internal/auth-events")
async def receive_better_auth_event(request: Request):
    """Record organization changes emitted by the Better Auth service.

    Browser requests cannot call this endpoint: each payload is authenticated by
    an HMAC generated from a server-only per-environment secret.
    """
    secret = os.environ.get("AUTH_EVENT_WEBHOOK_SECRET", "")
    payload = await request.body()
    supplied_signature = request.headers.get("x-cozema-auth-event", "")
    expected_signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest() if secret else ""
    if not secret or not hmac.compare_digest(supplied_signature, expected_signature):
        raise HTTPException(status_code=401, detail="Invalid authentication event signature")
    try:
        event = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid authentication event payload") from exc
    if not isinstance(event, dict) or not isinstance(event.get("event"), str):
        raise HTTPException(status_code=400, detail="Invalid authentication event payload")

    entity_id = _entity_id_for_better_auth_event(event.get("organization_id"))
    if not entity_id:
        # The service may briefly emit events while a newly created organization
        # is being linked. Do not turn that into a public error or cross-tenant write.
        logger.warning("Ignoring Better Auth event for an unlinked organization")
        return {"recorded": False}
    actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
    safely_record_audit_event(
        entity_id,
        event["event"],
        {"uid": actor.get("uid"), "email": actor.get("email"), "display_name": actor.get("display_name"), "role": "identity_provider"},
        event.get("target") if isinstance(event.get("target"), dict) else {},
        event.get("metadata") if isinstance(event.get("metadata"), dict) else {},
    )
    return {"recorded": True}


@router.get("/auth/me")
def get_authenticated_user(request: Request):
    try:
        user = optional_authenticated_user(request)
        if not user:
            return {"authenticated": False, "auth_enabled": web_auth_config()["enabled"]}
        profile = save_user_profile(user)
        return {
            "authenticated": True,
            # Firestore server timestamp sentinels are write-only values and
            # cannot be JSON-encoded for the browser.
            "user": public_user_context(user, profile.get("client_id")),
            "workspace_ready": bool(profile.get("client_id")) or user.get("role") == "super_admin",
            "workspace_status": workspace_access_status(user, profile.get("client_id")),
            "auth_provider": user.get("auth_provider"),
            "auth_base_url": os.environ.get("BETTER_AUTH_URL", "").rstrip("/") if user.get("auth_provider") == "better_auth" else None,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unable to prepare workspace context")
        raise HTTPException(
            status_code=500,
            detail="Unable to prepare the workspace context",
        ) from exc


@router.get("/")
def home():
    return react_dashboard_view()


@router.get("/accept-invitation")
def accept_invitation_view():
    """Serve the identity provider's invitation flow inside the React app."""
    return react_dashboard_view()


@router.get("/providers/{provider_id}/view")
def provider_view(provider_id: str):
    return react_dashboard_view()


@router.get("/practices/{group_id}/view")
def practice_view(group_id: str):
    return react_dashboard_view()


@router.get("/providers/view")
def providers_view():
    return react_dashboard_view()


@router.get("/practices/view")
def practices_view():
    return react_dashboard_view()


@router.get("/expirations/view")
def expirations_view():
    return react_dashboard_view()


@router.get("/issues/view")
def issues_view():
    return react_dashboard_view()


@router.get("/reports/view")
def reports_view():
    return react_dashboard_view()


@router.get("/users/view")
def users_view():
    return react_dashboard_view()


@router.get("/activity/view")
@router.get("/tasks/view")
def activity_view():
    return react_dashboard_view()


@router.get("/enrollment/view")
@router.get("/data-connections/view")
@router.get("/settings/view")
def workspace_placeholder_view():
    return react_dashboard_view()


@router.get("/providers/{provider_id}/issues/{issue_id}/view")
def issue_view(provider_id: str, issue_id: str):
    return react_dashboard_view()


@router.get("/practices/{group_id}/issues/{issue_id}/view")
def practice_issue_view(group_id: str, issue_id: str):
    return react_dashboard_view()


@authenticated_router.get("/providers", dependencies=[Depends(require_entity_permission("records:read"))])
def get_providers(
    entity_id: str = Query(default=DEFAULT_ENTITY_ID),
    limit: int = Query(default=100, ge=1, le=500),
):
    return {"providers": list_providers(limit, entity_id)}


@authenticated_router.get("/providers/{provider_id}", dependencies=[Depends(require_entity_permission("records:read"))])
def get_provider_by_id(provider_id: str, entity_id: str = Query(default=DEFAULT_ENTITY_ID)):
    return get_provider(provider_id, entity_id)


@authenticated_router.get("/entities/{entity_id}", dependencies=[Depends(require_entity_permission("records:read"))])
def get_entity_by_id(entity_id: str):
    return get_entity(entity_id)


@authenticated_router.get("/entities/{entity_id}/groups", dependencies=[Depends(require_entity_permission("records:read"))])
def get_entity_groups(entity_id: str, limit: int = Query(default=100, ge=1, le=500)):
    return {"groups": list_groups(limit, entity_id)}


@authenticated_router.get("/entities/{entity_id}/connections", dependencies=[Depends(require_entity_permission("records:read"))])
def get_entity_connections(entity_id: str):
    return {"google_drive": connection_status(entity_id)}


def summarize_drive_intake_events(events):
    """Return a privacy-safe summary for the workspace navigation indicator."""
    counts = {status: 0 for status in ACTIVE_DRIVE_INTAKE_EVENT_STATUSES}
    for event in events:
        payload = event.to_dict() if hasattr(event, "to_dict") else event
        status = payload.get("status") if isinstance(payload, dict) else None
        if status in counts:
            counts[status] += 1

    active_document_count = sum(counts.values())
    return {
        "active_document_count": active_document_count,
        "is_processing": active_document_count > 0,
        "detected_document_count": counts["detected"],
        "queued_document_count": counts["queued"],
        "processing_document_count": counts["processing"],
        "retrying_document_count": counts["retrying"],
    }


@authenticated_router.get(
    "/entities/{entity_id}/drive/intake-status",
    dependencies=[Depends(require_entity_permission("records:read"))],
)
def get_drive_intake_status(entity_id: str):
    """Report only active intake counts; never expose document metadata in the shell."""
    events = (
        get_firestore_client()
        .collection(ENTITY_COLLECTION)
        .document(entity_id)
        .collection(EVENT_COLLECTION)
        .where(
            filter=firestore.FieldFilter(
                "status", "in", list(ACTIVE_DRIVE_INTAKE_EVENT_STATUSES)
            )
        )
        .stream()
    )
    return summarize_drive_intake_events(events)


@authenticated_router.delete(
    "/entities/{entity_id}/connections/google-drive",
    dependencies=[Depends(require_entity_permission("documents:manage"))],
)
def disconnect_entity_google_drive(entity_id: str):
    """Remove this client's Drive authorization and intake monitoring state.

    Imported credentialing records remain intact. Stopping a Google watch is
    best effort because an expired Google refresh token must not prevent an
    administrator from disconnecting the integration.
    """
    try:
        connection = get_connection(entity_id)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        connection = {}

    if connection.get("channel_id") and connection.get("resource_id"):
        try:
            get_drive_service(connection).channels().stop(
                body={
                    "id": connection["channel_id"],
                    "resourceId": connection["resource_id"],
                }
            ).execute()
        except Exception:
            logger.warning(
                "Could not stop Google Drive watch while disconnecting entity %s",
                entity_id,
                exc_info=True,
            )

    delete_connection_lookup("oauth_state", connection.get("oauth_state"))
    delete_connection_lookup("channel_id", connection.get("channel_id"))
    update_connection(
        {
            "credentials": firestore.DELETE_FIELD,
            "oauth_state": firestore.DELETE_FIELD,
            "code_verifier": firestore.DELETE_FIELD,
            "folder_id": firestore.DELETE_FIELD,
            "folder_name": firestore.DELETE_FIELD,
            "page_token": firestore.DELETE_FIELD,
            "channel_id": firestore.DELETE_FIELD,
            "channel_token": firestore.DELETE_FIELD,
            "resource_id": firestore.DELETE_FIELD,
            "channel_expiration": firestore.DELETE_FIELD,
        },
        entity_id,
    )
    return {"google_drive": connection_status(entity_id)}


@authenticated_router.get("/entities/{entity_id}/issues", dependencies=[Depends(require_entity_permission("issues:read"))])
def get_entity_issues(entity_id: str):
    entity_ref = get_entity_ref(get_firestore_client(), entity_id)
    return {"issues": list_issue_summaries(entity_ref)}


@authenticated_router.get("/entities/{entity_id}/groups/{group_id}", dependencies=[Depends(require_entity_permission("records:read"))])
def get_entity_group_by_id(entity_id: str, group_id: str):
    return get_group(group_id, entity_id)


@authenticated_router.patch("/entities/{entity_id}/groups/{group_id}", dependencies=[Depends(require_entity_permission("records:write"))])
async def update_entity_group_by_id(
    entity_id: str, group_id: str, request: Request, user=Depends(require_authenticated_user)
):
    payload = await member_payload(request)
    return update_group_manually(group_id, payload.get("fields"), user, entity_id)


@authenticated_router.get("/entities/{entity_id}/groups/{group_id}/activity", dependencies=[Depends(require_entity_permission("activity:read"))])
def get_entity_group_activity(
    entity_id: str, group_id: str, limit: int = Query(default=100, ge=1, le=500)
):
    return get_record_activity(group_id, "practice", limit, entity_id)


@authenticated_router.get("/entities/{entity_id}/groups/{group_id}/issues/{issue_id}", dependencies=[Depends(require_entity_permission("issues:read"))])
def get_entity_group_issue_by_id(entity_id: str, group_id: str, issue_id: str):
    return get_group_issue(group_id, issue_id, entity_id)


@authenticated_router.post("/entities/{entity_id}/groups/{group_id}/issues/{issue_id}/remove", dependencies=[Depends(require_entity_permission("issues:manage"))])
def remove_entity_group_issue(entity_id: str, group_id: str, issue_id: str, user=Depends(require_authenticated_user)):
    issue = remove_group_issue(group_id, issue_id, user, entity_id)
    safely_record_audit_event(
        entity_id,
        "issue_removed",
        user,
        {"record_type": "practice", "record_id": group_id, "issue_id": issue_id},
        {"issue_type": issue.get("type"), "affected_fields": issue.get("affected_fields") or []},
    )
    return {"issue_id": issue_id, "status": "removed"}


@authenticated_router.get("/entities/{entity_id}/providers", dependencies=[Depends(require_entity_permission("records:read"))])
def get_entity_providers(entity_id: str, limit: int = Query(default=100, ge=1, le=500)):
    return {"providers": list_providers(limit, entity_id)}


@authenticated_router.get("/entities/{entity_id}/providers/{provider_id}/activity", dependencies=[Depends(require_entity_permission("activity:read"))])
def get_entity_provider_activity(
    entity_id: str, provider_id: str, limit: int = Query(default=100, ge=1, le=500)
):
    return get_record_activity(provider_id, "provider", limit, entity_id)


@authenticated_router.get("/entities/{entity_id}/expirations", dependencies=[Depends(require_entity_permission("issues:read"))])
def get_entity_expirations(entity_id: str, limit: int = Query(default=1000, ge=1, le=5000)):
    return {"expirations": list_provider_expirations(limit, entity_id)}


@authenticated_router.get("/entities/{entity_id}/activity", dependencies=[Depends(require_entity_permission("activity:read"))])
def get_entity_activity(
    entity_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    record_type: str = Query(default="all"),
    document_category: str | None = Query(default=None),
):
    return {
        "activity": list_entity_activity(
            limit, record_type, document_category, entity_id
        )
    }


@authenticated_router.get("/entities/{entity_id}/reports", dependencies=[Depends(require_entity_permission("reports:read"))])
def get_entity_report_catalog(entity_id: str):
    get_entity(entity_id)
    return {"reports": report_catalog()}


@authenticated_router.get("/entities/{entity_id}/reports/{report_type}", dependencies=[Depends(require_entity_permission("reports:read"))])
def get_entity_report(
    entity_id: str,
    report_type: str,
    practice_id: str | None = None,
    provider_id: str | None = None,
    issue_type: str | None = None,
    expiration_status: str | None = None,
):
    return build_report(
        report_type, entity_id, practice_id, provider_id, issue_type, expiration_status
    )


@authenticated_router.get("/entities/{entity_id}/reports/{report_type}/export", dependencies=[Depends(require_entity_permission("reports:export"))])
def export_entity_report(
    entity_id: str,
    report_type: str,
    practice_id: str | None = None,
    provider_id: str | None = None,
    issue_type: str | None = None,
    expiration_status: str | None = None,
):
    report = build_report(
        report_type, entity_id, practice_id, provider_id, issue_type, expiration_status
    )
    return StreamingResponse(
        iter([report_csv(report)]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{report_filename(report_type)}"'},
    )


@authenticated_router.get("/entities/{entity_id}/providers/{provider_id}", dependencies=[Depends(require_entity_permission("records:read"))])
def get_entity_provider_by_id(entity_id: str, provider_id: str):
    return get_provider(provider_id, entity_id)


@authenticated_router.patch("/entities/{entity_id}/providers/{provider_id}", dependencies=[Depends(require_entity_permission("records:write"))])
async def update_entity_provider_by_id(
    entity_id: str, provider_id: str, request: Request, user=Depends(require_authenticated_user)
):
    payload = await member_payload(request)
    return update_provider_manually(provider_id, payload.get("fields"), user, entity_id)


@authenticated_router.delete("/entities/{entity_id}/providers/{provider_id}", dependencies=[Depends(require_entity_permission("records:write"))])
def delete_entity_provider_by_id(
    entity_id: str, provider_id: str, user=Depends(require_authenticated_user)
):
    """Permanently remove a provider from this workspace.

    The audit event is stored at entity scope because the provider's revision
    tree is intentionally removed with the record.
    """
    get_provider(provider_id, entity_id)
    result = delete_provider(provider_id, entity_id)
    safely_record_audit_event(
        entity_id,
        "provider_deleted",
        user,
        {
            "record_type": "provider",
            "record_id": provider_id,
            "deleted_document_count": result.get("deleted_document_count"),
            "deletion_duration_ms": result.get("deletion_duration_ms"),
        },
        {},
    )
    return result


@authenticated_router.get("/entities/{entity_id}/providers/{provider_id}/issues/{issue_id}", dependencies=[Depends(require_entity_permission("issues:read"))])
def get_entity_provider_issue_by_id(entity_id: str, provider_id: str, issue_id: str):
    return get_provider_issue(provider_id, issue_id, entity_id)


@authenticated_router.post("/entities/{entity_id}/providers/{provider_id}/issues/{issue_id}/remove", dependencies=[Depends(require_entity_permission("issues:manage"))])
def remove_entity_provider_issue(entity_id: str, provider_id: str, issue_id: str, user=Depends(require_authenticated_user)):
    issue = remove_provider_issue(provider_id, issue_id, user, entity_id)
    safely_record_audit_event(
        entity_id,
        "issue_removed",
        user,
        {"record_type": "provider", "record_id": provider_id, "issue_id": issue_id},
        {"issue_type": issue.get("type"), "affected_fields": issue.get("affected_fields") or []},
    )
    return {"issue_id": issue_id, "status": "removed"}


@authenticated_router.get("/entities/{entity_id}/members", dependencies=[Depends(require_entity_permission("members:manage"))])
def get_entity_members(entity_id: str):
    organization_id = get_entity_clerk_organization_id(entity_id)
    members = [serialize_membership(member) for member in organization_memberships(organization_id)]
    return {"members": sorted(members, key=lambda member: member.get("display_name") or member.get("email") or "")}


def get_entity_clerk_organization_id(entity_id):
    entity = get_firestore_client().collection(ENTITY_COLLECTION).document(entity_id).get()
    organization_id = (entity.to_dict() or {}).get("clerk_organization_id") if entity.exists else None
    if not organization_id:
        raise HTTPException(status_code=409, detail="This client is not linked to a Clerk organization")
    return organization_id


async def member_payload(request):
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Provide a JSON request body") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Provide a JSON request body")
    return payload


@authenticated_router.post("/entities/{entity_id}/members/invitations", dependencies=[Depends(require_entity_permission("members:manage"))])
async def invite_entity_member(entity_id: str, request: Request, user=Depends(require_authenticated_user)):
    payload = await member_payload(request)
    email = str(payload.get("email") or "").strip().lower()
    role = payload.get("role")
    if "@" not in email or len(email) > 320:
        raise HTTPException(status_code=400, detail="Provide a valid email address")
    invitation = invite_organization_member(
        get_entity_clerk_organization_id(entity_id),
        email,
        role,
        redirect_url=invitation_redirect_url(),
    )
    safely_record_audit_event(
        entity_id,
        "member_invited",
        user,
        {"email": email},
        {"role": role, "invitation_id": invitation.get("id")},
    )
    return {"invitation": {"id": invitation.get("id"), "email": email, "role": role}}


@authenticated_router.patch("/entities/{entity_id}/members/{member_id}", dependencies=[Depends(require_entity_permission("members:manage"))])
async def update_entity_member(entity_id: str, member_id: str, request: Request, user=Depends(require_authenticated_user)):
    if member_id == user.get("uid"):
        raise HTTPException(status_code=400, detail="You cannot change your own role")
    payload = await member_payload(request)
    role = payload.get("role")
    membership = update_organization_member_role(
        get_entity_clerk_organization_id(entity_id), member_id, role
    )
    safely_record_audit_event(
        entity_id,
        "member_role_updated",
        user,
        {"user_id": member_id},
        {"role": role, "clerk_membership_id": membership.get("id")},
    )
    return {"member": serialize_membership(membership)}


@authenticated_router.delete("/entities/{entity_id}/members/{member_id}", dependencies=[Depends(require_entity_permission("members:manage"))])
def delete_entity_member(entity_id: str, member_id: str, user=Depends(require_authenticated_user)):
    if member_id == user.get("uid"):
        raise HTTPException(status_code=400, detail="You cannot remove yourself from this client")
    remove_organization_member(get_entity_clerk_organization_id(entity_id), member_id)
    safely_record_audit_event(entity_id, "member_removed", user, {"user_id": member_id})
    return Response(status_code=204)


def require_internal_token(request: Request):
    expected_token = os.environ.get("DRIVE_WATCH_RENEWAL_TOKEN", "").strip()
    provided_token = request.headers.get("x-renewal-token", "")
    if not expected_token or not secrets.compare_digest(provided_token, expected_token):
        raise HTTPException(status_code=401, detail="Invalid internal token")


def require_scheduler_identity(request: Request):
    """Require the OIDC identity assigned to the production Scheduler job."""
    scheduler_account = os.environ.get("DRIVE_WATCH_RENEWAL_SERVICE_ACCOUNT", "").strip()
    audience = os.environ.get("DRIVE_WATCH_RENEWAL_AUDIENCE", "").strip()
    authorization = request.headers.get("authorization", "")
    if not scheduler_account or not audience or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid scheduler authorization")

    try:
        claims = id_token.verify_oauth2_token(
            authorization.removeprefix("Bearer "),
            GoogleAuthRequest(),
            audience=audience,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid scheduler authorization") from exc

    if claims.get("email") != scheduler_account or not claims.get("email_verified"):
        raise HTTPException(status_code=401, detail="Unexpected scheduler service account")


@router.post("/internal/entities/{entity_id}/providers/reconcile")
async def reconcile_duplicate_providers(entity_id: str, request: Request):
    require_internal_token(request)
    payload = await request.json()
    target_provider_id = payload.get("target_provider_id")
    duplicate_provider_ids = payload.get("duplicate_provider_ids")
    if not isinstance(target_provider_id, str) or not isinstance(duplicate_provider_ids, list):
        raise HTTPException(status_code=400, detail="Provide target_provider_id and duplicate_provider_ids")
    return merge_duplicate_providers(target_provider_id, duplicate_provider_ids, entity_id)


@router.post("/internal/entities/{entity_id}/providers/{provider_id}/reconcile")
def reconcile_provider(entity_id: str, provider_id: str, request: Request):
    require_internal_token(request)
    provider_ref = get_entity_ref(get_firestore_client(), entity_id).collection(
        PROVIDER_COLLECTION
    ).document(provider_id)
    if not provider_ref.get().exists:
        raise HTTPException(status_code=404, detail="Provider not found")
    return reconcile_provider_records(provider_ref)


@router.delete("/internal/entities/{entity_id}/providers/{provider_id}")
def delete_internal_provider(entity_id: str, provider_id: str, request: Request):
    require_internal_token(request)
    return delete_provider(provider_id, entity_id)


@router.post("/internal/entities/{entity_id}/revisions/backfill-previous-files")
def backfill_previous_revision_files(entity_id: str, request: Request):
    require_internal_token(request)
    return backfill_revision_previous_files(entity_id)


def prepare_google_connection(entity_id):
    flow = create_flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    update_connection({"oauth_state": state, "code_verifier": flow.code_verifier}, entity_id)
    save_connection_lookup("oauth_state", state, entity_id)
    return authorization_url


@authenticated_router.get("/entities/{entity_id}/oauth/google/start", dependencies=[Depends(require_entity_permission("documents:manage"))])
def google_start(entity_id: str):
    return {"authorization_url": prepare_google_connection(entity_id)}


@authenticated_router.get("/oauth/google/start", dependencies=[Depends(require_workspace_permission("documents:manage"))])
def google_start_legacy(user=Depends(require_authenticated_user)):
    """Compatibility route for the former single-client Drive connection."""
    return RedirectResponse(prepare_google_connection(entity_for_user(user)))


@router.get("/oauth/google/callback")
def google_callback(request: Request):
    entity_id, connection = find_connection_by_field("oauth_state", request.query_params.get("state"))
    if not entity_id or not connection:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    code_verifier = connection.get("code_verifier")
    if not code_verifier:
        raise HTTPException(status_code=400, detail="OAuth authorization has expired")

    flow = create_flow()
    flow.code_verifier = code_verifier
    # Cloud Run terminates TLS before forwarding requests to the container, but
    # local OAuth uses an intentionally plain-HTTP loopback callback.
    forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
    authorization_response = str(
        request.url.replace(scheme="https") if forwarded_proto == "https" else request.url
    )
    try:
        flow.fetch_token(authorization_response=authorization_response)
    except Warning as exc:
        logger.warning("Google OAuth did not return the required Drive scope: %s", exc)
        raise HTTPException(
            status_code=400,
            detail="Google Drive permission was not granted. Start the connection again and approve Drive access.",
        ) from exc

    granted_scopes = set(flow.credentials.scopes or [])
    if SCOPES[0] not in granted_scopes:
        raise HTTPException(
            status_code=400,
            detail="Google Drive permission was not granted. Start the connection again and approve Drive access.",
        )

    update_connection(
        {
            "credentials": credentials_to_dict(flow.credentials),
            "oauth_state": firestore.DELETE_FIELD,
            "code_verifier": firestore.DELETE_FIELD,
        },
        entity_id,
    )
    delete_connection_lookup("oauth_state", request.query_params.get("state"))
    app_base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")
    if app_base_url:
        return RedirectResponse(
            f"{app_base_url}/data-connections/view?entity={entity_id}&connected=google-drive"
        )
    return {"connected": True, "message": "Google Drive connected successfully"}



def list_drive_folders(entity_id):
    service = get_drive_service(entity_id=entity_id)
    folders = []
    page_token = None
    # Folder selection needs to work for a real client Drive, not only its
    # first page of folders. Keep a deliberate upper bound for responsiveness.
    while len(folders) < 5000:
        result = (
            service.files()
            .list(
                q="mimeType='application/vnd.google-apps.folder' and trashed=false",
                spaces="drive",
                orderBy="name_natural",
                pageSize=min(1000, 5000 - len(folders)),
                pageToken=page_token,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="files(id,name,modifiedTime),nextPageToken",
            )
            .execute()
        )
        folders.extend(result.get("files", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return {"folders": folders}


def select_intake_folder(entity_id, folder_id, service=None):
    """Verify that a selected folder is still available to the connected account."""
    if not isinstance(folder_id, str) or not folder_id.strip():
        raise HTTPException(status_code=400, detail="Choose a Google Drive folder")

    service = service or get_drive_service(entity_id=entity_id)
    try:
        folder = (
            service.files()
            .get(
                fileId=folder_id,
                fields="id,name,mimeType,trashed",
                supportsAllDrives=True,
            )
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=404, detail="The selected Google Drive folder is unavailable") from exc

    if (
        folder.get("mimeType") != "application/vnd.google-apps.folder"
        or folder.get("trashed")
    ):
        raise HTTPException(status_code=400, detail="Choose an available Google Drive folder")

    return {"folder": folder}


@authenticated_router.get("/entities/{entity_id}/drive/folders", dependencies=[Depends(require_entity_permission("documents:manage"))])
def get_drive_folders(entity_id: str):
    return list_drive_folders(entity_id)


@authenticated_router.put("/entities/{entity_id}/drive/intake-folder", dependencies=[Depends(require_entity_permission("documents:manage"))])
async def set_drive_intake_folder(entity_id: str, request: Request):
    payload = await member_payload(request)
    # Folder selection is not considered complete until Drive monitoring is
    # active. This keeps a failed watch from looking like a usable intake.
    watch = activate_intake_folder(entity_id, payload.get("folder_id"))
    selected = {"folder": watch.pop("folder")}
    return {**selected, **watch}


@authenticated_router.get("/entities/{entity_id}/drive/find-test-folder", dependencies=[Depends(require_entity_permission("documents:manage"))])
def find_test_folder(entity_id: str):
    folders = list_drive_folders(entity_id).get("folders", [])
    default_folder = next((folder for folder in folders if folder.get("name") == "Credentialing Intake"), None)
    if not default_folder:
        raise HTTPException(status_code=404, detail="Credentialing Intake folder not found")
    return activate_intake_folder(entity_id, default_folder["id"])


@authenticated_router.get("/drive/find-test-folder", dependencies=[Depends(require_workspace_permission("documents:manage"))])
def find_test_folder_legacy(user=Depends(require_authenticated_user)):
    return find_test_folder(entity_for_user(user))


def process_drive_pdf_for_entity(entity_id, file_id):
    """Download a Drive document, OCR and interpret it, then release its in-memory bytes."""
    result = process_drive_document_in_memory(get_drive_service(entity_id=entity_id), file_id, entity_id)
    metadata = result["metadata"]

    logger.info(
        "Processed Drive document file_id=%s name=%s pages=%s extracted_characters=%s",
        metadata["id"],
        metadata.get("name"),
        result["page_count"],
        len(result["extracted_text"]),
    )
    return {
        "processed": True,
        "file": {"id": metadata["id"], "name": metadata.get("name")},
        "page_count": result["page_count"],
        "extracted_text": result["extracted_text"],
        "gemini_interpretation": result["gemini_interpretation"],
    }


@authenticated_router.post("/entities/{entity_id}/drive/process/{file_id}", dependencies=[Depends(require_entity_permission("documents:manage"))])
def process_drive_pdf(entity_id: str, file_id: str):
    return process_drive_pdf_for_entity(entity_id, file_id)


@authenticated_router.post("/drive/process/{file_id}", dependencies=[Depends(require_workspace_permission("documents:manage"))])
def process_drive_pdf_legacy(file_id: str, user=Depends(require_authenticated_user)):
    return process_drive_pdf_for_entity(entity_for_user(user), file_id)


def _create_drive_watch(service):
    """Create, but do not yet persist, a new Google Drive change watch."""
    try:
        page_token = service.changes().getStartPageToken().execute()["startPageToken"]
    except Exception as exc:
        logger.exception("Could not get a Google Drive change token")
        raise HTTPException(status_code=502, detail="Google Drive could not prepare document monitoring") from exc

    channel_id = str(uuid.uuid4())
    channel_token = secrets.token_urlsafe(32)
    try:
        response = (
            service.changes()
            .watch(
                pageToken=page_token,
                body={
                    "id": channel_id,
                    "type": "web_hook",
                    "address": get_webhook_url(),
                    "token": channel_token,
                },
            )
            .execute()
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not create a Google Drive change watch")
        raise HTTPException(status_code=502, detail="Google Drive could not activate document monitoring") from exc

    if not response.get("resourceId"):
        raise HTTPException(status_code=502, detail="Google Drive did not confirm document monitoring")
    return {
        "page_token": page_token,
        "channel_id": channel_id,
        "channel_token": channel_token,
        "resource_id": response["resourceId"],
        "channel_expiration": response.get("expiration"),
    }


def _stop_drive_watch(service, connection, entity_id):
    if not connection.get("channel_id") or not connection.get("resource_id"):
        return
    try:
        service.channels().stop(
            body={"id": connection["channel_id"], "resourceId": connection["resource_id"]}
        ).execute()
    except Exception:
        logger.warning("Could not stop prior Google Drive watch for entity_id=%s", entity_id, exc_info=True)


def _persist_drive_watch(entity_id, connection, service, folder=None):
    """Atomically replace a watch after Google has accepted the new one."""
    watch = _create_drive_watch(service)
    update = {**watch, "watch_owner": drive_watch_owner()}
    if folder:
        update.update({"folder_id": folder["id"], "folder_name": folder["name"]})

    update_connection(update, entity_id)
    delete_connection_lookup("channel_id", connection.get("channel_id"))
    save_connection_lookup("channel_id", watch["channel_id"], entity_id)
    _stop_drive_watch(service, connection, entity_id)
    return {
        "watching": True,
        "intake_ready": True,
        "channel_id": watch["channel_id"],
        "expires_at": watch["channel_expiration"],
    }


def activate_intake_folder(entity_id, folder_id):
    """Verify a folder and activate its watch before committing the selection."""
    connection = get_connection(entity_id)
    service = get_drive_service(connection)
    folder = select_intake_folder(entity_id, folder_id, service)["folder"]
    return {"folder": folder, **_persist_drive_watch(entity_id, connection, service, folder)}


def start_drive_watch_for_entity(entity_id):
    connection = get_connection(entity_id)
    if not connection.get("folder_id"):
        raise HTTPException(status_code=409, detail="Choose an intake folder before activating monitoring")
    return _persist_drive_watch(entity_id, connection, get_drive_service(connection))


@authenticated_router.post("/entities/{entity_id}/drive/watch", dependencies=[Depends(require_entity_permission("documents:manage"))])
def start_drive_watch(entity_id: str):
    return start_drive_watch_for_entity(entity_id)


@authenticated_router.post("/drive/watch", dependencies=[Depends(require_workspace_permission("documents:manage"))])
def start_drive_watch_legacy(user=Depends(require_authenticated_user)):
    return start_drive_watch_for_entity(entity_for_user(user))


@router.post("/drive/watch/renew")
def renew_drive_watch(request: Request):
    require_scheduler_identity(request)
    renewed = []
    owner = drive_watch_owner()
    for entity_id, connection in list_entity_connections():
        if not connection.get("folder_id"):
            continue
        # A local runner can use the shared development Firestore database for
        # testing, but only its temporary tunnel can renew its local watch.
        if connection.get("watch_owner", "cloud") != owner:
            continue
        try:
            start_drive_watch_for_entity(entity_id)
            renewed.append(entity_id)
        except Exception:
            logger.exception("Could not renew Drive watch for entity_id=%s", entity_id)
    return {"renewed_entity_ids": renewed}



@router.post("/webhooks/google-drive", status_code=204)
async def google_drive_webhook(request: Request):
    headers = request.headers
    entity_id, connection = find_connection_by_field("channel_id", headers.get("x-goog-channel-id", ""))
    if not entity_id or not connection:
        raise HTTPException(status_code=401, detail="Invalid Google Drive channel")
    if not secrets.compare_digest(
        headers.get("x-goog-channel-id", ""), connection.get("channel_id", "")
    ) or not secrets.compare_digest(
        headers.get("x-goog-channel-token", ""), connection.get("channel_token", "")
    ):
        raise HTTPException(status_code=401, detail="Invalid Google Drive channel")

    if headers.get("x-goog-resource-id") != connection.get("resource_id"):
        raise HTTPException(status_code=401, detail="Invalid Google Drive resource")

    changes = process_drive_changes(get_drive_service(connection), connection, entity_id)
    return Response(status_code=204, headers={"X-Detected-Changes": str(len(changes))})


@router.post("/tasks/process-drive-file", status_code=204)
async def process_drive_file_task(request: Request):
    """Cloud Tasks worker: process one queued file and retry unexpected failures."""
    verify_task_request(request)
    payload = await request.json()
    file_id = payload.get("file_id")
    entity_id = payload.get("entity_id")
    if not isinstance(file_id, str) or not file_id or not isinstance(entity_id, str) or not entity_id:
        raise HTTPException(status_code=400, detail="Cloud Tasks payload is missing client or file ID")

    event_ref = get_firestore_client().collection(ENTITY_COLLECTION).document(entity_id).collection(
        EVENT_COLLECTION
    ).document(f"drive-{file_id}")
    event = event_ref.get()
    if (
        not allow_duplicate_drive_imports()
        and event.exists
        and event.to_dict().get("status") in {"processed", "imported", "skipped"}
    ):
        return Response(status_code=204)

    event_ref.set(
        {
            "status": "processing",
            "attempt_count": firestore.Increment(1),
            "last_attempt_at": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )
    try:
        result = process_queued_drive_file(get_drive_service(entity_id=entity_id), file_id, entity_id)
    except HTTPException as exc:
        if exc.status_code in {404, 413, 415}:
            event_ref.set({"status": "skipped", "error": exc.detail}, merge=True)
            return Response(status_code=204)
        event_ref.set({"status": "retrying", "error": exc.detail}, merge=True)
        raise HTTPException(status_code=500, detail="Drive file processing will be retried") from exc
    except Exception as exc:
        # Do not persist document contents or model output with the retry state.
        logger.exception("Queued Drive processing failed for file_id=%s", file_id)
        event_ref.set({"status": "retrying"}, merge=True)
        raise HTTPException(status_code=500, detail="Drive file processing will be retried") from exc

    result.pop("metadata", None)
    event_ref.set(
        {
            **result,
            "completed_at": firestore.SERVER_TIMESTAMP,
            # A retry can succeed after recording a transient error. Do not leave
            # that stale failure visible on a completed intake event.
            "error": firestore.DELETE_FIELD,
        },
        merge=True,
    )
    return Response(status_code=204)


router.include_router(authenticated_router)
