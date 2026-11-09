#!/usr/bin/env bash
set -euo pipefail

# Run the full local environment as one session: Vite, the dashboard API, the
# isolated document worker, and the temporary HTTPS endpoints Drive needs.
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
intake_pid=""
dashboard_pid=""

cleanup() {
  [[ -n "$dashboard_pid" ]] && kill "$dashboard_pid" 2>/dev/null || true
  [[ -n "$intake_pid" ]] && kill "$intake_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(
  cd "$root_dir"
  # This child starts the dashboard API on 8080, the document worker on 8081,
  # and separate public tunnel URLs for Drive webhooks and Cloud Tasks.
  LOCAL_DEV_AUTH_BYPASS="${LOCAL_DEV_AUTH_BYPASS:-true}" \
    ./scripts/run-local-drive-api.sh
) &
intake_pid=$!

(
  cd "$root_dir/react-dashboard"
  npm run dev -- --host 127.0.0.1
) &
dashboard_pid=$!

while kill -0 "$intake_pid" 2>/dev/null && kill -0 "$dashboard_pid" 2>/dev/null; do
  sleep 1
done

wait "$intake_pid" 2>/dev/null || true
wait "$dashboard_pid" 2>/dev/null || true
