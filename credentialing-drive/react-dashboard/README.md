# Cozema Health React Dashboard

This is a React/Vite migration of the existing Cozema Health dashboard. It keeps the same routes, query-string entity context, API contracts, and visual design system while FastAPI selects either Clerk or Better Auth by environment.

## Run locally

From the repository root, run the coordinated local dashboard session:

```bash
./scripts/run-local-dashboard.sh
```

It starts the complete local environment:

- Vite dashboard on `127.0.0.1:5173`
- dashboard API and Drive webhook handler on `127.0.0.1:8080`
- dedicated document worker on `127.0.0.1:8081`
- temporary HTTPS tunnel URLs for the Drive webhook and Cloud Tasks worker

The script uses the local development auth bypass by default. Stop the command
to shut down the complete local environment. Run `npm install` in this directory
once before using it, then open
`http://127.0.0.1:5173/?entity=dummy-client`.

Vite proxies `/entities` and `/auth` to the FastAPI service, so the React app uses the same data and authentication behavior as the existing dashboard.

### Better Auth local development

To develop against Better Auth rather than the legacy Clerk configuration, follow
[`docs/local-better-auth.md`](../docs/local-better-auth.md). It provisions a local
PostgreSQL container and keeps Google sign-in and real email delivery disabled.

### Local Google Drive monitoring

The same command configures the temporary HTTPS endpoints required by Google
Drive and Cloud Tasks. Install `cloudflared` once if it is not already available:

```bash
brew install cloudflared
```

Each new local-development session receives fresh tunnel addresses, so keep the
launcher running while testing Drive intake.

## Build

Run `npm run build` to produce the deployable bundle in `dist/`.

The production Dockerfile runs this build automatically. FastAPI serves the
result at the existing dashboard URLs and exposes the bundle's hashed files at
`/assets`, so deploying the service does not require a separate Vite server.
