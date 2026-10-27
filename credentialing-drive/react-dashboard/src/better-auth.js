import { createAuthClient } from "better-auth/react";
import { organizationClient } from "better-auth/client/plugins";

export function createCozemaAuthClient(baseURL) {
  return createAuthClient({
    baseURL,
    basePath: "/api/auth",
    fetchOptions: { credentials: "include" },
    plugins: [organizationClient()],
  });
}

export async function getAccessToken(baseURL) {
  const response = await fetch(`${baseURL.replace(/\/$/, "")}/api/auth/token`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error("Unable to obtain a secure session token");
  const payload = await response.json();
  return payload.token;
}
