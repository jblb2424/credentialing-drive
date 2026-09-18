# Credentialing Drive

Credentialing Drive ingests healthcare credentialing files from a Google Drive folder, extracts structured data with Document AI and Gemini, and presents the resulting provider, practice, issue, and expiration data in a FastAPI web application.

The application lives in [`credentialing-drive/`](credentialing-drive).

## What It Does

- Connects to a Google Drive account through OAuth and watches a `Credentialing Intake` folder.
- Queues new Drive files with Cloud Tasks so bulk uploads are processed safely.
- Processes PDFs and supported images with Document AI OCR; handles spreadsheets and text-based files in memory.
- Uses Gemini to classify files and normalize provider, practice, license, education, payer, and expiration data.
- Stores normalized records and provenance revisions in Firestore.
- Provides a web dashboard and JSON endpoints for providers, practices, issues, and expirations.

## Architecture

```text
Google Drive -> Drive change webhook -> Cloud Tasks -> FastAPI worker
                                                    |
                              Document AI + Gemini -+
                                                    v
                                              Firestore
                                                    |
                                             FastAPI dashboard
```

The Drive bytes are processed in memory and are not persisted by the application. Firestore stores normalized structured data, field revisions, and source-file lineage.

## Prerequisites

- Python 3.12 or later
- A Google Cloud project with billing enabled
- Google Cloud CLI authenticated to that project
- A Google OAuth web-client credential
- A Firestore database
- A Document AI OCR processor
- A Cloud Tasks queue and an invoker service account
- APIs enabled: Cloud Run, Cloud Build, Firestore, Cloud Tasks, Document AI, Vertex AI, Google Drive API, and Secret Manager

## Local Setup

From the repository root:

```bash
cd credentialing-drive
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` with resources from your own GCP project. It is ignored by Git.

```bash
set -a
source .env
set +a
uvicorn main:app --reload --port 8080
```

Open `http://localhost:8080` to load the dashboard. The JSON API is served by the same application.

### Required Configuration

Use [`credentialing-drive/.env.example`](credentialing-drive/.env.example) as the canonical reference. At minimum, configure:

| Variable | Purpose |
| --- | --- |
| `GCP_PROJECT_ID` | Google Cloud project ID. |
| `FIRESTORE_DATABASE` | Firestore database ID. |
| `GOOGLE_CLIENT_ID` | OAuth web-client ID. |
| `GOOGLE_CLIENT_SECRET` | OAuth web-client secret. |
| `GOOGLE_REDIRECT_URI` | OAuth callback, such as `http://localhost:8080/oauth/google/callback`. |
| `GOOGLE_DRIVE_WEBHOOK_URL` | Public webhook URL ending in `/webhooks/google-drive`. |
| `DOCUMENT_AI_PROCESSOR_ID` | Document AI OCR processor ID. |
| `DRIVE_WATCH_RENEWAL_TOKEN` | Random secret used to renew Drive watches and protect internal maintenance endpoints. |

For local dashboard work, Google ADC credentials are required for Firestore and other GCP clients:

```bash
gcloud auth application-default login
```

## Connect a Drive Folder

1. In Google Cloud Console, add the callback URL to the OAuth client's authorized redirect URIs.
2. Start the app and browse to `/oauth/google/start`.
3. Grant readonly Drive permission.
4. Create a Drive folder named `Credentialing Intake`, then open `/drive/find-test-folder`.
5. Deploy the application to a public URL before starting a Drive watch. Google Drive cannot send change notifications to localhost.
6. Send `POST /drive/watch` to create the watch.

Each Drive watch expires, so create a Cloud Scheduler job that calls `POST /drive/watch/renew` with the `x-renewal-token` header. Keep that token in Secret Manager, not source control.

## Tests

```bash
cd credentialing-drive
python3 -m unittest discover -s tests -v
python3 -m compileall -q app main.py
```

## Key Routes

| Route | Purpose |
| --- | --- |
| `/` | Dashboard overview. |
| `/providers/view?entity=dummy-client` | Provider directory. |
| `/practices/view?entity=dummy-client` | Practice directory. |
| `/issues/view?entity=dummy-client` | Open issue list. |
| `/expirations/view?entity=dummy-client` | Credential expiration list. |
| `/entities/{entity_id}/providers` | Provider JSON API. |
| `/entities/{entity_id}/groups` | Practice JSON API. |
| `/entities/{entity_id}/expirations` | Expiration JSON API. |

`dummy-client` is the default development tenant. New client isolation should use a distinct entity document ID.

## Deploy to Cloud Run

The included Cloud Build configuration builds the Docker image from `credentialing-drive/` and deploys a public Cloud Run service:

```bash
gcloud builds submit --config credentialing-drive/cloudbuild.yaml .
```

For an ad-hoc source deployment from the application directory:

```bash
cd credentialing-drive
gcloud run deploy credentialing-drive --source . --region us-east1
```

Before deploying, configure runtime secrets and environment variables in Cloud Run. Do not pass OAuth secrets or renewal tokens on the command line; mount them from Secret Manager.

## Contributor Notes

- Do not commit `.env`, OAuth client secrets, access tokens, or Drive content.
- Do not persist source-document bytes. The intended design is in-memory processing with structured Firestore output.
- Preserve normalized record identity and field provenance when changing ingestion behavior. Exact JSON equality is not a sufficient deduplication strategy for healthcare credentialing data.
- Keep dashboard links client-scoped by preserving the `entity` query parameter.
