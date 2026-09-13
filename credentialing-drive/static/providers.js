const params = new URLSearchParams(window.location.search);
const entityId = params.get("entity") || "dummy-client";
const elements = {
  count: document.querySelector("#provider-count"),
  search: document.querySelector("#provider-search"),
  table: document.querySelector("#provider-table-body"),
};
let providers = [];

function escapeHtml(value) {
  return String(value ?? "Not provided")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function providerName(provider) {
  return provider.provider?.name || "Unnamed provider";
}

function recordHealth(provider) {
  const count = Array.isArray(provider.issues) ? provider.issues.length : 0;
  return count
    ? `<span class="record-status needs-review">${count} open ${count === 1 ? "issue" : "issues"}</span>`
    : '<span class="record-status">In good standing</span>';
}

function renderProviders() {
  const query = elements.search.value.trim().toLowerCase();
  const visible = providers.filter((provider) => [providerName(provider), provider.provider?.npi, provider.provider?.credentials]
    .filter(Boolean).join(" ").toLowerCase().includes(query));
  elements.count.textContent = providers.length;
  if (!visible.length) {
    elements.table.innerHTML = '<tr><td colspan="5" class="record-table-empty">No provider records match this search.</td></tr>';
    return;
  }
  elements.table.innerHTML = visible.map((provider) => `<tr class="expiration-row" tabindex="0" role="button" data-provider-id="${escapeHtml(provider.id)}"><td>${escapeHtml(providerName(provider))}</td><td>${escapeHtml(provider.provider?.npi || "Not provided")}</td><td>${escapeHtml(provider.provider?.credentials || "—")}</td><td>${recordHealth(provider)}</td><td class="open-record">→</td></tr>`).join("");
}

function openProvider(providerId) {
  window.location.assign(`/providers/${encodeURIComponent(providerId)}/view?entity=${encodeURIComponent(entityId)}`);
}

elements.search.addEventListener("input", renderProviders);
elements.table.addEventListener("click", (event) => {
  const row = event.target.closest("[data-provider-id]");
  if (row) openProvider(row.dataset.providerId);
});
elements.table.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    const row = event.target.closest("[data-provider-id]");
    if (row) openProvider(row.dataset.providerId);
  }
});

fetch(`/entities/${encodeURIComponent(entityId)}/providers?limit=500`)
  .then((response) => {
    if (!response.ok) throw new Error(`Provider directory could not be loaded (${response.status}).`);
    return response.json();
  })
  .then((response) => {
    providers = response.providers || [];
    renderProviders();
  })
  .catch((error) => {
    elements.table.innerHTML = `<tr><td colspan="5" class="record-table-empty">${escapeHtml(error.message)}</td></tr>`;
  });
