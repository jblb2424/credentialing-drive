let accessTokenGetter = null;

export function setAccessTokenGetter(getter) {
  accessTokenGetter = getter;
}

export async function fetchWithAuth(path, options = {}) {
  const token = accessTokenGetter ? await accessTokenGetter() : null;
  const headers = new Headers(options.headers);
  headers.set("Accept", headers.get("Accept") || "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(path, { ...options, headers, credentials: options.credentials || "include" });
}

export async function fetchJson(path, options) {
  const response = await fetchWithAuth(path, options);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") message = body.detail;
    } catch {
      // Some upstream failures return an empty or non-JSON body.
    }
    throw new Error(message);
  }
  return response.json();
}

export function entityPath(entityId, suffix) {
  return `/entities/${encodeURIComponent(entityId)}${suffix}`;
}
