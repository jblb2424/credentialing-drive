import { auth, pool } from "../src/auth.js";

const email = String(process.env.BOOTSTRAP_ADMIN_EMAIL || "").trim().toLowerCase();
const name = String(process.env.BOOTSTRAP_ADMIN_NAME || "Cozema Health administrator").trim();
const emailVerified = String(process.env.BOOTSTRAP_ADMIN_EMAIL_VERIFIED || "").trim().toLowerCase() === "true";

if (!email || !email.includes("@")) {
  throw new Error("BOOTSTRAP_ADMIN_EMAIL must be a valid email address");
}

try {
  const context = await auth.$context;
  const existing = await context.internalAdapter.findUserByEmail(email);
  let user = existing?.user;

  if (!user) {
    user = await context.internalAdapter.createUser({
      email,
      name,
      // This is only enabled for a human-confirmed, initial platform admin.
      emailVerified,
    });
  } else if (emailVerified && !user.emailVerified) {
    // Better Auth requires an existing local account to be verified before it
    // can be linked to a social identity. This controlled bootstrap flag avoids
    // weakening that policy for ordinary users.
    user = await context.internalAdapter.updateUser(user.id, { emailVerified: true });
  }

  console.log(JSON.stringify({
    id: user.id,
    email: user.email,
    email_verified: user.emailVerified,
    created: !existing,
    bootstrap_verified: emailVerified,
  }));
} finally {
  await pool.end();
}
