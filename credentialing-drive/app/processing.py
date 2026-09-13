import json
import logging

from fastapi import HTTPException
from google.api_core.exceptions import AlreadyExists

from app.config import DOCUMENT_MIME_TYPES, EVENT_COLLECTION, SPREADSHEET_MIME_TYPES
from app.connections import get_firestore_client, update_connection
from app.drive_files import (
    download_drive_document, download_drive_spreadsheet, get_drive_file_metadata,
    parse_spreadsheet,
)
from app.extraction import extract_document_text
from app.gemini import (
    classify_document_category, interpret_spreadsheet_with_gemini,
    interpret_text_with_gemini, parse_gemini_extraction,
)
from app.providers import (
    normalize_provider_data,
    upsert_normalized_group,
    upsert_normalized_provider,
)
from app.task_queue import enqueue_drive_processing_task

logger = logging.getLogger(__name__)



def save_spreadsheet_providers(metadata, providers, document_category):
    for extracted_provider in providers:
        extracted_provider.pop("source_row_numbers", None)
        normalized_provider = normalize_provider_data(extracted_provider)
        upsert_normalized_group(normalized_provider)
        upsert_normalized_provider(normalized_provider, metadata, document_category)


def process_drive_spreadsheet_in_memory(service, file_id):
    metadata, spreadsheet_bytes = download_drive_spreadsheet(service, file_id)
    try:
        rows = parse_spreadsheet(spreadsheet_bytes, metadata["mimeType"])
        document_category = classify_document_category(
            metadata, json.dumps(rows, default=str)
        )
        providers = interpret_spreadsheet_with_gemini(rows)
        save_spreadsheet_providers(metadata, providers, document_category)
    finally:
        # Raw spreadsheet bytes are never persisted.
        spreadsheet_bytes = b""
    return {
        "metadata": metadata,
        "document_category": document_category,
        "source_row_count": len(rows),
        "provider_count": len(providers),
    }


def save_document_provider(metadata, extraction, document_category):
    provider = normalize_provider_data(extraction)
    group_id = upsert_normalized_group(provider)
    provider_id = upsert_normalized_provider(provider, metadata, document_category)
    return {"group_id": group_id, "provider_id": provider_id}


def process_drive_document_in_memory(service, file_id):
    metadata, document_bytes = download_drive_document(service, file_id)
    try:
        extracted_text, page_count = extract_document_text(
            document_bytes, metadata["mimeType"]
        )
        document_category = classify_document_category(metadata, extracted_text)
        interpretation = interpret_text_with_gemini(extracted_text)
    finally:
        # Release the raw source document immediately after managed processing.
        document_bytes = b""

    extraction = parse_gemini_extraction(interpretation)
    save_document_provider(metadata, extraction, document_category)
    return {
        "metadata": metadata,
        "document_category": document_category,
        "page_count": page_count,
        "extracted_text": extracted_text,
        "gemini_interpretation": extraction,
    }


def process_queued_drive_file(service, file_id):
    """Process exactly one Drive file after Cloud Tasks dispatches its job."""
    metadata = get_drive_file_metadata(service, file_id)
    mime_type = metadata.get("mimeType")
    if metadata.get("trashed") or mime_type not in DOCUMENT_MIME_TYPES | SPREADSHEET_MIME_TYPES:
        return {"status": "skipped", "metadata": metadata}

    if mime_type in SPREADSHEET_MIME_TYPES:
        result = process_drive_spreadsheet_in_memory(service, file_id)
        return {
            "status": "imported",
            "metadata": result["metadata"],
            "source_row_count": result["source_row_count"],
            "provider_count": result["provider_count"],
        }

    result = process_drive_document_in_memory(service, file_id)
    return {
        "status": "processed",
        "metadata": result["metadata"],
        "page_count": result["page_count"],
        "extracted_character_count": len(result["extracted_text"]),
        "gemini_response_character_count": len(str(result["gemini_interpretation"])),
    }


def process_drive_changes(connection):
    page_token = connection.get("page_token")
    folder_id = connection.get("folder_id")
    if not page_token or not folder_id:
        raise HTTPException(status_code=409, detail="Drive watch is not configured")

    client = get_firestore_client()
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

            try:
                task_name = enqueue_drive_processing_task(file_id)
            except Exception:
                logger.exception("Could not enqueue Drive file_id=%s", file_id)
                event_ref.set({"status": "enqueue_failed"}, merge=True)
                detected_changes.append({**event, "status": "enqueue_failed"})
                continue

            event_ref.set(
                {
                    "status": "queued",
                    "task_name": task_name,
                },
                merge=True,
            )
            detected_changes.append({**event, "status": "queued"})

        page_token = result.get("nextPageToken")
        if page_token:
            update_connection({"page_token": page_token})
        elif result.get("newStartPageToken"):
            update_connection({"page_token": result["newStartPageToken"]})

    return detected_changes
