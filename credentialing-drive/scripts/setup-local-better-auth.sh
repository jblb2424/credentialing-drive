#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
app_env="$repo_root/.env.better-auth"
auth_env="$repo_root/auth-service/.env"

if [[ -e "$app_env" || -e "$auth_env" ]]; then
  echo "Refusing to overwrite existing local Better Auth configuration."
  echo "Delete both $app_env and $auth_env only if you intentionally want fresh local credentials."
  exit 1
fi

generate_secret() {
  openssl rand -base64 48 | tr -d '\n'
}

umask 077
webhook_secret="$(generate_secret)"
auth_secret="$(generate_secret)"

cat > "$app_env" <<EOF
# Generated for local development only. This file is gitignored.
AUTH_PROVIDER=better_auth
BETTER_AUTH_URL=http://127.0.0.1:8787
BETTER_AUTH_JWT_AUDIENCE=http://127.0.0.1:8080
# FastAPI only checks that this is configured; PostgreSQL is owned by auth-service.
BETTER_AUTH_DATABASE_URL=postgresql://cozema_auth:local-password@127.0.0.1:5432/cozema_auth
BETTER_AUTH_GOOGLE_SIGN_IN_ENABLED=false
COZEMA_PLATFORM_ADMIN_EMAILS=
AUTH_EVENT_WEBHOOK_SECRET=$webhook_secret
EOF

cat > "$auth_env" <<EOF
# Generated for local development only. This file is gitignored.
NODE_ENV=development
PORT=8787
DATABASE_URL=postgresql://cozema_auth:local-password@127.0.0.1:5432/cozema_auth
BETTER_AUTH_URL=http://127.0.0.1:8787
BETTER_AUTH_TRUSTED_ORIGINS=http://127.0.0.1:5173,http://127.0.0.1:8080
BETTER_AUTH_SECRET=$auth_secret
BETTER_AUTH_JWT_AUDIENCE=http://127.0.0.1:8080
APP_URL=http://127.0.0.1:5173
AUTH_EVENT_WEBHOOK_URL=http://127.0.0.1:8080/internal/auth-events
AUTH_EVENT_WEBHOOK_SECRET=$webhook_secret
EMAIL_FROM=Cozema Health <no-reply@localhost>
# Empty credentials keep local Google sign-in disabled. Email actions log
# preview links rather than delivering messages while NODE_ENV=development.
RESEND_API_KEY=
GOOGLE_AUTH_CLIENT_ID=
GOOGLE_AUTH_CLIENT_SECRET=
EOF

echo "Created local Better Auth configuration. Next run:"
echo "  docker compose -f docker-compose.dev.yml up -d"
echo "  (cd auth-service && npm ci && npm run migrate && npm run dev)"
