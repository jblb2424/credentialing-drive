import os

from fastapi import HTTPException
from google.cloud import firestore
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.config import CONNECTION_COLLECTION, CONNECTION_ID, SCOPES



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
