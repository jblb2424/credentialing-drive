import "dotenv/config";

import { betterAuth } from "better-auth";
import { jwt } from "better-auth/plugins/jwt";
import { organization } from "better-auth/plugins/organization";
import { Pool } from "pg";

import { sendInvitationEmail, sendPasswordResetEmail, sendVerificationEmail } from "./email.js";
import { recordAuthEvent } from "./audit.js";
import { ac, roles } from "./roles.js";

function required(name) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

const databaseUrl = required("DATABASE_URL");
const baseURL = required("BETTER_AUTH_URL");
const appURL = required("APP_URL");
const audience = required("BETTER_AUTH_JWT_AUDIENCE");

export const pool = new Pool({ connectionString: databaseUrl });

const trustedOrigins = new Set([
  baseURL,
  appURL,
  ...(process.env.BETTER_AUTH_TRUSTED_ORIGINS || "").split(",").map((origin) => origin.trim()).filter(Boolean),
]);

const socialProviders = process.env.GOOGLE_AUTH_CLIENT_ID && process.env.GOOGLE_AUTH_CLIENT_SECRET
  ? {
      google: {
        clientId: process.env.GOOGLE_AUTH_CLIENT_ID,
        clientSecret: process.env.GOOGLE_AUTH_CLIENT_SECRET,
      },
    }
  : undefined;

export const auth = betterAuth({
  baseURL,
  basePath: "/api/auth",
  secret: required("BETTER_AUTH_SECRET"),
  database: pool,
  trustedOrigins: [...trustedOrigins],
  socialProviders,
  emailAndPassword: {
    enabled: true,
    requireEmailVerification: true,
    sendResetPassword: sendPasswordResetEmail,
  },
  emailVerification: {
    sendVerificationEmail,
    sendOnSignUp: true,
    sendOnSignIn: true,
    autoSignInAfterVerification: true,
  },
  plugins: [
    organization({
      ac,
      roles,
      creatorRole: "customer_admin",
      allowUserToCreateOrganization: false,
      requireEmailVerificationOnInvitation: true,
      sendInvitationEmail,
      organizationHooks: {
        afterCreateInvitation: async ({ invitation, inviter, organization }) => recordAuthEvent({
          event: "member_invited",
          organization_id: organization.id,
          actor: { uid: inviter.id, email: inviter.email, display_name: inviter.name },
          target: { email: invitation.email },
          metadata: { role: invitation.role, invitation_id: invitation.id },
        }),
        afterAcceptInvitation: async ({ invitation, member, user, organization }) => recordAuthEvent({
          event: "member_joined",
          organization_id: organization.id,
          actor: { uid: user.id, email: user.email, display_name: user.name },
          target: { uid: member.userId, email: user.email },
          metadata: { role: member.role, invitation_id: invitation.id },
        }),
        afterUpdateMemberRole: async ({ member, previousRole, user, organization }) => recordAuthEvent({
          event: "member_role_updated",
          organization_id: organization.id,
          actor: { uid: user.id, email: user.email, display_name: user.name },
          target: { uid: member.userId },
          metadata: { role: member.role, previous_role: previousRole, member_id: member.id },
        }),
        afterRemoveMember: async ({ member, user, organization }) => recordAuthEvent({
          event: "member_removed",
          organization_id: organization.id,
          actor: { uid: user.id, email: user.email, display_name: user.name },
          target: { uid: member.userId },
          metadata: { role: member.role, member_id: member.id },
        }),
      },
    }),
    jwt({
      jwt: {
        issuer: baseURL,
        audience,
        expirationTime: "10 minutes",
        definePayload: ({ user, session }) => ({
          email: user.email,
          name: user.name,
          activeOrganizationId: session.activeOrganizationId || null,
        }),
      },
      jwks: {
        keyPairConfig: { alg: "EdDSA", crv: "Ed25519" },
        rotationInterval: 60 * 60 * 24 * 90,
      },
    }),
  ],
});
