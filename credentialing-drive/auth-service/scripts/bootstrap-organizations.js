import { readFile } from "node:fs/promises";

import { auth } from "../src/auth.js";

const [inputPath] = process.argv.slice(2);
const bootstrapOwnerUserId = process.env.BOOTSTRAP_OWNER_USER_ID;

if (!inputPath || !bootstrapOwnerUserId) {
  console.error("Usage: BOOTSTRAP_OWNER_USER_ID=<better-auth-user-id> node scripts/bootstrap-organizations.js <organizations.json>");
  process.exit(1);
}

const organizations = JSON.parse(await readFile(inputPath, "utf8"));
if (!Array.isArray(organizations)) throw new Error("The input must be a JSON array");

const mapping = [];
for (const item of organizations) {
  if (!item?.entity_id || !item?.name || !item?.slug) {
    throw new Error("Every organization needs entity_id, name, and slug");
  }
  const organization = await auth.api.createOrganization({
    body: {
      name: item.name,
      slug: item.slug,
      userId: bootstrapOwnerUserId,
      metadata: { entity_id: item.entity_id },
    },
  });
  mapping.push({ entity_id: item.entity_id, better_auth_organization_id: organization.id });
  console.info(`Created ${item.name}: ${organization.id}`);
}

console.log(JSON.stringify(mapping, null, 2));
