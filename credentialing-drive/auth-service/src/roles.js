import { createAccessControl } from "better-auth/plugins/access";

// These are application-level capabilities, kept alongside Better Auth's
// organization capabilities so a role change is enforced in one authority.
export const statements = {
  organization: ["update", "delete"],
  member: ["create", "update", "delete"],
  invitation: ["create", "cancel"],
  credentialing: [
    "activity_read",
    "documents_manage",
    "issues_read",
    "issues_manage",
    "members_manage",
    "records_read",
    "records_write",
    "reports_export",
    "reports_read",
  ],
};

export const ac = createAccessControl(statements);

const allCredentialingPermissions = statements.credentialing;

export const roles = {
  // This is an organization role, not a global administrative account. Global
  // platform administrators are explicitly configured in the Python API.
  super_admin: ac.newRole({
    organization: ["update", "delete"],
    member: ["create", "update", "delete"],
    invitation: ["create", "cancel"],
    credentialing: allCredentialingPermissions,
  }),
  customer_admin: ac.newRole({
    organization: ["update"],
    member: ["create", "update", "delete"],
    invitation: ["create", "cancel"],
    credentialing: allCredentialingPermissions,
  }),
  manager: ac.newRole({
    member: ["create", "update", "delete"],
    invitation: ["create", "cancel"],
    credentialing: [
      "activity_read",
      "issues_read",
      "issues_manage",
      "members_manage",
      "records_read",
      "records_write",
      "reports_export",
      "reports_read",
    ],
  }),
  credentialing_specialist: ac.newRole({
    credentialing: [
      "activity_read",
      "documents_manage",
      "issues_read",
      "issues_manage",
      "records_read",
      "records_write",
      "reports_read",
    ],
  }),
  client: ac.newRole({
    credentialing: ["activity_read", "issues_read", "records_read", "reports_read"],
  }),
};
