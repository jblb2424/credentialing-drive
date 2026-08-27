import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

app = FastAPI()

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "openid",
    "email",
]

STATE = {
    "oauth_state": None,
    "credentials": None,
    "folder_id": None,
    "page_token": None,
    "channel_id": None,
    "channel_token": None,
    "resource_id": None,
}


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


def get_drive_service():
    credentials_data = STATE["credentials"]

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


@app.get("/")
def home():
    return {
        "status": "ok",
        "service": "credentialing-drive-test",
    }


@app.get("/oauth/google/start")
def google_start():
    flow = create_flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    STATE["oauth_state"] = state
    return RedirectResponse(authorization_url)


@app.get("/oauth/google/callback")
def google_callback(request: Request):
    incoming_state = request.query_params.get("state")

    if incoming_state != STATE["oauth_state"]:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    flow = create_flow()
    # Cloud Run terminates TLS before forwarding requests to the container.
    authorization_response = str(request.url.replace(scheme="https"))
    flow.fetch_token(authorization_response=authorization_response)

    credentials = flow.credentials
    STATE["credentials"] = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes),
    }

    return {
        "connected": True,
        "message": "Google Drive connected successfully",
    }


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
        raise HTTPException(
            status_code=404,
            detail="Credentialing Intake folder not found",
        )

    folder = folders[0]
    STATE["folder_id"] = folder["id"]
    return {"folder": folder}


@app.post("/webhooks/google-drive")
async def google_drive_webhook(request: Request):
    print("Google Drive webhook received")
    return {
        "received": True,
    }
