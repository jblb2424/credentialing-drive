export const DEFAULT_ENTITY_ID = "dummy-client";

export function value(value, fallback = "Not captured") {
  if (value === null || value === undefined || value === "") return fallback;
  if (Array.isArray(value)) return value.length ? value.join(", ") : fallback;
  if (typeof value === "object") return Object.values(value).filter(Boolean).join(", ") || fallback;
  return String(value);
}

export function recordName(record, fallback = "Unnamed record") {
  return value(record?.provider?.name || record?.name || record?.legal_name, fallback);
}

export function providerData(record) {
  return record?.provider || record || {};
}

export function fieldLabel(path) {
  const normalized = String(path || "record information")
    .replace(/^provider\./, "")
    .replace(/^group\./, "");
  const locationMatch = normalized.match(/^(?:provider_)?locations(?:\[(\d+)\]|\.(\d+))\.(.+)$/);
  if (locationMatch) {
    const locationNumber = Number(locationMatch[1] ?? locationMatch[2]) + 1;
    return `Location ${locationNumber}: ${fieldLabel(locationMatch[3])}`;
  }
  const indexedMatch = normalized.match(/^([^.\[]+)(?:\[(\d+)\]|\.(\d+))\.(.+)$/);
  if (indexedMatch) {
    const itemNumber = Number(indexedMatch[2] ?? indexedMatch[3]) + 1;
    return `${fieldLabel(indexedMatch[1])} ${itemNumber}: ${fieldLabel(indexedMatch[4])}`;
  }
  return normalized
    .replaceAll("_", " ")
    .replaceAll(".", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function issueLabel(issue) {
  if (issue?.title) return issue.title;
  if (issue?.type === "missing_data") return `Missing ${fieldLabel(issue.field_path || issue.affected_fields?.[0])}`;
  if (issue?.type === "expired") return `${value(issue.credential_label, "Credential")} is expired`;
  if (issue?.type === "expiring") return `${value(issue.credential_label, "Credential")} is expiring soon`;
  return fieldLabel(issue?.field_path || issue?.affected_fields?.[0] || issue?.type);
}

export function issueTypeLabel(type) {
  return String(type || "issue").replaceAll("_", " ");
}

export function categoryLabel(category) {
  const labels = { w_9: "W-9", cv_or_resume: "CV / resume", dea_registration: "DEA registration", manual_update: "Manual update" };
  return labels[category] || issueTypeLabel(category || "other");
}

export function dateLabel(date, fallback = "Date not captured") {
  const result = normalizedDate(date);
  if (!result) return date ? String(date) : fallback;
  if (result.precision === "year") return result.value;
  if (result.precision === "month") {
    return new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric" }).format(result.date);
  }
  return new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", year: "numeric" }).format(result.date);
}

export function shortDate(date, fallback = "Expiration date not captured") {
  const result = normalizedDate(date);
  if (!result) return date ? String(date) : fallback;
  if (result.precision === "year") return result.value;
  if (result.precision === "month") {
    return new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric" }).format(result.date);
  }
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(result.date);
}

function normalizedDate(value) {
  if (typeof value !== "string") return null;
  if (/^\d{4}$/.test(value)) return { precision: "year", value };
  const normalized = /^\d{4}-\d{2}$/.test(value) ? `${value}-01` : value;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(normalized)) return null;
  const date = new Date(`${normalized}T00:00:00`);
  if (Number.isNaN(date.getTime())) return null;
  return { precision: value.length === 7 ? "month" : "day", date };
}

export function timestampLabel(timestamp) {
  if (!timestamp) return "Date not recorded";
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "Date not recorded";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
}

export function expirationState(expirationDate) {
  const normalized = normalizedDate(expirationDate);
  if (!normalized || normalized.precision !== "day") return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return normalized.date < today ? "expired" : null;
}

export function addressLabel(address) {
  if (!address) return "Address not captured";
  if (typeof address === "string") return address || "Address not captured";
  return [address.line_1 || address.address_line_1 || address.street, address.line_2, [address.city, address.state, address.zip || address.postal_code].filter(Boolean).join(", ")]
    .filter(Boolean)
    .join(" · ") || "Address not captured";
}

export function locationLabel(location) {
  return value(location?.display_name || location?.name || location?.address?.display_name || location?.address?.line_1, "Location not captured");
}

export function priority(issue) {
  const order = { critical: 0, high: 1, medium: 2, low: 3 };
  return order[issue?.severity] ?? 4;
}
