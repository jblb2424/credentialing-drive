import io
import json
import logging
import os
import csv
import re
import secrets
import uuid
from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from google.api_core.exceptions import AlreadyExists
from google.api_core.client_options import ClientOptions
from google import genai
from google.cloud import documentai, firestore
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from openpyxl import load_workbook

app = FastAPI()
logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

CONNECTION_ID = "default"
CONNECTION_COLLECTION = "drive_connections"
EVENT_COLLECTION = "drive_change_events"
SOURCE_DOCUMENT_COLLECTION = "source_documents"
SPREADSHEET_IMPORT_COLLECTION = "spreadsheet_imports"
PROVIDER_COLLECTION = "providers"
PROVIDER_IDENTITY_COLLECTION = "provider_identities"
PDF_MIME_TYPE = "application/pdf"
CSV_MIME_TYPE = "text/csv"
XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
GOOGLE_SHEETS_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
SPREADSHEET_MIME_TYPES = {CSV_MIME_TYPE, XLSX_MIME_TYPE, GOOGLE_SHEETS_MIME_TYPE}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_SPREADSHEET_BYTES = 10 * 1024 * 1024
MAX_GEMINI_INPUT_CHARS = 100_000

HEADER_ALIASES = {
    "entity": "entity_name",
    "entity name": "entity_name",
    "group": "group_name",
    "group name": "group_name",
    "provider name": "provider_name",
    "provider full name": "provider_name",
    "first name": "first_name",
    "last name": "last_name",
    "middle name": "middle_name",
    "npi number": "npi",
    "provider npi": "npi",
    "credential": "credentials",
    "locations": "locations",
    "payers": "payers",
    "insurances": "payers",
}


def get_connection_ref():
    database = os.environ.get("FIRESTORE_DATABASE", "healthcare-credentialing")
    client = firestore.Client(database=database)
    return client.collection(CONNECTION_COLLECTION).document(CONNECTION_ID)


def get_connection():
    snapshot = get_connection_ref().get()
    if not snapshot.exists:
        raise HTTPException(status_code=401, detail="Google Drive not connected")
    return snapshot.to_dict()


def update_connection(data):
    get_connection_ref().set(data, merge=True)


def create_flow():
    return Flow.from_client_config(
        {
            "web": {
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        redirect_uri=os.environ["GOOGLE_REDIRECT_URI"],
    )


def credentials_to_dict(credentials):
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes),
    }


def get_drive_service(connection=None):
    connection = connection or get_connection()
    credentials_data = connection.get("credentials")
    if not credentials_data:
        raise HTTPException(status_code=401, detail="Google Drive not connected")

    credentials = Credentials(
        token=credentials_data["token"],
        refresh_token=credentials_data["refresh_token"],
        token_uri=credentials_data["token_uri"],
        client_id=credentials_data["client_id"],
        client_secret=credentials_data["client_secret"],
        scopes=credentials_data["scopes"],
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def get_webhook_url():
    url = os.environ.get("GOOGLE_DRIVE_WEBHOOK_URL")
    if not url:
        raise HTTPException(
            status_code=500,
            detail="GOOGLE_DRIVE_WEBHOOK_URL is not configured",
        )
    return url


def get_project_id():
    project_id = os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        raise HTTPException(status_code=500, detail="GCP_PROJECT_ID is not configured")
    return project_id


def download_drive_pdf(service, file_id):
    metadata = (
        service.files()
        .get(fileId=file_id, fields="id,name,mimeType,size,trashed")
        .execute()
    )
    if metadata.get("trashed"):
        raise HTTPException(status_code=404, detail="Drive file is trashed")
    if metadata.get("mimeType") != PDF_MIME_TYPE:
        raise HTTPException(status_code=415, detail="Only PDF files are supported")

    file_size = int(metadata.get("size", 0))
    if file_size > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="PDF exceeds the 20 MB processing limit")

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, service.files().get_media(fileId=file_id))
    done = False
    while not done:
        _, done = downloader.next_chunk()

    pdf_bytes = buffer.getvalue()
    if len(pdf_bytes) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="PDF exceeds the 20 MB processing limit")
    return metadata, pdf_bytes


