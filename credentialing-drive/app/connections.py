import hashlib
import os
import time

from fastapi import HTTPException
from google.cloud import firestore
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.config import CONNECTION_COLLECTION, CONNECTION_ID, DEFAULT_ENTITY_ID, ENTITY_COLLECTION, SCOPES

CONNECTION_LOOKUP_COLLECTION = "drive_connection_lookups"


def get_firestore_client():
    # The credentialing records are stored in this Native-mode database. Keep
    # the environment override for other tenants or environments.
    database = os.environ.get("FIRESTORE_DATABASE", "credentialing-drive")
    return firestore.Client(database=database)


def get_connection_ref(entity_id=None):
    """Return a Drive connection owned by one credentialing client.

    The legacy root document remains readable only when no client is supplied,
    which lets the one-time migration copy an existing sandbox connection.
    """
    client = get_firestore_client()
    if entity_id:
        return client.collection(ENTITY_COLLECTION).document(entity_id).collection(
            CONNECTION_COLLECTION
        ).document(CONNECTION_ID)
    return client.collection(CONNECTION_COLLECTION).document(CONNECTION_ID)


def get_connection(entity_id=None):
    snapshot = get_connection_ref(entity_id).get()
    if not snapshot.exists:
        raise HTTPException(status_code=401, detail="Google Drive not connected")
    return snapshot.to_dict()


def connection_status(entity_id):
    """Return browser-safe connection state without exposing OAuth credentials."""
    snapshot = get_connection_ref(entity_id).get()
    connection = snapshot.to_dict() if snapshot.exists else {}
    folder_id = connection.get("folder_id")
    required_watch_fields = ("page_token", "channel_id", "channel_token", "resource_id")
    watch_is_active = bool(folder_id and all(connection.get(field) for field in required_watch_fields))
    expiration = connection.get("channel_expiration")
    if watch_is_active and expiration:
        try:
            # Google Drive returns channel expiration as Unix milliseconds.
            watch_is_active = int(expiration) > int(time.time() * 1000)
        except (TypeError, ValueError):
            watch_is_active = False
    return {
        "provider": "google_drive",
        "connected": bool(connection.get("credentials")),
        "intake_folder_configured": bool(folder_id),
        "intake_folder_name": connection.get("folder_name"),
        "watching": watch_is_active,
        "intake_ready": bool(connection.get("credentials")) and watch_is_active,
        "watch_expires_at": expiration,
    }


def update_connection(data, entity_id=None):
    get_connection_ref(entity_id).set(data, merge=True)


def connection_lookup_ref(field_name, value):
    """Return a non-guessable direct lookup for a short-lived Drive value."""
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return get_firestore_client().collection(CONNECTION_LOOKUP_COLLECTION).document(
        f"{field_name}-{digest}"
    )


def save_connection_lookup(field_name, value, entity_id):
    if value:
        connection_lookup_ref(field_name, value).set({"entity_id": entity_id})


def delete_connection_lookup(field_name, value):
    if value:
        connection_lookup_ref(field_name, value).delete()


def entity_id_for_connection_snapshot(snapshot):
    """Extract the owning entity from entities/{id}/drive_connections/default."""
    parent = snapshot.reference.parent.parent
    return parent.id if parent and parent.parent and parent.parent.id == ENTITY_COLLECTION else None


def find_connection_by_field(field_name, value):
    """Resolve an OAuth state or Drive channel without a collection-group query."""
    if not value:
        return None, None
    lookup = connection_lookup_ref(field_name, value).get()
    entity_id = (lookup.to_dict() or {}).get("entity_id") if lookup.exists else None
    if not entity_id:
        return None, None

    snapshot = get_connection_ref(entity_id).get()
    connection = snapshot.to_dict() if snapshot.exists else {}
    if connection.get(field_name) == value:
        return entity_id, connection

    # Avoid retaining a stale lookup after a new authorization or watch replaces it.
    connection_lookup_ref(field_name, value).delete()
    return None, None


def list_entity_connections():
    """Return active tenant Drive connections for scheduled watch renewal."""
    connections = []
    for snapshot in get_firestore_client().collection_group(CONNECTION_COLLECTION).stream():
        entity_id = entity_id_for_connection_snapshot(snapshot)
        if entity_id:
            connections.append((entity_id, snapshot.to_dict() or {}))
    return connections


def migrate_legacy_connection(entity_id=DEFAULT_ENTITY_ID):
    """Copy the prior single-client connection into its first tenant namespace."""
    legacy = get_connection_ref().get()
    target = get_connection_ref(entity_id)
    if not legacy.exists:
        return {"migrated": False, "reason": "legacy_connection_not_found"}
    if target.get().exists:
        return {"migrated": False, "reason": "tenant_connection_exists"}
    target.set({**(legacy.to_dict() or {}), "migrated_from_legacy": True}, merge=True)
    return {"migrated": True, "entity_id": entity_id}


def create_flow():
    required_settings = (
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REDIRECT_URI",
    )
    missing_settings = [name for name in required_settings if not os.environ.get(name)]
    if missing_settings:
        raise HTTPException(
            status_code=503,
            detail=(
                "Google Drive connection is not configured. Missing "
                f"{', '.join(missing_settings)}."
            ),
        )
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


def get_drive_service(connection=None, entity_id=None):
    connection = connection or get_connection(entity_id)
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
            status_code=503,
            detail=(
                "Document monitoring is not configured locally. Set "
                "GOOGLE_DRIVE_WEBHOOK_URL to a publicly reachable HTTPS tunnel URL "
                "ending in /webhooks/google-drive."
            ),
        )
    return url


def get_project_id():
    project_id = os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        raise HTTPException(status_code=500, detail="GCP_PROJECT_ID is not configured")
    return project_id
