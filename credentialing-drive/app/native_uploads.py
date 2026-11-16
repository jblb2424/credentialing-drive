"""Ephemeral Cloud Storage staging for documents uploaded from the dashboard.

Native uploads never become a document archive. The object exists only while a
Cloud Tasks job needs it; the worker deletes it before completing the intake
event. A bucket lifecycle policy provides a backstop for abandoned uploads.
"""

import os
import re
import uuid
from datetime import timedelta
from pathlib import PurePath

import google.auth
from fastapi import HTTPException
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud import storage

from app.config import (
    DOCUMENT_MIME_TYPES,
    MAX_DOCUMENT_BYTES,
    MAX_SPREADSHEET_BYTES,
    NATIVE_UPLOAD_SOURCE,
    NATIVE_UPLOAD_URL_TTL_SECONDS,
    SPREADSHEET_MIME_TYPES,
)


EVENT_PREFIX = "upload-"
_EXTENSION_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".rtf": "text/rtf",
    ".html": "text/html",
    ".htm": "text/html",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_NATIVE_MIME_TYPES = (DOCUMENT_MIME_TYPES | SPREADSHEET_MIME_TYPES) - {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
}


def native_upload_event_id(upload_id):
    return f"{EVENT_PREFIX}{upload_id}"


def native_upload_bucket_name():
    return os.environ.get("NATIVE_UPLOAD_BUCKET", "").strip()


def native_upload_bucket():
    name = native_upload_bucket_name()
    if not name:
        raise HTTPException(status_code=503, detail="Native document uploads are not configured")
    return storage.Client().bucket(name)


def _safe_filename(file_name):
    original = PurePath(str(file_name or "")).name.strip()
    if not original or len(original) > 255 or any(ord(char) < 32 for char in original):
        raise HTTPException(status_code=400, detail="Provide a valid document file name")
    return original


def native_upload_mime_type(file_name, supplied_mime_type):
    supplied = str(supplied_mime_type or "").split(";", 1)[0].strip().lower()
    inferred = _EXTENSION_MIME_TYPES.get(PurePath(file_name).suffix.lower())
    mime_type = supplied if supplied in _NATIVE_MIME_TYPES else inferred
    if mime_type not in _NATIVE_MIME_TYPES:
        raise HTTPException(status_code=415, detail="This file type is not supported for credentialing intake")
    return mime_type


def native_upload_max_bytes(mime_type):
    return MAX_SPREADSHEET_BYTES if mime_type in SPREADSHEET_MIME_TYPES else MAX_DOCUMENT_BYTES


def validate_native_upload(file_name, mime_type, size_bytes):
    file_name = _safe_filename(file_name)
    mime_type = native_upload_mime_type(file_name, mime_type)
    try:
        size_bytes = int(size_bytes)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Provide a valid file size") from exc
    max_bytes = native_upload_max_bytes(mime_type)
    if size_bytes <= 0 or size_bytes > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"{file_name} exceeds the {max_bytes // (1024 * 1024)} MB processing limit",
        )
    return file_name, mime_type, size_bytes


def native_upload_object_name(entity_id, upload_id, file_name):
    # Keep the user-facing name in Firestore; this opaque, sanitized path is
    # suitable for Cloud Storage and makes an object unguessable across clients.
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", file_name).strip(".-") or "document"
    return f"native-uploads/{entity_id}/{upload_id}/{safe_name}"


def signed_native_upload_url(object_name, mime_type):
    blob = native_upload_bucket().blob(object_name)
    credentials, _ = google.auth.default()
    if not credentials.valid:
        credentials.refresh(GoogleAuthRequest())
    service_account_email = getattr(credentials, "service_account_email", None)
    options = {
        "version": "v4",
        "expiration": timedelta(seconds=NATIVE_UPLOAD_URL_TTL_SECONDS),
        "method": "PUT",
        "content_type": mime_type,
    }
    # Cloud Run uses workload credentials rather than a downloadable key. The
    # Storage library signs through IAM when these values are supplied.
    if service_account_email and credentials.token:
        options.update({"service_account_email": service_account_email, "access_token": credentials.token})
    try:
        return blob.generate_signed_url(**options)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="A secure upload URL could not be created") from exc


def read_native_upload(object_name):
    blob = native_upload_bucket().blob(object_name)
    try:
        blob.reload()
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Uploaded document was not found") from exc
    return blob, blob.download_as_bytes()


def delete_native_upload(object_name):
    try:
        native_upload_bucket().blob(object_name).delete()
    except Exception as exc:
        # The worker retries deletion before completing the intake event.
        raise HTTPException(status_code=502, detail="Uploaded document could not be securely deleted") from exc


def native_upload_event(upload_id, file_name, mime_type, size_bytes, entity_id):
    object_name = native_upload_object_name(entity_id, upload_id, file_name)
    return {
        "id": native_upload_event_id(upload_id),
        "source": NATIVE_UPLOAD_SOURCE,
        "file_name": file_name,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "object_name": object_name,
        "status": "awaiting_upload",
    }