def download_drive_spreadsheet(service, file_id):
    metadata = (
        service.files()
        .get(fileId=file_id, fields="id,name,mimeType,size,trashed")
        .execute()
    )
    if metadata.get("trashed"):
        raise HTTPException(status_code=404, detail="Drive file is trashed")
    if metadata.get("mimeType") not in SPREADSHEET_MIME_TYPES:
        raise HTTPException(status_code=415, detail="Unsupported spreadsheet format")
    if int(metadata.get("size", 0)) > MAX_SPREADSHEET_BYTES:
        raise HTTPException(
            status_code=413, detail="Spreadsheet exceeds the 10 MB processing limit"
        )

    if metadata["mimeType"] == GOOGLE_SHEETS_MIME_TYPE:
        request = service.files().export_media(fileId=file_id, mimeType=CSV_MIME_TYPE)
    else:
        request = service.files().get_media(fileId=file_id)

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    spreadsheet_bytes = buffer.getvalue()
    if len(spreadsheet_bytes) > MAX_SPREADSHEET_BYTES:
        raise HTTPException(
            status_code=413, detail="Spreadsheet exceeds the 10 MB processing limit"
        )
    return metadata, spreadsheet_bytes


def normalize_header(value):
    header = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return HEADER_ALIASES.get(header, re.sub(r"[^a-z0-9]+", "_", header).strip("_"))


