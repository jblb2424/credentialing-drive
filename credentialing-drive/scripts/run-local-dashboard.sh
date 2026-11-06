#!/usr/bin/env bash
set -euo pipefail

# Run the local API and Vite dashboard as one development session. The bypass is
# deliberately local-only so the dashboard can use the development workspace.
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
api_pid=""
dashboard_pid=""

cleanup() {
  [[ -n "$dashboard_pid" ]] && kill "$dashboard_pid" 2>/dev/null || true
  [[ -n "$api_pid" ]] && kill "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(
  cd "$root_dir"
  LOCAL_DEV_AUTH_BYPASS="${LOCAL_DEV_AUTH_BYPASS:-true}" \
    .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8080
) &
api_pid=$!

(
  cd "$root_dir/react-dashboard"
  npm run dev -- --host 127.0.0.1
) &
dashboard_pid=$!

wait -n "$api_pid" "$dashboard_pid"
