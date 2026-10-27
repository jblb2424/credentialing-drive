import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.auth import (
    ROLES,
    authorize_entity_access,
    better_auth_web_config,
    clerk_allows_missing_azp,
    clerk_frontend_api_url,
    clerk_web_config,
    local_development_user,
    role_allows,
    user_from_better_auth_claims,
    user_from_claims,
    workspace_access_status,
)
from app.clerk import invitation_redirect_url, invite_organization_member, serialize_membership
from app.routes import authenticated_router, delete_entity_provider_by_id, require_scheduler_identity
from app.task_queue import task_id_for_file


class ClerkAuthTests(unittest.TestCase):
    def test_scheduler_identity_requires_the_configured_oidc_service_account(self):
        request = type("Request", (), {"headers": {"authorization": "Bearer signed-token"}})()
        environment = {
            "DRIVE_WATCH_RENEWAL_SERVICE_ACCOUNT": "scheduler@example.iam.gserviceaccount.com",
            "DRIVE_WATCH_RENEWAL_AUDIENCE": "https://app.example.test",
        }
        with patch.dict(os.environ, environment, clear=True):
            with patch(
                "app.routes.id_token.verify_oauth2_token",
                return_value={
                    "email": "scheduler@example.iam.gserviceaccount.com",
                    "email_verified": True,
                },
            ) as verify:
                require_scheduler_identity(request)
        verify.assert_called_once()
        self.assertEqual(verify.call_args.kwargs["audience"], "https://app.example.test")

    def test_scheduler_identity_rejects_a_token_from_another_service_account(self):
        request = type("Request", (), {"headers": {"authorization": "Bearer signed-token"}})()
        environment = {
            "DRIVE_WATCH_RENEWAL_SERVICE_ACCOUNT": "scheduler@example.iam.gserviceaccount.com",
            "DRIVE_WATCH_RENEWAL_AUDIENCE": "https://app.example.test",
        }
        with patch.dict(os.environ, environment, clear=True):
            with patch(
                "app.routes.id_token.verify_oauth2_token",
                return_value={"email": "other@example.iam.gserviceaccount.com", "email_verified": True},
            ):
                with self.assertRaises(HTTPException) as error:
                    require_scheduler_identity(request)
        self.assertEqual(error.exception.status_code, 401)

    def test_scheduler_identity_fails_closed_without_its_production_configuration(self):
        request = type("Request", (), {"headers": {"authorization": "Bearer signed-token"}})()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HTTPException) as error:
                require_scheduler_identity(request)
        self.assertEqual(error.exception.status_code, 401)

    def test_web_config_is_disabled_without_required_values(self):
        with patch.dict(os.environ, {"CLERK_PUBLISHABLE_KEY": "pk_test"}, clear=True):
            self.assertEqual(clerk_web_config(), {"enabled": False})

    def test_local_development_bypass_requires_loopback_and_explicit_opt_in(self):
        loopback_request = type("Request", (), {"client": type("Client", (), {"host": "127.0.0.1"})()})()
        remote_request = type("Request", (), {"client": type("Client", (), {"host": "10.0.0.4"})()})()
        with patch.dict(os.environ, {"LOCAL_DEV_AUTH_BYPASS": "true"}, clear=True):
            self.assertEqual(local_development_user(loopback_request)["role"], "super_admin")
            self.assertIsNone(local_development_user(remote_request))
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(local_development_user(loopback_request))

    def test_web_config_returns_only_browser_safe_values(self):
        with patch.dict(
            os.environ,
            {
                "CLERK_PUBLISHABLE_KEY": "pk_test_public",
                "CLERK_FRONTEND_API_URL": "https://example.clerk.accounts.dev/",
                "CLERK_SECRET_KEY": "sk_test_secret",
                "CLERK_AUTHORIZED_PARTIES": "http://localhost:8080",
            },
            clear=True,
        ):
            self.assertEqual(
                clerk_web_config(),
                {
                    "enabled": True,
                    "clerk_config": {
                        "publishable_key": "pk_test_public",
                        "frontend_api_url": "https://example.clerk.accounts.dev",
                    },
                },
            )

    def test_better_auth_web_config_requires_the_database_authorization_path(self):
        with patch.dict(
            os.environ,
            {
                "AUTH_PROVIDER": "better_auth",
                "BETTER_AUTH_URL": "https://auth.example.test",
                "BETTER_AUTH_JWT_AUDIENCE": "https://app.example.test",
            },
            clear=True,
        ):
            self.assertEqual(better_auth_web_config(), {"enabled": False, "provider": "better_auth"})

    def test_better_auth_claim_role_comes_from_authoritative_membership(self):
        with patch.dict(
            os.environ,
            {"COZEMA_PLATFORM_ADMIN_EMAILS": "platform@example.com"},
            clear=True,
        ):
            with patch("app.auth._better_auth_membership", return_value="credentialing_specialist") as membership:
                user = user_from_better_auth_claims(
                    {
                        "sub": "user_better_123",
                        "email": "specialist@example.com",
                        "name": "Casey Specialist",
                        "activeOrganizationId": "org_better_123",
                        # A role claim must never be trusted by the Python API.
                        "role": "customer_admin",
                    }
                )
        membership.assert_called_once_with("user_better_123", "org_better_123")
        self.assertEqual(user["role"], "credentialing_specialist")
        self.assertEqual(user["better_auth_org_id"], "org_better_123")

    def test_configured_platform_admin_is_not_limited_to_one_organization(self):
        with patch.dict(os.environ, {"COZEMA_PLATFORM_ADMIN_EMAILS": "platform@example.com"}, clear=True):
            with patch("app.auth._better_auth_membership") as membership:
                user = user_from_better_auth_claims({"sub": "admin_123", "email": "platform@example.com"})
        membership.assert_not_called()
        self.assertEqual(user["role"], "super_admin")

    def test_missing_azp_compatibility_is_limited_to_clerk_test_keys(self):
        with patch.dict(
            os.environ,
            {"CLERK_ALLOW_MISSING_AZP": "true", "CLERK_SECRET_KEY": "sk_test_secret"},
            clear=True,
        ):
            self.assertTrue(clerk_allows_missing_azp())
        with patch.dict(
            os.environ,
            {"CLERK_ALLOW_MISSING_AZP": "true", "CLERK_SECRET_KEY": "sk_live_secret"},
            clear=True,
        ):
            self.assertFalse(clerk_allows_missing_azp())

    def test_claims_map_active_organization_role(self):
        user = user_from_claims(
            {
                "sub": "user_123",
                "email": "manager@example.com",
                "name": "Morgan Manager",
                "o": {"id": "org_123", "rol": "org:manager", "slg": "dummy-client"},
            }
        )
        self.assertEqual(user["role"], "manager")
        self.assertEqual(user["clerk_org_id"], "org_123")
        self.assertEqual(user["clerk_org_slug"], "dummy-client")
        self.assertEqual(user_from_claims({"sub": "user_456", "org_role": "unknown"})["role"], "unassigned")
        self.assertEqual(user_from_claims({"sub": "user_789", "org_role": "org:admin"})["role"], "customer_admin")
        self.assertEqual(user_from_claims({"sub": "user_987", "org_role": "admin"})["role"], "customer_admin")
        self.assertIn("super_admin", ROLES)

    def test_frontend_api_url_can_be_derived_from_the_publishable_key(self):
        self.assertEqual(
            clerk_frontend_api_url("pk_test_ZXhhbXBsZS5jbGVyay5hY2NvdW50cy5kZXYk"),
            "https://example.clerk.accounts.dev",
        )

    def test_role_permissions_match_the_workspace_roles(self):
        self.assertTrue(role_allows("customer_admin", "members:manage"))
        self.assertTrue(role_allows("credentialing_specialist", "issues:manage"))
        self.assertTrue(role_allows("credentialing_specialist", "documents:manage"))
        self.assertFalse(role_allows("client", "issues:manage"))
        self.assertFalse(role_allows("client", "reports:export"))
        self.assertTrue(role_allows("super_admin", "records:write"))
        self.assertTrue(role_allows("customer_admin", "records:write"))
        self.assertTrue(role_allows("manager", "records:write"))
        self.assertTrue(role_allows("credentialing_specialist", "records:write"))
        self.assertFalse(role_allows("client", "records:write"))

    def test_provider_deletion_records_an_entity_level_audit_event(self):
        user = {"uid": "user_123", "email": "manager@example.com", "role": "manager"}
        with patch("app.routes.get_provider", return_value={"id": "provider_123"}) as get_provider:
            with patch(
                "app.routes.delete_provider",
                return_value={
                    "deleted_provider_id": "provider_123",
                    "deleted_document_count": 8,
                    "deletion_duration_ms": 42,
                },
            ) as delete_provider:
                with patch("app.routes.safely_record_audit_event") as record_event:
                    result = delete_entity_provider_by_id("client_123", "provider_123", user)
        self.assertEqual(
            result,
            {
                "deleted_provider_id": "provider_123",
                "deleted_document_count": 8,
                "deletion_duration_ms": 42,
            },
        )
        get_provider.assert_called_once_with("provider_123", "client_123")
        delete_provider.assert_called_once_with("provider_123", "client_123")
        record_event.assert_called_once_with(
            "client_123",
            "provider_deleted",
            user,
            {
                "record_type": "provider",
                "record_id": "provider_123",
                "deleted_document_count": 8,
                "deletion_duration_ms": 42,
            },
            {},
        )

    def test_workspace_status_explains_unassigned_client_access(self):
        self.assertEqual(
            workspace_access_status({"role": "client"}, None),
            "no_client_organization",
        )
        self.assertEqual(
            workspace_access_status({"role": "client", "clerk_org_id": "org_unlinked"}, None),
            "unlinked_organization",
        )
        self.assertEqual(
            workspace_access_status({"role": "manager", "clerk_org_id": "org_linked"}, "dummy-client"),
            "ready",
        )

    def test_drive_task_ids_are_tenant_scoped(self):
        self.assertNotEqual(
            task_id_for_file("client-a", "drive-file-123"),
            task_id_for_file("client-b", "drive-file-123"),
        )

    def test_clerk_membership_is_safe_for_the_users_page(self):
        member = serialize_membership(
            {
                "role": "org:credentialing_specialist",
                "created_at": 123,
                "public_user_data": {
                    "user_id": "user_123",
                    "first_name": "Avery",
                    "last_name": "Rowan",
                    "identifier": "avery@example.com",
                },
            }
        )
        self.assertEqual(member["role"], "credentialing_specialist")
        self.assertEqual(member["display_name"], "Avery Rowan")

    def test_organization_invitation_uses_the_configured_acceptance_page(self):
        with patch.dict(os.environ, {"APP_BASE_URL": "https://app.example.test"}, clear=True):
            with patch("app.clerk.clerk_request", return_value={"id": "orginv_123"}) as request:
                invite_organization_member(
                    "org_123",
                    "member@example.com",
                    "manager",
                    redirect_url=invitation_redirect_url(),
                )
        request.assert_called_once_with(
            "POST",
            "/organizations/org_123/invitations",
            {
                "email_address": "member@example.com",
                "role": "org:manager",
                "redirect_url": "https://app.example.test/accept-invitation",
            },
        )

    def test_organization_invitation_rejects_an_unsafe_redirect_configuration(self):
        with patch.dict(os.environ, {"APP_BASE_URL": "javascript:alert(1)"}, clear=True):
            with self.assertRaises(HTTPException) as error:
                invitation_redirect_url()
        self.assertEqual(error.exception.status_code, 503)

    def test_entity_authorization_rejects_another_clients_data(self):
        user = {"role": "manager", "clerk_org_id": "org_123"}
        with patch("app.auth.entity_for_user", return_value="client-a"):
            with patch("app.audit.safely_record_security_event") as audit_event:
                with self.assertRaises(HTTPException) as error:
                    authorize_entity_access("client-b", user, "records:read")
        audit_event.assert_called_once()
        self.assertEqual(error.exception.status_code, 403)

    def test_entity_authorization_allows_a_platform_admin(self):
        user = {"role": "super_admin"}
        self.assertEqual(
            authorize_entity_access("any-client", user, "records:read"), user
        )

    def test_data_router_rejects_a_authenticated_user_from_another_client(self):
        app = FastAPI()
        app.include_router(authenticated_router)
        user = {"uid": "user_123", "role": "manager", "clerk_org_id": "org_123"}
        with patch("app.auth.verify_session_token", return_value=user):
            with patch("app.auth.entity_for_user", return_value="client-a"):
                with patch("app.audit.safely_record_security_event"):
                    response = TestClient(app).get("/entities/client-b/providers")
        self.assertEqual(response.status_code, 403)

    def test_data_router_rejects_an_unauthenticated_request(self):
        app = FastAPI()
        app.include_router(authenticated_router)
        with patch(
            "app.auth.verify_session_token",
            side_effect=HTTPException(status_code=401, detail="Invalid or expired Clerk session token"),
        ):
            response = TestClient(app).get("/entities/dummy-client/providers")
        self.assertEqual(response.status_code, 401)
