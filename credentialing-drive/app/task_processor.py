"""The private Cloud Tasks handler used by the document worker service."""

import logging

from fastapi import HTTPException, Request, Response
from google.cloud import firestore

from app.config import (
    ENTITY_COLLECTION,
    EVENT_COLLECTION,
    NATIVE_UPLOAD_SOURCE,
    allow_duplicate_drive_imports,
)
from app.connections import get_drive_service, get_firestore_client
from app.native_uploads import delete_native_upload, native_upload_event_id
from app.processing import process_queued_drive_file, process_queued_native_upload
from app.task_queue import verify_task_request


logger = logging.getLogger(__name__)


async def process_drive_file_task(request: Request):
    """Process one queued document and leave transient failures for Cloud Tasks to retry."""
    verify_task_request(request)
    payload = await request.json()
    source = payload.get("source", "google_drive")
    document_id = payload.get("document_id") or payload.get("file_id")
    entity_id = payload.get("entity_id")
    if source not in {"google_drive", NATIVE_UPLOAD_SOURCE} or not isinstance(document_id, str) or not document_id or not isinstance(entity_id, str) or not entity_id:
        raise HTTPException(status_code=400, detail="Cloud Tasks payload is missing a valid document source")

    event_ref = get_firestore_client().collection(ENTITY_COLLECTION).document(entity_id).collection(
        EVENT_COLLECTION
    ).document(f"drive-{document_id}" if source == "google_drive" else native_upload_event_id(document_id.removeprefix("upload-")))
    event = event_ref.get()
    event_data = event.to_dict() if event.exists else {}
    if (
        not allow_duplicate_drive_imports()
        and event.exists
        and event_data.get("status") in {"processed", "imported", "skipped"}
    ):
        return Response(status_code=204)

    # A successful import is not considered complete until its private staging
    # object has been purged. Retry only deletion instead of reprocessing data.
    if source == NATIVE_UPLOAD_SOURCE and event_data.get("status") == "purging":
        try:
            delete_native_upload(event_data.get("object_name", ""))
        except HTTPException as exc:
            raise HTTPException(status_code=500, detail="Uploaded document deletion will be retried") from exc
        event_ref.set(
            {
                "status": event_data.get("completion_status", "processed"),
                "source_deleted_at": firestore.SERVER_TIMESTAMP,
                "completion_status": firestore.DELETE_FIELD,
                "error": firestore.DELETE_FIELD,
            },
            merge=True,
        )
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
        result = (
            process_queued_drive_file(get_drive_service(entity_id=entity_id), document_id, entity_id)
            if source == "google_drive"
            else process_queued_native_upload(event_data, entity_id)
        )
    except HTTPException as exc:
        if exc.status_code in {404, 413, 415}:
            result = {"status": "skipped", "error": exc.detail}
            if source == NATIVE_UPLOAD_SOURCE:
                _complete_native_upload(event_ref, event_data, result)
            else:
                event_ref.set(result, merge=True)
            return Response(status_code=204)
        event_ref.set({"status": "retrying", "error": exc.detail}, merge=True)
        raise HTTPException(status_code=500, detail="Document processing will be retried") from exc
    except Exception as exc:
        # Do not persist document contents or model output with the retry state.
        logger.exception("Queued document processing failed for source=%s document_id=%s", source, document_id)
        event_ref.set({"status": "retrying"}, merge=True)
        raise HTTPException(status_code=500, detail="Document processing will be retried") from exc

    result.pop("metadata", None)
    if source == NATIVE_UPLOAD_SOURCE:
        _complete_native_upload(event_ref, event_data, result)
    else:
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


def _complete_native_upload(event_ref, event, result):
    """Delete native source material before exposing its intake as complete."""
    completion_status = result.get("status", "processed")
    event_ref.set(
        {
            **result,
            "status": "purging",
            "completion_status": completion_status,
            "completed_at": firestore.SERVER_TIMESTAMP,
            "error": firestore.DELETE_FIELD,
        },
        merge=True,
    )
    try:
        delete_native_upload(event.get("object_name", ""))
    except HTTPException as exc:
        raise HTTPException(status_code=500, detail="Uploaded document deletion will be retried") from exc
    event_ref.set(
        {
            "status": completion_status,
            "source_deleted_at": firestore.SERVER_TIMESTAMP,
            "completion_status": firestore.DELETE_FIELD,
        },
        merge=True,
    )
