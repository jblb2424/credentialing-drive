const params = new URLSearchParams(window.location.search);
const entityId = params.get("entity") || "dummy-client";
const statusFilter = params.get("status");
const countElement = document.querySelector("#expiration-count");
const labelElement = document.querySelector("#expiration-label");
const eyebrowElement = document.querySelector("#expiration-eyebrow");
const tableElement = document.querySelector("#expiration-table-body");

function escapeHtml(value) {
  return String(value ?? "Not provided")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "Not provided";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" })
    .format(new Date(`${value}T00:00:00`));
}

function statusLabel(record) {
  if (record.type === "expired") return `${record.days_past_expiration} days past due`;
  if (record.type === "expiring") return `${record.days_until_expiration} days remaining`;
  return `${record.days_until_expiration} days remaining`;
}

function render(records) {
  if (statusFilter === "expiring") {
    records = records.filter((record) => record.type === "expiring");
    eyebrowElement.textContent = "Next 90 days";
    labelElement.textContent = "credentials expiring soon";
  }
  countElement.textContent = records.length;
  if (!records.length) {
    tableElement.innerHTML = '<tr><td colspan="5" class="record-table-empty">No credential expiration dates have been captured yet.</td></tr>';
    return;
  }
  tableElement.innerHTML = records.map((record) => `<tr class="expiration-row" tabindex="0" role="button" data-provider-id="${escapeHtml(record.provider_id)}"><td>${escapeHtml(record.credential_label || "Unclassified credential")}<span class="expiration-subtitle">${escapeHtml((record.credential_category || "unclassified").replaceAll("_", " "))}</span></td><td><strong>${escapeHtml(record.provider_name)}</strong><span class="expiration-subtitle">${escapeHtml(record.provider_npi || "NPI not captured")}</span></td><td class="expiration-date">${escapeHtml(formatDate(record.expiration_date))}</td><td><span class="severity-tag status-${escapeHtml(record.type)}">${escapeHtml(statusLabel(record))}</span></td><td class="open-record">→</td></tr>`).join("");
}

function openProvider(providerId) {
  window.location.assign(`/providers/${encodeURIComponent(providerId)}/view?entity=${encodeURIComponent(entityId)}`);
}

tableElement.addEventListener("click", (event) => {
  const row = event.target.closest("[data-provider-id]");
  if (row) openProvider(row.dataset.providerId);
});
tableElement.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    const row = event.target.closest("[data-provider-id]");
    if (row) openProvider(row.dataset.providerId);
  }
});

fetch(`/entities/${encodeURIComponent(entityId)}/expirations?limit=5000`)
  .then((response) => {
    if (!response.ok) throw new Error(`Expirations could not be loaded (${response.status}).`);
    return response.json();
  })
  .then((response) => render(response.expirations || []))
  .catch((error) => {
    tableElement.innerHTML = `<tr><td colspan="5" class="record-table-empty">${escapeHtml(error.message)}</td></tr>`;
  });
