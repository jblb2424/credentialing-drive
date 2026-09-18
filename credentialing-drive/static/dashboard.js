const ENTITY_ID = "dummy-client";
const state = { entity: null, groups: [], providers: [] };

const elements = {
  entityName: document.querySelector("#entity-name"),
  providerCount: document.querySelector("#provider-count"),
  providerSupport: document.querySelector("#provider-support"),
  groupCount: document.querySelector("#group-count"),
  expiringCount: document.querySelector("#expiring-count"),
  issueCount: document.querySelector("#issue-count"),
  attentionList: document.querySelector("#attention-list"),
  groupList: document.querySelector("#group-list"),
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
  const expiring = issues.filter(({ issue }) => issue.type === "expiring");
  elements.providerCount.textContent = state.providers.length;
  elements.providerSupport.textContent = state.providers.length === 1 ? "1 active provider record" : "Active provider records";
  elements.groupCount.textContent = state.groups.length;
  elements.expiringCount.textContent = expiring.length;
  elements.issueCount.textContent = issues.length;
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

function renderGroups() {
  elements.groupList.innerHTML = state.groups.length
    ? state.groups.map((group) => `<a class="group-chip" href="/practices/${encodeURIComponent(group.id)}/view?entity=${ENTITY_ID}">${escapeHtml(group.legal_name || group.name || "Unnamed practice")}<span aria-hidden="true">→</span></a>`).join("")
    : '<span class="group-chip">No practices detected yet</span>';
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  window.setTimeout(() => elements.toast.classList.remove("show"), 3500);
}

async function loadDashboard() {
  elements.refresh.disabled = true;
  try {
    const [entity, groupsResponse, providersResponse] = await Promise.all([
      fetchJson(`/entities/${ENTITY_ID}`),
      fetchJson(`/entities/${ENTITY_ID}/groups`),
      fetchJson(`/entities/${ENTITY_ID}/providers?limit=500`),
    ]);
    state.entity = entity;
    state.groups = groupsResponse.groups || [];
    state.providers = providersResponse.providers || [];
    elements.entityName.textContent = entity.name || entity.legal_name || "team";
    renderMetrics();
    renderAttention();
    renderGroups();
  } catch (error) {
    elements.attentionList.innerHTML = `<div class="empty-state">Unable to load credentialing data. ${escapeHtml(error.message)}</div>`;
    showToast("Could not refresh dashboard data.");
  } finally {
    elements.refresh.disabled = false;
  }
}

elements.refresh.addEventListener("click", loadDashboard);
loadDashboard();
