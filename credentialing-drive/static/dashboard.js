const ENTITY_ID = "dummy-client";
const state = { groups: [], providers: [] };

const elements = {
  greeting: document.querySelector("#dashboard-greeting"),
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

function timeOfDayGreeting(date = new Date()) {
  const hour = date.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

async function fetchJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json();
}

function escapeHtml(value) {
  return String(value ?? "Field value not captured")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function recordName(record) {
  return record.kind === "provider"
    ? record.data.provider?.name || "Unnamed provider"
    : record.data.legal_name || record.data.name || "Unnamed practice";
}

function recordIdentifier(record) {
  return record.kind === "provider"
    ? record.data.provider?.npi || "NPI not captured"
    : record.data.type_2_npi || "Type 2 NPI not captured";
}

function recordIssuePath(record, issue) {
  const path = record.kind === "provider" ? "providers" : "practices";
  return `/${path}/${encodeURIComponent(record.data.id)}/issues/${encodeURIComponent(issue.id)}/view?entity=${ENTITY_ID}`;
}

function humanizeField(value) {
  return String(value || "")
    .replace("provider.", "")
    .replace("group.", "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function issueLabel(issue) {
  const field = issue.display_label || humanizeField((issue.affected_fields || []).join(", "));
  const credential = issue.credential_label || field || "Credential";
  if (issue.type === "expiring") return `${credential} expires in ${issue.days_until_expiration} days`;
  if (issue.type === "expired") return `${credential} is expired`;
  if (issue.type === "missing_data") return `Missing ${field || "critical record data"}`;
  if (issue.type === "discrepancy") return `Conflicting ${field || "record information"}`;
  return issue.type?.replaceAll("_", " ") || "Record needs review";
}

function issueTypeLabel(issue) {
  return String(issue.type || "review").replaceAll("_", " ");
}

function issuePriority(issue) {
  return { critical: 0, high: 1, medium: 2, low: 3 }[issue.severity] ?? 4;
}

function allIssues() {
  const records = [
    ...state.providers.map((data) => ({ kind: "provider", data })),
    ...state.groups.map((data) => ({ kind: "practice", data })),
  ];
  return records.flatMap((record) => (record.data.issues || []).map((issue) => ({ record, issue })))
    .sort((a, b) => issuePriority(a.issue) - issuePriority(b.issue));
}

function setMetricValue(element, value) {
  element.classList.remove("is-loading");
  element.setAttribute("aria-busy", "false");
  element.textContent = value;
}

function setMetricLoading(element, label) {
  element.classList.add("is-loading");
  element.setAttribute("aria-busy", "true");
  element.innerHTML = `<span class="loading-skeleton loading-skeleton-metric" aria-hidden="true"></span><span class="sr-only">Loading ${label}</span>`;
}

function setDashboardLoading() {
  setMetricLoading(elements.groupCount, "practice count");
  setMetricLoading(elements.providerCount, "provider count");
  setMetricLoading(elements.issueCount, "issue count");
  setMetricLoading(elements.expiringCount, "expiring credential count");
  elements.providerSupport.classList.add("metric-support-loading");
  elements.providerSupport.innerHTML = '<span class="loading-skeleton loading-skeleton-support" aria-hidden="true"></span><span class="sr-only">Loading provider directory</span>';
  elements.attentionList.innerHTML = '<div class="loading-row"><span class="spinner"></span>Reviewing current credentialing records...</div>';
  elements.groupList.innerHTML = '<div class="loading-inline"><span class="spinner"></span>Loading practices...</div>';
}

function setMetricsUnavailable() {
  [elements.providerCount, elements.groupCount, elements.expiringCount, elements.issueCount]
    .forEach((element) => setMetricValue(element, "Unavailable"));
  elements.providerSupport.classList.remove("metric-support-loading");
  elements.providerSupport.textContent = "Credentialing data unavailable";
}

function renderMetrics() {
  const issues = allIssues();
  const expiring = issues.filter(({ issue }) => issue.type === "expiring");
  setMetricValue(elements.providerCount, state.providers.length);
  elements.providerSupport.textContent = state.providers.length === 1
    ? "1 active provider record"
    : "Browse all provider records";
  elements.providerSupport.classList.remove("metric-support-loading");
  setMetricValue(elements.groupCount, state.groups.length);
  setMetricValue(elements.expiringCount, expiring.length);
  setMetricValue(elements.issueCount, issues.length);
}

function renderAttention() {
  const issues = allIssues()
    .filter(({ issue }) => issue.severity === "critical")
    .slice(0, 5);
  if (!issues.length) {
    elements.attentionList.innerHTML = '<div class="empty-state">No critical issues. Provider and practice records are in good standing.</div>';
    return;
  }
  elements.attentionList.innerHTML = issues.map(({ record, issue }) => `
    <a class="attention-row ${issue.type === "expired" ? "is-expired" : ""}" href="${recordIssuePath(record, issue)}">
      <span class="attention-provider">${escapeHtml(recordName(record))}<span>${escapeHtml(record.kind === "provider" ? `NPI ${recordIdentifier(record)}` : `Practice · Type 2 NPI ${recordIdentifier(record)}`)}</span></span>
      <span class="attention-detail">${escapeHtml(issueLabel(issue))}</span>
      <span class="issue-tags"><span class="severity-tag severity-${escapeHtml(issue.severity || "low")}">${escapeHtml(issue.severity || "review")}</span><span class="issue-type-tag issue-type-${escapeHtml(issue.type || "review")}">${escapeHtml(issueTypeLabel(issue))}</span></span>
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
  setDashboardLoading();
  try {
    const [groupsResponse, providersResponse] = await Promise.all([
      fetchJson(`/entities/${ENTITY_ID}/groups`),
      fetchJson(`/entities/${ENTITY_ID}/providers?limit=500`),
    ]);
    state.groups = groupsResponse.groups || [];
    state.providers = providersResponse.providers || [];
    renderMetrics();
    renderAttention();
    renderGroups();
  } catch (error) {
    setMetricsUnavailable();
    elements.attentionList.innerHTML = `<div class="empty-state">Unable to load credentialing data. ${escapeHtml(error.message)}</div>`;
    elements.groupList.innerHTML = '<span class="group-chip">Practice data unavailable</span>';
    showToast("Could not refresh dashboard data.");
  } finally {
    elements.refresh.disabled = false;
  }
}

elements.refresh.addEventListener("click", loadDashboard);
elements.greeting.textContent = timeOfDayGreeting();
loadDashboard();
