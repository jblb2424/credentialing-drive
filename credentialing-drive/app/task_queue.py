import hashlib
import json
import os
import uuid

from fastapi import HTTPException, Request
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud import tasks_v2
from google.oauth2 import id_token
from google.protobuf import duration_pb2

from app.config import CLOUD_TASKS_LOCATION, CLOUD_TASKS_QUEUE, TASK_PROCESSOR_PATH
from app.connections import get_project_id, get_webhook_url


def get_task_processor_url():
    configured_url = os.environ.get("TASK_PROCESSOR_URL")
    if configured_url:
        return configured_url.rstrip("/")

    webhook_url = get_webhook_url().rstrip("/")
    if not webhook_url.endswith("/webhooks/google-drive"):
        raise HTTPException(status_code=500, detail="TASK_PROCESSOR_URL is not configured")
    return webhook_url.removesuffix("/webhooks/google-drive") + TASK_PROCESSOR_PATH


def get_task_service_account():
    configured_account = os.environ.get("TASK_INVOKER_SERVICE_ACCOUNT")
    if configured_account:
        return configured_account
    return f"credentialing-drive-tasks@{get_project_id()}.iam.gserviceaccount.com"


def queue_path(client=None):
    client = client or tasks_v2.CloudTasksClient()
    return client.queue_path(
        get_project_id(),
        os.environ.get("CLOUD_TASKS_LOCATION", CLOUD_TASKS_LOCATION),
        os.environ.get("CLOUD_TASKS_QUEUE", CLOUD_TASKS_QUEUE),
    )


def task_id_for_file(file_id, allow_duplicate=False):
    # Cloud Tasks task IDs are restrictive; hash Drive's opaque file ID for safe idempotency.
    task_id = f"drive-{hashlib.sha256(file_id.encode()).hexdigest()[:40]}"
    return f"{task_id}-{uuid.uuid4().hex[:8]}" if allow_duplicate else task_id


def enqueue_drive_processing_task(file_id, allow_duplicate=False):
    client = tasks_v2.CloudTasksClient()
    parent = queue_path(client)
    task_name = f"{parent}/tasks/{task_id_for_file(file_id, allow_duplicate)}"
    target_url = get_task_processor_url()
    task = {
        "name": task_name,
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": target_url,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"file_id": file_id}).encode(),
            "oidc_token": {
                "service_account_email": get_task_service_account(),
                "audience": target_url,
            },
        },
        "dispatch_deadline": duration_pb2.Duration(seconds=1800),
    }
    return client.create_task(parent=parent, task=task).name


def verify_task_request(request: Request):
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Cloud Tasks authorization")

    try:
        claims = id_token.verify_oauth2_token(
            authorization.removeprefix("Bearer "),
            GoogleAuthRequest(),
            audience=get_task_processor_url(),
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid Cloud Tasks authorization") from exc

    if claims.get("email") != get_task_service_account():
        raise HTTPException(status_code=401, detail="Unexpected Cloud Tasks service account")
