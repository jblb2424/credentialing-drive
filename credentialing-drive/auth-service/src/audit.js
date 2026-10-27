import { createHmac } from "node:crypto";

export async function recordAuthEvent(event) {
  const url = process.env.AUTH_EVENT_WEBHOOK_URL;
  const secret = process.env.AUTH_EVENT_WEBHOOK_SECRET;
  if (!url || !secret) {
    if (process.env.NODE_ENV !== "production") console.info("[auth audit preview]", event);
    return;
  }

  const body = JSON.stringify(event);
  const signature = createHmac("sha256", secret).update(body).digest("hex");
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Cozema-Auth-Event": signature,
    },
    body,
  });
  if (!response.ok) throw new Error(`Audit event request failed (${response.status})`);
}