def normalize_cell(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value.strip() if isinstance(value, str) else value


def parse_rows(headers, rows):
    normalized_headers = [normalize_header(header) for header in headers]
    if not any(normalized_headers):
        raise HTTPException(status_code=422, detail="Spreadsheet must include a header row")

    parsed_rows = []
    for row_number, values in rows:
        fields = {
            header: normalize_cell(value)
            for header, value in zip(normalized_headers, values)
            if header and value not in (None, "")
        }
        if fields:
            parsed_rows.append({"row_number": row_number, "fields": fields})
    return parsed_rows


def parse_spreadsheet(spreadsheet_bytes, mime_type):
    if mime_type in {CSV_MIME_TYPE, GOOGLE_SHEETS_MIME_TYPE}:
        csv_rows = list(csv.reader(io.StringIO(spreadsheet_bytes.decode("utf-8-sig"))))
        if not csv_rows:
            return []
        return parse_rows(csv_rows[0], enumerate(csv_rows[1:], start=2))

    workbook = load_workbook(io.BytesIO(spreadsheet_bytes), read_only=True, data_only=True)
    worksheet = workbook.active
    rows = worksheet.iter_rows(values_only=True)
    headers = next(rows, None)
    if headers is None:
        return []
    return parse_rows(headers, enumerate(rows, start=2))


def split_list(value):
    if not value:
        return []
    return [item.strip() for item in re.split(r"[;,|]", str(value)) if item.strip()]


def provider_fields(fields):
    provider_name = fields.get("provider_name") or " ".join(
        str(fields.get(key, "")).strip()
        for key in ("first_name", "middle_name", "last_name")
        if fields.get(key)
    )
    return normalize_provider_data(
        {
            "document_type": "Provider Onboarding Spreadsheet",
            "entity_name": fields.get("entity_name"),
            "group_name": fields.get("group_name"),
            "provider": {
                "name": provider_name or None,
                "npi": str(fields["npi"]) if fields.get("npi") else None,
                "credentials": fields.get("credentials"),
            },
            "locations": split_list(fields.get("locations")),
            "payers": split_list(fields.get("payers")),
            "licenses": split_list(fields.get("licenses")),
            "expiration_dates": split_list(fields.get("expiration_dates")),
            "summary": None,
        }
    )


def save_spreadsheet_import(metadata, rows):
    database = os.environ.get("FIRESTORE_DATABASE", "healthcare-credentialing")
    client = firestore.Client(database=database)
    import_ref = client.collection(SPREADSHEET_IMPORT_COLLECTION).document(
        f"drive-{metadata['id']}"
    )
    import_ref.set(
        {
            "drive_file_id": metadata["id"],
            "file_name": metadata.get("name"),
            "mime_type": metadata.get("mimeType"),
            "status": "imported_pending_assignment",
            "provider_row_count": len(rows),
            "imported_at": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )

    for row in rows:
        source_document_id = f"drive-{metadata['id']}-row-{row['row_number']}"
        normalized_provider = provider_fields(row["fields"])
        provider_id = upsert_normalized_provider(normalized_provider, source_document_id)
        client.collection(SOURCE_DOCUMENT_COLLECTION).document(source_document_id).set(
            {
                "drive_file_id": metadata["id"],
                "file_name": metadata.get("name"),
                "mime_type": metadata.get("mimeType"),
                "spreadsheet_row_number": row["row_number"],
                "status": "normalized" if provider_id else "pending_provider_identity",
                "structured_data": normalized_provider,
                "provider_id": provider_id,
                "processed_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        import_ref.collection("provider_rows").document(f"row-{row['row_number']}").set(
            {
                "row_number": row["row_number"],
                "provider_id": provider_id,
                "source_fields": row["fields"],
                "status": "normalized" if provider_id else "pending_provider_identity",
            },
            merge=True,
        )


def process_drive_spreadsheet_in_memory(service, file_id):
    metadata, spreadsheet_bytes = download_drive_spreadsheet(service, file_id)
    try:
        rows = parse_spreadsheet(spreadsheet_bytes, metadata["mimeType"])
        save_spreadsheet_import(metadata, rows)
    finally:
        # Raw spreadsheet bytes are never persisted.
        spreadsheet_bytes = b""
    return {"metadata": metadata, "provider_row_count": len(rows)}


def extract_text_with_document_ai(pdf_bytes):
    project_id = get_project_id()
    location = os.environ.get("DOCUMENT_AI_LOCATION", "us")
    processor_id = os.environ.get("DOCUMENT_AI_PROCESSOR_ID")
    if not processor_id:
        raise HTTPException(status_code=500, detail="DOCUMENT_AI_PROCESSOR_ID is not configured")

    client = documentai.DocumentProcessorServiceClient(
        client_options=ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
    )
    request = documentai.ProcessRequest(
        name=client.processor_path(project_id, location, processor_id),
        raw_document=documentai.RawDocument(content=pdf_bytes, mime_type=PDF_MIME_TYPE),
    )
    document = client.process_document(request=request).document
    return document.text, len(document.pages)


def interpret_text_with_gemini(ocr_text):
    if not ocr_text:
        return {"document_type": "unknown", "summary": "No text extracted"}

    prompt = """You interpret healthcare credentialing documents. Return JSON only with these keys:
document_type, entity_name, group_name, provider, locations, payers, licenses, expiration_dates, and summary.
`provider` must be an object with name, npi, and credentials. `locations`, `payers`, and `licenses` must be arrays.
Use null or empty arrays when a value is not present. Do not infer values that are not supported by the text.

OCR text:
"""
    client = genai.Client(
        vertexai=True,
        project=get_project_id(),
        location=os.environ.get("VERTEX_AI_LOCATION", "global"),
    )
    response = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=prompt + ocr_text[:MAX_GEMINI_INPUT_CHARS],
        config={"response_mime_type": "application/json", "temperature": 0},
    )
    return response.text


def parse_gemini_extraction(interpretation):
    if isinstance(interpretation, dict):
        return interpretation
    try:
        parsed = json.loads(interpretation)
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="Gemini returned invalid structured output")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="Gemini returned an unexpected response")
    return parsed


