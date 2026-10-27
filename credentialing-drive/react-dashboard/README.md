# Cozema Health React Dashboard

This is a React/Vite migration of the existing Cozema Health dashboard. It keeps the same routes, query-string entity context, API contracts, and visual design system while FastAPI selects either Clerk or Better Auth by environment.

## Run locally

1. Start the FastAPI API from the repository root on `127.0.0.1:8080`.
2. In this directory, run `npm install` once.
3. Run `npm run dev` and open `http://127.0.0.1:5173/?entity=dummy-client`.

Vite proxies `/entities` and `/auth` to the FastAPI service, so the React app uses the same data and authentication behavior as the existing dashboard.

### Better Auth local development

To develop against Better Auth rather than the legacy Clerk configuration, follow
[`docs/local-better-auth.md`](../docs/local-better-auth.md). It provisions a local
PostgreSQL container and keeps Google sign-in and real email delivery disabled.

### Local Google Drive monitoring

Connecting a Google account works locally, but selecting an intake folder also starts a
Google Drive change watch. Google requires its webhook target to be publicly reachable
over HTTPS, so `localhost` cannot be used directly. Start an HTTPS tunnel to local port
`8080`, then set its callback URL before selecting a folder:

```bash
export GOOGLE_DRIVE_WEBHOOK_URL="https://your-tunnel.example/webhooks/google-drive"
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8080
```

Use a new tunnel URL each time it changes and restart FastAPI after updating the
environment variable. Without this configuration, the app now reports the setup issue
instead of a generic server error.

## Build

Run `npm run build` to produce the deployable bundle in `dist/`.

The production Dockerfile runs this build automatically. FastAPI serves the
result at the existing dashboard URLs and exposes the bundle's hashed files at
`/assets`, so deploying the service does not require a separate Vite server.
