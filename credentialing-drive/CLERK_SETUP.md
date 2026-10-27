# Clerk Setup

Clerk owns user accounts, sessions, sign-in methods, and organization membership. Firestore remains the system of record for credentialing data and mirrors only safe user context for future audit and user-management screens.

Every browser data request now requires a valid Clerk session. For non-platform users, the active Clerk Organization must be linked to the requested Firestore client; changing an `entity` value in a URL cannot cross that boundary. Clerk remains the authority for membership and roles, while Firestore mirrors safe member metadata at `entities/{clientId}/members/{clerkUserId}` for activity and future user-management views.

## Create the Clerk application

1. Create a Clerk application and enable the desired sign-in methods. Email/password is sufficient for the first rollout.
2. Enable Organizations. For this invitation-only portal, select **Membership optional** (Personal Accounts enabled), turn off **Create first organization automatically**, and set **Allow user-created Organizations** to disabled. This lets unassigned users receive a clear app-level access message without letting them create a workspace.
3. Add the Cloud Run URL and `http://localhost:8080` as allowed origins/redirect URLs in Clerk.
4. In the Clerk API Keys screen, collect the publishable key, secret key, Frontend API URL, and JWT public key (PEM).
5. Configure these Cloud Run environment variables:

```text
CLERK_PUBLISHABLE_KEY=pk_live_...
CLERK_FRONTEND_API_URL=https://your-instance.clerk.accounts.dev
CLERK_SECRET_KEY=sk_live_...
CLERK_JWT_KEY="-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"
CLERK_AUTHORIZED_PARTIES=https://credentialing-drive-306458975300.us-east1.run.app,https://credentialing-drive-iaq2ujb3ya-ue.a.run.app,http://localhost:8080,http://127.0.0.1:8080
CLERK_ALLOW_MISSING_AZP=false
APP_BASE_URL=https://credentialing-drive-iaq2ujb3ya-ue.a.run.app
```

`CLERK_JWT_KEY` enables offline signature verification; Clerk's cached JWKS fallback works if it is omitted, but the PEM is recommended for production.

For a Clerk development instance that issues session tokens without an `azp` claim, set `CLERK_ALLOW_MISSING_AZP=true`. The application accepts this only with an `sk_test_` key and still verifies the token's signature, issuer, expiry, and session. Keep it `false` for production keys, where `CLERK_AUTHORIZED_PARTIES` remains enforced.

For local development, `clerk env pull --file .env.clerk` keeps Clerk's development keys separate from the app's `.env` settings. The application loads `.env.clerk` at startup. `CLERK_FRONTEND_API_URL` is optional locally because Clerk's publishable key encodes that domain.

Organization invitations are created by the application server with a fixed `redirect_url` of `${APP_BASE_URL}/accept-invitation`. Set `APP_BASE_URL=http://127.0.0.1:8080` in `.env.clerk` for local development. This is intentionally server-controlled so an invitation cannot be turned into an open redirect.

## Organization roles

Create these custom Clerk organization roles. The application owns the permission matrix, so they do not need Clerk permission entries:

| Clerk role key | App role | Intended access |
| --- | --- | --- |
| `org:customer_admin` | Customer Admin | Full control of this client, including members and Drive intake. |
| `org:manager` | Manager | Manage providers, practices, issues, reports, and users. |
| `org:credentialing_specialist` | Credentialing Specialist | Work documents, records, and issues. |
| `org:client` | Client | View-only access. |

Clerk's built-in `org:admin` and `org:member` remain mapped as a short-term compatibility fallback. New invitations use the custom roles above.

For the platform-wide `super_admin` role, add a `platform_role: "super_admin"` session claim from trusted Clerk user metadata. This role can access every linked credentialing client; do not assign it to customer users.

## What each role can do

- `super_admin`: All clients and all application actions.
- `customer_admin`: Manage the client’s users, records, documents, issues, and reports.
- `manager`: Manage users, records, issues, and reports, but not the client Drive connection.
- `credentialing_specialist`: Work records, documents, and issues; can read reports but cannot manage users or export reports.
- `client`: Read-only access to records, issues, activity, and reports.

## Link a Clerk organization to a client

Run this once for each client entity after creating its Clerk Organization:

```bash
python3 scripts/link_clerk_organization.py dummy-client org_your_clerk_organization_id
```

The command stores `clerk_organization_id` on `entities/{client_id}`. After a member signs in under that active organization, the app mirrors safe member context to `users/{clerk_user_id}` and `entities/{client_id}/members/{clerk_user_id}` without storing passwords or Clerk session tokens.

An authenticated user with no active organization sees an access-required screen rather than any credentialing data. A user in an organization not linked to a client sees a separate configuration message. Neither state grants API access.

## New customer setup

1. Create the Firestore client entity during customer onboarding.
2. Create the matching Clerk Organization and configure its organization roles.
3. Link the organization to the client with the script above.
4. Invite customer staff from Clerk, assigning the least-privileged organization role.
5. Have a customer administrator sign in and select the organization. The app will create the Firestore member mirror on first use.

## Client-scoped Drive intake

Each client has its own OAuth connection, folder selection, Drive watch, change events, and Cloud Tasks idempotency keys at `entities/{client_id}/drive_connections/default`. To preserve the original single-client connection during this migration, run once after deployment:

```bash
python3 scripts/migrate_drive_connection.py dummy-client
```

Use the tenant routes for all new connections: `/entities/{client_id}/oauth/google/start`, `/entities/{client_id}/drive/find-test-folder`, and `/entities/{client_id}/drive/watch`.
