import os
import secrets
import uuid
import logging

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from google.cloud import firestore

from app.connections import (
    create_flow, credentials_to_dict, get_connection, get_drive_service,
    get_webhook_url, update_connection,
)
from app.processing import process_drive_changes, process_drive_document_in_memory

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/")
def home():
    return {"status": "ok", "service": "credentialing-drive-test"}



@router.get("/oauth/google/start")
def google_start():
    flow = create_flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    update_connection({"oauth_state": state, "code_verifier": flow.code_verifier})
    return RedirectResponse(authorization_url)



@router.get("/oauth/google/callback")
def google_callback(request: Request):
    connection = get_connection()
    if request.query_params.get("state") != connection.get("oauth_state"):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    code_verifier = connection.get("code_verifier")
    if not code_verifier:
        raise HTTPException(status_code=400, detail="OAuth authorization has expired")

    flow = create_flow()
    flow.code_verifier = code_verifier
    # Cloud Run terminates TLS before forwarding requests to the container.
    authorization_response = str(request.url.replace(scheme="https"))
    flow.fetch_token(authorization_response=authorization_response)

    update_connection(
        {
            "credentials": credentials_to_dict(flow.credentials),
            "oauth_state": firestore.DELETE_FIELD,
            "code_verifier": firestore.DELETE_FIELD,
        }
    )
    return {"connected": True, "message": "Google Drive connected successfully"}



@router.get("/drive/find-test-folder")
def find_test_folder():
    service = get_drive_service()
    result = (
        service.files()
        .list(
            q=(
                "name='Credentialing Intake' "
                "and mimeType='application/vnd.google-apps.folder' "
                "and trashed=false"
            ),
            spaces="drive",
            fields="files(id,name,parents)",
        )
        .execute()
    )
    folders = result.get("files", [])
    if not folders:
        raise HTTPException(status_code=404, detail="Credentialing Intake folder not found")

    folder = folders[0]
    update_connection({"folder_id": folder["id"]})
    return {"folder": folder}



@router.post("/drive/process/{file_id}")
def process_drive_pdf(file_id: str):
    """Download a Drive document, OCR and interpret it, then release its in-memory bytes."""
    result = process_drive_document_in_memory(get_drive_service(), file_id)
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



@router.post("/drive/watch")
def start_drive_watch():
    connection = get_connection()
    if not connection.get("folder_id"):
        raise HTTPException(status_code=409, detail="Credentialing Intake folder not selected")

    service = get_drive_service(connection)
    page_token = service.changes().getStartPageToken().execute()["startPageToken"]
    channel_id = str(uuid.uuid4())
    channel_token = secrets.token_urlsafe(32)
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

    update_connection(
        {
            "page_token": page_token,
            "channel_id": channel_id,
            "channel_token": channel_token,
            "resource_id": response["resourceId"],
            "channel_expiration": response.get("expiration"),
        }
    )
    return {
        "watching": True,
        "channel_id": channel_id,
        "expires_at": response.get("expiration"),
    }



@router.post("/drive/watch/renew")
def renew_drive_watch(request: Request):
    # Secret Manager values created from command output can include a trailing newline.
    expected_token = os.environ.get("DRIVE_WATCH_RENEWAL_TOKEN", "").strip()
    provided_token = request.headers.get("x-renewal-token", "")
    if not expected_token or not secrets.compare_digest(provided_token, expected_token):
        raise HTTPException(status_code=401, detail="Invalid renewal token")
    return start_drive_watch()



@router.post("/webhooks/google-drive", status_code=204)
async def google_drive_webhook(request: Request):
    connection = get_connection()
    headers = request.headers
    if not secrets.compare_digest(
        headers.get("x-goog-channel-id", ""), connection.get("channel_id", "")
    ) or not secrets.compare_digest(
        headers.get("x-goog-channel-token", ""), connection.get("channel_token", "")
    ):
        raise HTTPException(status_code=401, detail="Invalid Google Drive channel")

    if headers.get("x-goog-resource-id") != connection.get("resource_id"):
        raise HTTPException(status_code=401, detail="Invalid Google Drive resource")

    changes = process_drive_changes(get_drive_service(connection), connection)
    return Response(status_code=204, headers={"X-Detected-Changes": str(len(changes))})
