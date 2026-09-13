const params = new URLSearchParams(window.location.search);
const entityId = params.get("entity") || "dummy-client";
const groupId = window.location.pathname.split("/")[2];

const elements = {
  loading: document.querySelector("#record-loading"),
  error: document.querySelector("#record-error"),
  page: document.querySelector("#record-page"),
  client: document.querySelector("#crumb-client"),
  crumbPractice: document.querySelector("#crumb-practice"),
  name: document.querySelector("#practice-name"),
  subtitle: document.querySelector("#practice-subtitle"),
  identity: document.querySelector("#identity-grid"),
  locations: document.querySelector("#location-list"),
  w9: document.querySelector("#w9-grid"),
  summary: document.querySelector("#summary-list"),
};

function escapeHtml(value) {
  return String(value ?? "Not provided")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function value(input) {
  return input === undefined || input === null || input === "" ? "Not provided" : input;
}

function formatAddress(address) {
  if (!address) return null;
  if (typeof address === "string") return address;
  if (typeof address !== "object") return null;
  return [
    address.line1 || address.street || address.address_1 || address.address,
    address.line2 || address.address_2,
    [address.city, address.state, address.zip || address.postal_code].filter(Boolean).join(", "),
  ].filter(Boolean).join(" · ");
}

function textList(input) {
  if (Array.isArray(input)) return input.filter(Boolean).join(", ");
  if (input && typeof input === "object") return Object.values(input).filter(Boolean).join(", ");
  return input;
}

function fieldGrid(entries) {
  return entries.map(([label, content]) => `<div class="data-field"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value(content))}</strong></div>`).join("");
}

function renderLocations(locations) {
  if (!locations.length) {
    elements.locations.innerHTML = '<p class="address-value">No practice locations have been captured yet.</p>';
    return;
  }
  elements.locations.innerHTML = locations.map((location) => {
    const embeddedAddress = typeof location.display_name === "object" ? location.display_name : null;
    const title = typeof location.display_name === "string"
      ? location.display_name
      : typeof location.name === "string" ? location.name : "Practice location";
    const details = [formatAddress(location.address || embeddedAddress), textList(location.phone), textList(location.email)].filter(Boolean).join(" · ");
    const supporting = [location.practice_hours && `Hours: ${textList(location.practice_hours)}`, location.faxes && `Fax: ${textList(location.faxes)}`, location.languages && `Languages: ${textList(location.languages)}`].filter(Boolean).join(" · ");
    return `<article class="location-card"><span class="location-type">${escapeHtml(value(location.type || "Practice location"))}</span><h3>${escapeHtml(value(title))}</h3><p>${escapeHtml(value(details))}</p>${supporting ? `<span class="practice-note">${escapeHtml(supporting)}</span>` : ""}</article>`;
  }).join("");
}

function renderW9(group) {
  const w9 = group.w9 && typeof group.w9 === "object" ? group.w9 : null;
  if (!w9) {
    elements.w9.innerHTML = '<p class="address-value">No W-9 information has been captured yet.</p>';
    return;
  }
  elements.w9.innerHTML = fieldGrid([
    ["Legal name on W-9", w9.legal_name],
    ["Tax ID on W-9", w9.tax_id],
    ["Signed date", w9.signed_date],
    ["W-9 status", w9.status || "Captured"],
  ]);
}

function renderPractice(group) {
  const legalName = group.legal_name || group.name || "Unnamed practice";
  const locations = Array.isArray(group.locations) ? group.locations : [];
  elements.name.textContent = legalName;
  elements.crumbPractice.textContent = legalName;
  elements.subtitle.textContent = group.dba ? `DBA: ${group.dba}` : "Organization credentialing profile";
  elements.identity.innerHTML = fieldGrid([
    ["Legal name", legalName],
    ["DBA", group.dba],
    ["Type 2 NPI", group.type_2_npi],
    ["Tax ID number", group.tax_id],
  ]);
  renderLocations(locations);
  renderW9(group);
  elements.summary.innerHTML = [
    ["Locations", locations.length],
    ["Linked providers", group.provider_count || 0],
    ["W-9", group.w9 ? "Captured" : "Not captured"],
  ].map(([label, summaryValue]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value(summaryValue))}</strong></div>`).join("");
}

async function loadPractice() {
  if (!groupId) throw new Error("Practice ID is missing from the page address.");
  const [group, entity] = await Promise.all([
    fetch(`/entities/${encodeURIComponent(entityId)}/groups/${encodeURIComponent(groupId)}`).then((response) => {
      if (!response.ok) throw new Error(`Practice record could not be loaded (${response.status}).`);
      return response.json();
    }),
    fetch(`/entities/${encodeURIComponent(entityId)}`).then((response) => response.ok ? response.json() : { name: "Client" }),
  ]);
  elements.client.textContent = entity.name || entity.legal_name || "Client";
  renderPractice(group);
  elements.loading.hidden = true;
  elements.page.hidden = false;
}

loadPractice().catch((error) => {
  elements.loading.hidden = true;
  elements.error.hidden = false;
  elements.error.textContent = error.message;
});
