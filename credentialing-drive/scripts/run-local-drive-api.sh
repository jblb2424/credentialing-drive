#!/usr/bin/env bash
set -euo pipefail

# Google Drive webhooks must reach a public HTTPS URL. This starts an
# ephemeral Cloudflare Quick Tunnel for local-only intake monitoring.
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is required. Install it with: brew install cloudflared" >&2
  exit 1
fi

tunnel_log="$(mktemp)"
tunnel_pid=""
api_pid=""

cleanup() {
  [[ -n "$api_pid" ]] && kill "$api_pid" 2>/dev/null || true
  [[ -n "$tunnel_pid" ]] && kill "$tunnel_pid" 2>/dev/null || true
  rm -f "$tunnel_log"
}
trap cleanup EXIT INT TERM

cloudflared tunnel --url http://127.0.0.1:8080 >"$tunnel_log" 2>&1 &
tunnel_pid=$!

tunnel_url=""
for _ in $(seq 1 30); do
  tunnel_url="$(grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' "$tunnel_log" | head -n 1 || true)"
  [[ -n "$tunnel_url" ]] && break
  sleep 1
done

if [[ -z "$tunnel_url" ]]; then
  cat "$tunnel_log" >&2
  echo "Cloudflare Quick Tunnel did not start." >&2
  exit 1
fi

export GOOGLE_DRIVE_WEBHOOK_URL="$tunnel_url/webhooks/google-drive"
export DRIVE_WATCH_OWNER=local
export GCP_PROJECT_ID="${LOCAL_GCP_PROJECT:-healthcare-credentialing}"
export CLOUD_TASKS_LOCATION="${CLOUD_TASKS_LOCATION:-us-east1}"
export CLOUD_TASKS_QUEUE="${CLOUD_TASKS_QUEUE:-drive-document-processing}"
export TASK_INVOKER_SERVICE_ACCOUNT="${TASK_INVOKER_SERVICE_ACCOUNT:-credentialing-drive-tasks@${GCP_PROJECT_ID}.iam.gserviceaccount.com}"
export TASK_PROCESSOR_URL="$tunnel_url/tasks/process-drive-file"
# Local intake uses the same development Document AI processor as the queued
# Cloud Tasks worker. Allow an override for an isolated processor when needed.
export DOCUMENT_AI_LOCATION="${DOCUMENT_AI_LOCATION:-us}"
export DOCUMENT_AI_PROCESSOR_ID="${LOCAL_DOCUMENT_AI_PROCESSOR_ID:-302b5ee22c4f448f}"
echo "Local Drive webhook: $GOOGLE_DRIVE_WEBHOOK_URL"
echo "Local task processor: $TASK_PROCESSOR_URL"
echo "Keep this process running while testing local-only Drive monitoring."

./scripts/run-local-api.sh &
api_pid=$!
wait "$api_pid"
