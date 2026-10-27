import { auth, pool } from "../src/auth.js";

const ownerUserId = process.env.BOOTSTRAP_OWNER_USER_ID;
const entityId = process.env.BOOTSTRAP_ENTITY_ID;
const organizationName = process.env.BOOTSTRAP_ORGANIZATION_NAME;
const organizationSlug = process.env.BOOTSTRAP_ORGANIZATION_SLUG;

if (!ownerUserId || !entityId || !organizationName || !organizationSlug) {
  throw new Error("BOOTSTRAP_OWNER_USER_ID, BOOTSTRAP_ENTITY_ID, BOOTSTRAP_ORGANIZATION_NAME, and BOOTSTRAP_ORGANIZATION_SLUG are required");
}

const context = await auth.$context;
let organization = await context.adapter.findOne({
  model: "organization",
  where: [{ field: "slug", value: organizationSlug }],
});
let created = false;

if (organization) {
  const metadata = typeof organization.metadata === "string"
    ? JSON.parse(organization.metadata || "{}")
    : organization.metadata || {};
  if (metadata.entity_id && metadata.entity_id !== entityId) {
    throw new Error(`Organization slug ${organizationSlug} is already linked to a different entity`);
  }
} else {
  organization = await auth.api.createOrganization({
    body: {
      name: organizationName,
      slug: organizationSlug,
      userId: ownerUserId,
      metadata: { entity_id: entityId },
    },
  });
  created = true;
}

const membership = await context.adapter.findOne({
  model: "member",
  where: [
    { field: "organizationId", value: organization.id },
    { field: "userId", value: ownerUserId },
  ],
});
if (!membership) {
  throw new Error("The bootstrap owner is not a member of the existing organization");
}

const result = await pool.query(
  'UPDATE "session" SET "activeOrganizationId" = $1 WHERE "userId" = $2',
  [organization.id, ownerUserId],
);

console.log(JSON.stringify({
  entity_id: entityId,
  better_auth_organization_id: organization.id,
  created,
  sessions_activated: result.rowCount,
}));

await pool.end();
