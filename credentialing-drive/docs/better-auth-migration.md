# Better Auth Migration

Better Auth becomes the authority for user accounts, organizations,
invitations, and application roles. Firestore continues to store credentialing
records and a non-authoritative user/audit mirror.

## What changed

- `auth-service/` is a standalone Better Auth service backed by PostgreSQL.
- Better Auth organization roles mirror the existing product roles:
  `customer_admin`, `manager`, `credentialing_specialist`, and `client`.
- FastAPI verifies a 10-minute JWT against Better Auth's JWKS and reads the
  active membership from PostgreSQL on each authorized request. A stale role
  claim can never grant access after a removal or role change.
- The React dashboard uses Better Auth's cookie session, scoped to the auth
  service host. It requests a short-lived bearer JWT only when calling FastAPI.
- Invitations, role changes, and removals are made against Better Auth's
  organization API. The auth service signs audit webhooks to FastAPI.

## Provision once per environment

1. Provision a PostgreSQL database for the auth service. Production uses the
   dedicated zonal `db-f1-micro` `cozema-auth-prod` Cloud SQL PostgreSQL instance and
   `cozema-auth-runtime` service account. Connect only through the Cloud Run
   Cloud SQL connector; do not add authorized networks.
2. Create Secret Manager values: `better-auth-database-url`,
   `better-auth-secret`, `resend-api-key`, `better-auth-google-client-id`,
   `better-auth-google-client-secret`, `auth-event-webhook-secret`, and
   `better-auth-email-from`.
3. Configure Resend (or replace `src/email.js` with the selected provider),
   verify the sender domain, and keep invitation/email-verification delivery
   disabled in production until it has been tested.
4. Deploy `auth-service/cloudbuild.production.yaml`, map
   `auth.cozemahealth.com` to it, and add that hostname to Better Auth's trusted
   origins. Do not map `auth.cozemahealth.com` to the FastAPI service.
5. Configure a *separate* Google OAuth web client for sign-in, with
   `https://auth.cozemahealth.com/api/auth/callback/google` as its redirect URI.
   This is independent from the Drive OAuth client used by FastAPI.
6. Run `cd auth-service && npm run migrate` against the chosen database.

## Move organizations and users

Clerk password hashes and live sessions cannot be transferred. Existing users
must create a Better Auth password, reset a password, or use the configured
Google sign-in provider. Keep Clerk available until every client has completed
that transition.

1. Export client definitions: `python scripts/export_better_auth_organizations.py`.
2. Create one verified Better Auth bootstrap administrator, then run
   `BOOTSTRAP_OWNER_USER_ID=<id> node auth-service/scripts/bootstrap-organizations.js better-auth-organizations.json`.
   Save its JSON output as `better-auth-organization-mapping.json`.
3. Link entities: `python scripts/link_better_auth_organizations.py better-auth-organization-mapping.json`.
4. Sign in as the bootstrap administrator and invite each client administrator
   from the Users page. Those administrators can then invite their own team.
5. Set the FastAPI environment values below and deploy the FastAPI service:

```
AUTH_PROVIDER=better_auth
BETTER_AUTH_URL=https://auth.cozemahealth.com
BETTER_AUTH_JWT_AUDIENCE=https://app.cozemahealth.com
BETTER_AUTH_DATABASE_URL=<same server-only PostgreSQL URL used by auth-service>
BETTER_AUTH_GOOGLE_SIGN_IN_ENABLED=true
COZEMA_PLATFORM_ADMIN_EMAILS=<comma-separated platform admin emails>
AUTH_EVENT_WEBHOOK_SECRET=<same secret configured in auth-service>
```

6. Run the tenant/role/invitation/Drive smoke tests before changing the
   production app service to `AUTH_PROVIDER=better_auth`.

## Explicit production cutover

Do not change the default `cloudbuild.production.yaml` substitutions until the
auth service, email delivery, and Google sign-in have been verified. At
cutover, submit the existing production build with these substitutions in
addition to the pinned Clerk and Drive secret versions:

```bash
gcloud builds submit --config=cloudbuild.production.yaml \
  --project=healthcare-credentialing-prod \
  --substitutions=_AUTH_PROVIDER=better_auth,_BETTER_AUTH_GOOGLE_SIGN_IN_ENABLED=true,_COZEMA_PLATFORM_ADMIN_EMAILS=YOUR_PLATFORM_ADMIN_EMAIL
```

Capture the prior `credentialing-drive` revision before this command. Rollback
means routing both the dashboard/API and auth-service traffic to their
known-good revisions; never leave the Better Auth browser dashboard paired with
the Clerk-only API revision.

## Rollback

Before cutover, retain the existing Clerk environment variables and the prior
FastAPI/dashboard Cloud Run revision. Roll back the **dashboard and API
together** to that known-good revision; this avoids combining the Better Auth
browser client with Clerk server configuration. Do not remove Clerk users or
organization links until all production clients have been stable on Better Auth
and the retention period has elapsed.