def normalized_key(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def normalize_provider_data(extraction):
    provider = extraction.get("provider") or {}
    if not isinstance(provider, dict):
        provider = {"name": str(provider)}

    name = provider.get("name") or extraction.get("provider_name")
    npi = provider.get("npi") or extraction.get("npi")
    return {
        "document_type": extraction.get("document_type") or "unknown",
        "entity_name": extraction.get("entity_name"),
        "group_name": extraction.get("group_name"),
        "provider": {
            "name": name,
            "npi": str(npi) if npi else None,
            "credentials": provider.get("credentials") or extraction.get("credentials"),
        },
        "locations": extraction.get("locations") or [],
        "payers": extraction.get("payers") or [],
        "licenses": extraction.get("licenses") or [],
        "expiration_dates": extraction.get("expiration_dates") or [],
        "summary": extraction.get("summary"),
    }


def resolve_provider_id(client, provider):
    name_key = normalized_key(provider["provider"].get("name"))
    entity_key = normalized_key(provider.get("entity_name"))
    npi = re.sub(r"\D", "", provider["provider"].get("npi") or "")
    if not npi and not name_key:
        return None

    identity_keys = []
    if npi:
        identity_keys.append(f"npi-{npi}")
    if name_key:
        identity_keys.append(f"name-{entity_key or 'unknown'}-{name_key}")

    for identity_key in identity_keys:
        snapshot = client.collection(PROVIDER_IDENTITY_COLLECTION).document(identity_key).get()
        if snapshot.exists:
            provider_id = snapshot.to_dict()["provider_id"]
            for alias_key in identity_keys:
                client.collection(PROVIDER_IDENTITY_COLLECTION).document(alias_key).set(
                    {"provider_id": provider_id}, merge=True
                )
            return provider_id

    provider_id = identity_keys[0]
    for identity_key in identity_keys:
        client.collection(PROVIDER_IDENTITY_COLLECTION).document(identity_key).set(
            {"provider_id": provider_id}, merge=True
        )
    return provider_id


def merge_unique(existing, incoming):
    values = list(existing or [])
    for value in incoming or []:
        if value and value not in values:
            values.append(value)
    return values


def upsert_normalized_provider(provider, source_document_id):
    database = os.environ.get("FIRESTORE_DATABASE", "healthcare-credentialing")
    client = firestore.Client(database=database)
    provider_id = resolve_provider_id(client, provider)
    if not provider_id:
        return None

    provider_ref = client.collection(PROVIDER_COLLECTION).document(provider_id)
    existing = provider_ref.get().to_dict() or {}
    existing_profile = existing.get("provider") or {}
    incoming_profile = provider["provider"]
    merged_profile = {
        key: incoming_profile.get(key) or existing_profile.get(key)
        for key in ("name", "npi", "credentials")
    }
    provider_ref.set(
        {
            "document_type": provider.get("document_type") or existing.get("document_type"),
            "entity_name": provider.get("entity_name") or existing.get("entity_name"),
            "group_name": provider.get("group_name") or existing.get("group_name"),
            "provider": merged_profile,
            "locations": merge_unique(existing.get("locations"), provider.get("locations")),
            "payers": merge_unique(existing.get("payers"), provider.get("payers")),
            "licenses": merge_unique(existing.get("licenses"), provider.get("licenses")),
            "expiration_dates": merge_unique(
                existing.get("expiration_dates"), provider.get("expiration_dates")
            ),
        },
        merge=True,
    )
    provider_ref.collection("sources").document(source_document_id).set(
        {"source_document_id": source_document_id, "linked_at": firestore.SERVER_TIMESTAMP},
        merge=True,
    )
    return provider_id


def save_structured_document(metadata, page_count, extraction):
    database = os.environ.get("FIRESTORE_DATABASE", "healthcare-credentialing")
    client = firestore.Client(database=database)
    source_document_id = f"drive-{metadata['id']}"
    provider = normalize_provider_data(extraction)
    provider_id = upsert_normalized_provider(provider, source_document_id)
    # A Drive file ID is stable across retries, so this write is naturally idempotent.
    client.collection(SOURCE_DOCUMENT_COLLECTION).document(source_document_id).set(
        {
            "drive_file_id": metadata["id"],
            "file_name": metadata.get("name"),
            "mime_type": metadata.get("mimeType"),
            "page_count": page_count,
            "status": "normalized" if provider_id else "extracted_pending_provider",
            "extraction_version": "v1",
            "structured_data": extraction,
            "provider_id": provider_id,
            "processed_at": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )
    return provider_id


def process_drive_pdf_in_memory(service, file_id):
    metadata, pdf_bytes = download_drive_pdf(service, file_id)
    try:
        extracted_text, page_count = extract_text_with_document_ai(pdf_bytes)
        interpretation = interpret_text_with_gemini(extracted_text)
    finally:
        # Release the raw source document immediately after managed processing.
        pdf_bytes = b""

    extraction = parse_gemini_extraction(interpretation)
    save_structured_document(metadata, page_count, extraction)
    return {
        "metadata": metadata,
        "page_count": page_count,
        "extracted_text": extracted_text,
        "gemini_interpretation": extraction,
    }


def process_drive_changes(service, connection):
    page_token = connection.get("page_token")
    folder_id = connection.get("folder_id")
    if not page_token or not folder_id:
        raise HTTPException(status_code=409, detail="Drive watch is not configured")

    database = os.environ.get("FIRESTORE_DATABASE", "healthcare-credentialing")
    client = firestore.Client(database=database)
    detected_changes = []

    while page_token:
        result = (
            service.changes()
            .list(
                pageToken=page_token,
                spaces="drive",
                fields=(
                    "nextPageToken,newStartPageToken,"
                    "changes(changeType,fileId,removed,file(id,name,mimeType,parents,trashed))"
                ),
            )
            .execute()
        )

        for change in result.get("changes", []):
            file_data = change.get("file") or {}
            if folder_id not in file_data.get("parents", []) or file_data.get("trashed"):
                continue

            file_id = change.get("fileId")
            if not file_id:
                continue

            event = {
                "file_id": file_id,
                "file_name": file_data.get("name"),
                "mime_type": file_data.get("mimeType"),
                "change_type": change.get("changeType"),
                "status": "detected",
            }
            # Use the Drive file ID as an idempotency key across overlapping watch channels.
            event_ref = client.collection(EVENT_COLLECTION).document(f"drive-{file_id}")
            try:
                event_ref.create(event)
            except AlreadyExists:
                continue

            if event["mime_type"] in SPREADSHEET_MIME_TYPES:
                try:
                    result = process_drive_spreadsheet_in_memory(service, file_id)
                except Exception:
                    logger.exception("Spreadsheet import failed for Drive file_id=%s", file_id)
                    event_ref.set({"status": "failed"}, merge=True)
                    detected_changes.append({**event, "status": "failed"})
                    continue

                event_ref.set(
                    {
                        "status": "imported",
                        "provider_row_count": result["provider_row_count"],
                    },
                    merge=True,
                )
                detected_changes.append({**event, "status": "imported"})
                continue

            if event["mime_type"] != PDF_MIME_TYPE:
                event_ref.set({"status": "skipped"}, merge=True)
                detected_changes.append({**event, "status": "skipped"})
                continue

            try:
                result = process_drive_pdf_in_memory(service, file_id)
            except Exception:
                # Preserve no document contents or model output in logs or Firestore.
                logger.exception("Automatic PDF processing failed for Drive file_id=%s", file_id)
                event_ref.set({"status": "failed"}, merge=True)
                detected_changes.append({**event, "status": "failed"})
                continue

            event_ref.set(
                {
                    "status": "processed",
                    "page_count": result["page_count"],
                    "extracted_character_count": len(result["extracted_text"]),
                    "gemini_response_character_count": len(
                        str(result["gemini_interpretation"])
                    ),
                },
                merge=True,
            )
            logger.info(
                "Automatically processed Drive PDF file_id=%s pages=%s extracted_characters=%s",
                file_id,
                result["page_count"],
                len(result["extracted_text"]),
            )
            detected_changes.append({**event, "status": "processed"})

        page_token = result.get("nextPageToken")
        if page_token:
            update_connection({"page_token": page_token})
        elif result.get("newStartPageToken"):
            update_connection({"page_token": result["newStartPageToken"]})

    return detected_changes


@app.get("/")
def home():
    return {"status": "ok", "service": "credentialing-drive-test"}


@app.get("/oauth/google/start")
def google_start():
    flow = create_flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    update_connection({"oauth_state": state, "code_verifier": flow.code_verifier})
    return RedirectResponse(authorization_url)


@app.get("/oauth/google/callback")
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


@app.get("/drive/find-test-folder")
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


@app.post("/drive/process/{file_id}")
def process_drive_pdf(file_id: str):
    """Download a Drive PDF, OCR and interpret it, then release its in-memory bytes."""
    result = process_drive_pdf_in_memory(get_drive_service(), file_id)
    metadata = result["metadata"]

    logger.info(
        "Processed Drive PDF file_id=%s name=%s pages=%s extracted_characters=%s",
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


@app.post("/drive/watch")
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


@app.post("/drive/watch/renew")
def renew_drive_watch(request: Request):
    # Secret Manager values created from command output can include a trailing newline.
    expected_token = os.environ.get("DRIVE_WATCH_RENEWAL_TOKEN", "").strip()
    provided_token = request.headers.get("x-renewal-token", "")
    if not expected_token or not secrets.compare_digest(provided_token, expected_token):
        raise HTTPException(status_code=401, detail="Invalid renewal token")
    return start_drive_watch()


@app.post("/webhooks/google-drive", status_code=204)
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
