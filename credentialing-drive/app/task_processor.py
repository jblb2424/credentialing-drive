"""The private Cloud Tasks handler used by the document worker service."""

import logging

from fastapi import HTTPException, Request, Response
from google.cloud import firestore

from app.config import (
    ENTITY_COLLECTION,
    EVENT_COLLECTION,
    allow_duplicate_drive_imports,
)
from app.connections import get_drive_service, get_firestore_client
from app.processing import process_queued_drive_file
from app.task_queue import verify_task_request


logger = logging.getLogger(__name__)


async def process_drive_file_task(request: Request):
    """Process one queued file and leave transient failures for Cloud Tasks to retry."""
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
