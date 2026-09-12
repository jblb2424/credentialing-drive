const ENTITY_ID = "dummy-client";
const state = { entity: null, groups: [], providers: [] };

const elements = {
  entityName: document.querySelector("#entity-name"),
  providerCount: document.querySelector("#provider-count"),
  providerSupport: document.querySelector("#provider-support"),
  groupCount: document.querySelector("#group-count"),
  expiringCount: document.querySelector("#expiring-count"),
  issueCount: document.querySelector("#issue-count"),
  navIssueCount: document.querySelector("#nav-issue-count"),
  attentionList: document.querySelector("#attention-list"),
  providerTable: document.querySelector("#provider-table-body"),
  groupList: document.querySelector("#group-list"),
  search: document.querySelector("#provider-search"),
  refresh: document.querySelector("#refresh-button"),
  toast: document.querySelector("#toast"),
};

async function fetchJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json();
}

function escapeHtml(value) {
  return String(value ?? "Not provided")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function providerName(provider) {
  return provider.provider?.name || "Unnamed provider";
}

function providerIssues(provider) {
  return Array.isArray(provider.issues) ? provider.issues : [];
}

function humanizeField(value) {
  return String(value || "")
    .replace("provider.", "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function issueLabel(issue) {
  const field = humanizeField((issue.affected_fields || []).join(", "));
  const credential = issue.credential_label || field || "Credential";
  if (issue.type === "expiring") return `${credential} expires in ${issue.days_until_expiration} days`;
  if (issue.type === "expired") return `${credential} is expired`;
  if (issue.type === "missing_data") return `Missing ${field || "critical provider data"}`;
  if (issue.type === "discrepancy") return `Conflicting ${field || "provider information"}`;
  return issue.type?.replaceAll("_", " ") || "Record needs review";
}

function issuePriority(issue) {
  return { critical: 0, high: 1, medium: 2, low: 3 }[issue.severity] ?? 4;
}

function allIssues() {
  return state.providers.flatMap((provider) => providerIssues(provider).map((issue) => ({ provider, issue })))
    .sort((a, b) => issuePriority(a.issue) - issuePriority(b.issue));
}

function renderMetrics() {
  const issues = allIssues();
  const expiring = issues.filter(({ issue }) => issue.type === "expiring" || issue.type === "expired");
  elements.providerCount.textContent = state.providers.length;
  elements.providerSupport.textContent = state.providers.length === 1 ? "1 active provider record" : "Active provider records";
  elements.groupCount.textContent = state.groups.length;
  elements.expiringCount.textContent = expiring.length;
  elements.issueCount.textContent = issues.length;
  elements.navIssueCount.textContent = issues.length;
}

function renderAttention() {
  const issues = allIssues().slice(0, 5);
  if (!issues.length) {
    elements.attentionList.innerHTML = '<div class="empty-state">No outstanding issues. Provider records are in good standing.</div>';
    return;
  }
  elements.attentionList.innerHTML = issues.map(({ provider, issue }) => `
    <a class="attention-row" href="/providers/${encodeURIComponent(provider.id)}/issues/${encodeURIComponent(issue.id)}/view?entity=${ENTITY_ID}">
      <span class="attention-provider">${escapeHtml(providerName(provider))}<span>${escapeHtml(provider.provider?.npi || "NPI not yet captured")}</span></span>
      <span class="attention-detail">${escapeHtml(issueLabel(issue))}</span>
      <span class="severity-tag severity-${escapeHtml(issue.severity || "low")}">${escapeHtml(issue.severity || "review")}</span>
    </a>
  `).join("");
}

function recordHealth(provider) {
  const count = providerIssues(provider).length;
  return count ? `<span class="record-status needs-review">${count} open ${count === 1 ? "issue" : "issues"}</span>` : '<span class="record-status">In good standing</span>';
}

function renderProviders() {
  const query = elements.search.value.trim().toLowerCase();
  const providers = state.providers.filter((provider) => [providerName(provider), provider.provider?.npi, provider.provider?.credentials]
    .filter(Boolean).join(" ").toLowerCase().includes(query));
  if (!providers.length) {
    elements.providerTable.innerHTML = '<tr><td colspan="5" class="empty-state">No provider records match this search.</td></tr>';
    return;
  }
  elements.providerTable.innerHTML = providers.map((provider) => `
    <tr tabindex="0" role="button" data-provider-id="${escapeHtml(provider.id)}">
      <td>${escapeHtml(providerName(provider))}</td>
      <td>${escapeHtml(provider.provider?.npi || "Not provided")}</td>
      <td>${escapeHtml(provider.provider?.credentials || "—")}</td>
      <td>${recordHealth(provider)}</td>
      <td class="open-record">→</td>
    </tr>
  `).join("");
}

function renderGroups() {
  elements.groupList.innerHTML = state.groups.length
    ? state.groups.map((group) => `<span class="group-chip">${escapeHtml(group.legal_name || group.name || "Unnamed practice")}</span>`).join("")
    : '<span class="group-chip">No practices detected yet</span>';
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  window.setTimeout(() => elements.toast.classList.remove("show"), 3500);
}

function openProvider(providerId) {
  window.location.assign(`/providers/${encodeURIComponent(providerId)}/view?entity=${ENTITY_ID}`);
}

async function loadDashboard() {
  elements.refresh.disabled = true;
  try {
    const [entity, groupsResponse, providersResponse] = await Promise.all([
      fetchJson(`/entities/${ENTITY_ID}`),
      fetchJson(`/entities/${ENTITY_ID}/groups`),
      fetchJson(`/entities/${ENTITY_ID}/providers`),
    ]);
    state.entity = entity;
    state.groups = groupsResponse.groups || [];
    state.providers = providersResponse.providers || [];
    elements.entityName.textContent = entity.name || entity.legal_name || "team";
    renderMetrics();
    renderAttention();
    renderProviders();
    renderGroups();
  } catch (error) {
    elements.attentionList.innerHTML = `<div class="empty-state">Unable to load credentialing data. ${escapeHtml(error.message)}</div>`;
    elements.providerTable.innerHTML = '<tr><td colspan="5" class="empty-state">The provider directory is temporarily unavailable.</td></tr>';
    showToast("Could not refresh dashboard data.");
  } finally {
    elements.refresh.disabled = false;
  }
}

elements.refresh.addEventListener("click", loadDashboard);
elements.search.addEventListener("input", renderProviders);
elements.providerTable.addEventListener("click", (event) => {
  const row = event.target.closest("[data-provider-id]");
  if (row) openProvider(row.dataset.providerId);
});
elements.providerTable.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    const row = event.target.closest("[data-provider-id]");
    if (row) openProvider(row.dataset.providerId);
  }
});
loadDashboard();
