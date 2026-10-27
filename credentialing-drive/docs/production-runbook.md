# Production deployment runbook

## Preconditions

1. Create the production Secret Manager secrets named in `auth-service/cloudbuild.production.yaml` and add the intended values as new immutable versions.
2. Grant `cozema-auth-runtime@healthcare-credentialing-prod.iam.gserviceaccount.com` `roles/secretmanager.secretAccessor` on each Better Auth secret.
3. Add `https://auth.cozemahealth.com/api/auth/callback/google` to the separate Better Auth Google OAuth client. Do not reuse the Drive client.
4. Confirm the Scheduler job target and `DRIVE_WATCH_RENEWAL_AUDIENCE` use the same stable origin. The endpoint verifies Scheduler's OIDC identity, not a header secret.

## Deploy a production revision

Run from the repository root. Replace every `VERSION` value with the immutable Secret Manager version intended for that release; never use `latest`.

```bash
cd auth-service
gcloud builds submit --config=cloudbuild.production.yaml --project=healthcare-credentialing-prod \
  --service-account=projects/healthcare-credentialing-prod/serviceAccounts/credentialing-drive-deployer@healthcare-credentialing-prod.iam.gserviceaccount.com
```

The Better Auth service is intentionally public because browsers must reach its
sign-in and OAuth callback endpoints. Its database and secrets remain private.
Do not expose the PostgreSQL instance with authorized networks.

## Verify and release the scheduler

1. Confirm the new Cloud Run revision reports `Ready`.
2. Invoke `/drive/watch/renew` from the Scheduler job once and verify a successful job attempt in Cloud Logging.
3. Confirm the job's OIDC service account is `credentialing-drive-scheduler@healthcare-credentialing-prod.iam.gserviceaccount.com` and its audience matches `DRIVE_WATCH_RENEWAL_AUDIENCE`.
4. Resume the job only after a live Google Drive connection has created at least one watch channel.

## Observe production

The `ops/production-alerts` definitions cover Cloud Run 5xxs, Cloud Tasks failures, Drive webhook failures, and Cloud Scheduler failures. The configured email channel must be verified before it can deliver notifications. The production budget is filtered to this project and alerts at 50%, 80%, 100%, and forecasted 100% of its monthly threshold.

## Roll back

List ready revisions, then route all traffic to the prior known-good revision:

```bash
gcloud run revisions list --service=credentialing-drive --region=us-east1 --project=healthcare-credentialing-prod
gcloud run services update-traffic credentialing-drive --region=us-east1 --project=healthcare-credentialing-prod --to-revisions=KNOWN_GOOD_REVISION=100
```

Record the released revision, image digest, and each pinned secret version in the release ticket. Rollback changes traffic only; it does not mutate Firestore data or secret versions.
