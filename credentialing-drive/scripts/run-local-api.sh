#!/usr/bin/env bash
set -euo pipefail

# Keep development-only Drive OAuth credentials in Secret Manager rather than
# writing them to a tracked or local env file. The callback URI must also be
# registered on the development OAuth client in Google Cloud.
project="${LOCAL_GOOGLE_OAUTH_PROJECT:-healthcare-credentialing}"
export GOOGLE_CLIENT_ID="$(gcloud secrets versions access latest --project="$project" --secret=credentialing-drive-google-client-id)"
export GOOGLE_CLIENT_SECRET="$(gcloud secrets versions access latest --project="$project" --secret=credentialing-drive-google-client-secret)"
export GOOGLE_REDIRECT_URI="${GOOGLE_REDIRECT_URI:-http://localhost:8080/oauth/google/callback}"
export APP_BASE_URL="${APP_BASE_URL:-http://127.0.0.1:5173}"
# OAuthlib rejects HTTP by default. This is safe only for the localhost callback
# configured above and must never be set in a deployed environment.
export OAUTHLIB_INSECURE_TRANSPORT=1

exec .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8080 --reload
