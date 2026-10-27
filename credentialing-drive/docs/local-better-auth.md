# Local Better Auth Development

Local development uses an isolated PostgreSQL container and Better Auth service.
It never uses production PostgreSQL, production secrets, Google sign-in, or Resend.
In development, email verification and invitation links are printed to the auth-service
terminal instead of being delivered.

## One-time setup

From the repository root:

```bash
./scripts/setup-local-better-auth.sh
docker compose -f docker-compose.dev.yml up -d
cd auth-service && npm ci && npm run migrate
```

The setup script creates two ignored files:

- `.env.better-auth`: FastAPI's Better Auth configuration.
- `auth-service/.env`: auth service and local PostgreSQL configuration.

It refuses to overwrite either file. To start over, stop the database and remove the
`cozema-auth-dev-postgres` Docker volume, then delete both generated env files.

## Run the local stack

Use three terminals:

```bash
# Terminal 1: Better Auth
cd auth-service && npm run dev

# Terminal 2: FastAPI
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8080

# Terminal 3: dashboard
cd react-dashboard && npm run dev
```

Open `http://127.0.0.1:5173/?entity=dummy-client`.

Check the dependencies before using the dashboard:

```bash
curl http://127.0.0.1:8787/healthz
curl http://127.0.0.1:8080/auth/config
```

The first response should be `{"status":"ok"}`. The second should identify
`better_auth` as the provider.

## First local workspace

Better Auth owns organizations; Firestore links each organization to an application
entity. Create a local account in the dashboard first, then create and link its
organization before attempting to access `dummy-client` data:

1. Retrieve the Better Auth user ID from local PostgreSQL.
2. Run `auth-service/scripts/bootstrap-client-organization.js` with the user ID,
   `dummy-client`, and a local organization name/slug.
3. Use `scripts/link_better_auth_organizations.py` to write the returned organization
   ID to the `dummy-client` Firestore entity.

Keep this mapping in the development Firestore database only. Never reuse a production
organization ID locally.
