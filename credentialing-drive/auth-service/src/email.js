const RESEND_API_URL = "https://api.resend.com/emails";

function isProduction() {
  return process.env.NODE_ENV === "production";
}

export async function sendEmail({ to, subject, html, text }) {
  const apiKey = process.env.RESEND_API_KEY;
  if (!apiKey) {
    if (isProduction()) {
      throw new Error("RESEND_API_KEY must be configured before production email can be sent");
    }
    // Local development deliberately never silently sends to real recipients.
    console.info("[email preview]", { to, subject, text });
    return;
  }

  const response = await fetch(RESEND_API_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: process.env.EMAIL_FROM,
      to: [to],
      subject,
      html,
      text,
    }),
  });
  if (!response.ok) {
    throw new Error(`Email provider request failed (${response.status})`);
  }
}

export function invitationUrl(invitationId) {
  return new URL(`/accept-invitation?invitationId=${encodeURIComponent(invitationId)}`, process.env.APP_URL).toString();
}

export async function sendInvitationEmail({ id, email, organization, inviter }) {
  const url = invitationUrl(id);
  await sendEmail({
    to: email,
    subject: `Join ${organization.name} on Cozema Health`,
    text: `${inviter.user.name || inviter.user.email} invited you to join ${organization.name} on Cozema Health. Open ${url}`,
    html: `<p>${escapeHtml(inviter.user.name || inviter.user.email)} invited you to join ${escapeHtml(organization.name)} on Cozema Health.</p><p><a href="${url}">Accept invitation</a></p>`,
  });
}

export async function sendVerificationEmail({ user, url }) {
  await sendEmail({
    to: user.email,
    subject: "Verify your Cozema Health email",
    text: `Verify your email address: ${url}`,
    html: `<p>Verify your Cozema Health email address.</p><p><a href="${url}">Verify email</a></p>`,
  });
}

export async function sendPasswordResetEmail({ user, url }) {
  await sendEmail({
    to: user.email,
    subject: "Reset your Cozema Health password",
    text: `Reset your password: ${url}`,
    html: `<p>Reset your Cozema Health password.</p><p><a href="${url}">Reset password</a></p>`,
  });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  })[character]);
}
