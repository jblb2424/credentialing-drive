const params = new URLSearchParams(window.location.search);
const entityId = params.get("entity") || "dummy-client";
const state = { catalog: [], report: null };

const elements = {
  templates: document.querySelector("#report-templates"),
  form: document.querySelector("#report-form"),
  reportType: document.querySelector("#report-type"),
  practice: document.querySelector("#practice-id"),
  provider: document.querySelector("#provider-id"),
  issueType: document.querySelector("#issue-type"),
  expirationStatus: document.querySelector("#expiration-status"),
  description: document.querySelector("#report-description"),
  export: document.querySelector("#export-report"),
  previewTitle: document.querySelector("#report-preview-title"),
  count: document.querySelector("#report-row-count"),
  tableHead: document.querySelector("#report-table-head"),
  tableBody: document.querySelector("#report-table-body"),
};

function escapeHtml(value) {
  return String(value ?? "Not captured")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

async function fetchJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json();
}

function formParams() {
  const query = new URLSearchParams();
  [["practice_id", elements.practice.value], ["provider_id", elements.provider.value], ["issue_type", elements.issueType.value], ["expiration_status", elements.expirationStatus.value]]
    .filter(([, value]) => value)
    .forEach(([key, value]) => query.set(key, value));
  return query;
}

function selectedDefinition() {
  return state.catalog.find((definition) => definition.id === elements.reportType.value);
}

function refreshBuilderCopy() {
  const definition = selectedDefinition();
  elements.description.textContent = definition?.description || "Select a report template to begin.";
}

function renderTemplates() {
  elements.templates.innerHTML = state.catalog.map((definition) => `<button class="report-template ${definition.id === elements.reportType.value ? "selected" : ""}" type="button" data-report-type="${escapeHtml(definition.id)}"><span>Template</span><strong>${escapeHtml(definition.title)}</strong><p>${escapeHtml(definition.description)}</p><b>Use report →</b></button>`).join("");
}

function setReportCount(value) {
  elements.count.classList.remove("is-loading");
  elements.count.setAttribute("aria-busy", "false");
  elements.count.textContent = value;
}

function setReportLoading() {
  elements.count.classList.add("is-loading");
  elements.count.setAttribute("aria-busy", "true");
  elements.count.innerHTML = '<span class="loading-skeleton" aria-hidden="true"></span><span class="sr-only">Loading report preview</span>';
}

function renderReport() {
  const report = state.report;
  elements.previewTitle.textContent = report.title;
  setReportCount(`${report.row_count} ${report.row_count === 1 ? "row" : "rows"}`);
  elements.tableHead.innerHTML = `<tr>${report.columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("")}</tr>`;
  if (!report.rows.length) {
    elements.tableBody.innerHTML = `<tr><td colspan="${report.columns.length}" class="record-table-empty">No records match the current report filters.</td></tr>`;
    return;
  }
  elements.tableBody.innerHTML = report.rows.map((row) => `<tr class="report-row" ${row._provider_id ? `data-provider-id="${escapeHtml(row._provider_id)}" tabindex="0" role="button"` : ""}>${report.columns.map((column) => `<td>${escapeHtml(row[column.key])}</td>`).join("")}</tr>`).join("");
}

async function previewReport() {
  const reportType = elements.reportType.value;
  if (!reportType) return;
  elements.export.disabled = true;
  elements.previewTitle.textContent = "Building report";
  setReportLoading();
  elements.tableHead.innerHTML = "";
  elements.tableBody.innerHTML = '<tr><td class="record-table-empty"><span class="spinner"></span>Building live report preview...</td></tr>';
  try {
    const query = formParams();
    const report = await fetchJson(`/entities/${encodeURIComponent(entityId)}/reports/${encodeURIComponent(reportType)}?${query}`);
    state.report = report;
    elements.export.disabled = false;
    renderReport();
  } catch (error) {
    elements.previewTitle.textContent = "Report unavailable";
    setReportCount("Unavailable");
    elements.tableBody.innerHTML = `<tr><td class="record-table-empty">${escapeHtml(error.message)}</td></tr>`;
  }
}

async function initialize() {
  try {
    const [catalogResponse, groupsResponse, providersResponse] = await Promise.all([
      fetchJson(`/entities/${encodeURIComponent(entityId)}/reports`),
      fetchJson(`/entities/${encodeURIComponent(entityId)}/groups?limit=500`),
      fetchJson(`/entities/${encodeURIComponent(entityId)}/providers?limit=500`),
    ]);
    state.catalog = catalogResponse.reports || [];
    elements.reportType.innerHTML = state.catalog.map((definition) => `<option value="${escapeHtml(definition.id)}">${escapeHtml(definition.title)}</option>`).join("");
    elements.practice.insertAdjacentHTML("beforeend", (groupsResponse.groups || []).map((group) => `<option value="${escapeHtml(group.id)}">${escapeHtml(group.legal_name || group.name || "Unnamed practice")}</option>`).join(""));
    elements.provider.insertAdjacentHTML("beforeend", (providersResponse.providers || []).map((provider) => `<option value="${escapeHtml(provider.id)}">${escapeHtml(provider.provider?.name || "Unnamed provider")}</option>`).join(""));
    refreshBuilderCopy();
    renderTemplates();
    previewReport();
  } catch (error) {
    elements.templates.innerHTML = `<div class="empty-state">Unable to load report builder. ${escapeHtml(error.message)}</div>`;
  }
}

elements.reportType.addEventListener("change", () => {
  refreshBuilderCopy();
  renderTemplates();
  state.report = null;
  elements.export.disabled = true;
});
elements.templates.addEventListener("click", (event) => {
  const template = event.target.closest("[data-report-type]");
  if (!template) return;
  elements.reportType.value = template.dataset.reportType;
  refreshBuilderCopy();
  renderTemplates();
  previewReport();
});
elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  previewReport();
});
elements.export.addEventListener("click", () => {
  const query = formParams();
  const reportType = elements.reportType.value;
  window.location.assign(`/entities/${encodeURIComponent(entityId)}/reports/${encodeURIComponent(reportType)}/export?${query}`);
});
elements.tableBody.addEventListener("click", (event) => {
  const row = event.target.closest("[data-provider-id]");
  if (row) window.location.assign(`/providers/${encodeURIComponent(row.dataset.providerId)}/view?entity=${encodeURIComponent(entityId)}`);
});
elements.tableBody.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const row = event.target.closest("[data-provider-id]");
  if (row) window.location.assign(`/providers/${encodeURIComponent(row.dataset.providerId)}/view?entity=${encodeURIComponent(entityId)}`);
});

initialize();
