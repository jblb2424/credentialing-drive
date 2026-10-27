const params = new URLSearchParams(window.location.search);
const entityId = params.get("entity") || "dummy-client";
const recordId = params.get("record_id");
const scopedRecordType = params.get("record_type");
const PAGE_SIZE = 25;
const state = { activity: [], recordName: null, visible: PAGE_SIZE };

const elements = {
  count: document.querySelector("#activity-count"),
  heading: document.querySelector("#activity-heading"),
  description: document.querySelector("#activity-description"),
  search: document.querySelector("#activity-search"),
  recordType: document.querySelector("#activity-record-type"),
  recordTypeControl: document.querySelector("#activity-record-type-control"),
  category: document.querySelector("#activity-category"),
  body: document.querySelector("#activity-table-body"),
  resultCount: document.querySelector("#activity-result-count"),
  loadMore: document.querySelector("#load-more-activity"),
};

function escapeHtml(value) {
  return String(value ?? "Not captured")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function categoryLabel(value) {
  const labels = { w_9: "W-9", cv_or_resume: "CV / resume", dea_registration: "DEA registration" };
  return labels[value] || String(value || "other").replaceAll("_", " ");
}

function fieldLabel(fieldPath) {
  return String(fieldPath || "record information")
    .replace(/^provider\./, "")
    .replace(/^group\./, "")
    .replace(/^locations\.[^.]+\./, "location ")
    .replace(/^provider_locations\./, "provider locations")
    .replaceAll("_", " ");
}

function recordTypeLabel(value) {
  return value === "practice" ? "Practice" : "Provider";
}

function formatTimestamp(value) {
  if (!value) return "Date not recorded";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Date not recorded";
  return new Intl.DateTimeFormat(undefined, {
    month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
  }).format(date);
}

function matchingActivity() {
  const query = elements.search.value.trim().toLowerCase();
  return state.activity.filter((activity) => {
    if (elements.recordType.value !== "all" && activity.record_type !== elements.recordType.value) return false;
    if (elements.category.value && activity.document_category !== elements.category.value) return false;
    const searchable = [
      activity.record_name,
      activity.file_name,
      categoryLabel(activity.document_category),
      ...(activity.changed_fields || []).map(fieldLabel),
    ].join(" ").toLowerCase();
    return !query || searchable.includes(query);
  });
}

function renderCount(count) {
  elements.count.classList.remove("is-loading");
  elements.count.setAttribute("aria-busy", "false");
  elements.count.textContent = count;
}

function changesMarkup(activity) {
  const fields = (activity.changed_fields || []).map(fieldLabel);
  if (!fields.length) return '<span class="activity-change-empty">Imported record data</span>';
  const shown = fields.slice(0, 3);
  const additional = fields.length - shown.length;
  return `<span class="activity-changes">${shown.map((field) => `<span>${escapeHtml(field)}</span>`).join("")}${additional ? `<span>+${additional} more</span>` : ""}</span>`;
}

function render() {
  const activity = matchingActivity();
  renderCount(activity.length);
  if (!activity.length) {
    elements.body.innerHTML = '<tr><td colspan="5" class="record-table-empty">No revisions match the selected filters.</td></tr>';
    elements.resultCount.textContent = "";
    elements.loadMore.hidden = true;
    return;
  }
  const visible = activity.slice(0, state.visible);
  elements.body.innerHTML = visible.map((entry) => `<tr class="activity-row"><td><span class="activity-date">${escapeHtml(formatTimestamp(entry.recorded_at))}</span></td><td><strong>${escapeHtml(entry.record_name)}</strong><span class="activity-record-type">${escapeHtml(recordTypeLabel(entry.record_type))}</span></td><td>${escapeHtml(entry.file_name || "File name not captured")}</td><td><span class="activity-category">${escapeHtml(categoryLabel(entry.document_category))}</span></td><td>${changesMarkup(entry)}</td></tr>`).join("");
  elements.resultCount.textContent = `Showing ${visible.length} of ${activity.length} revisions`;
  elements.loadMore.hidden = visible.length >= activity.length;
}

function resetVisible() {
  state.visible = PAGE_SIZE;
}

function populateCategories() {
  const categories = [...new Set(state.activity.map((entry) => entry.document_category).filter(Boolean))]
    .sort((left, right) => categoryLabel(left).localeCompare(categoryLabel(right)));
  elements.category.insertAdjacentHTML("beforeend", categories.map((category) => `<option value="${escapeHtml(category)}">${escapeHtml(categoryLabel(category))}</option>`).join(""));
}

function isScopedRecord() {
  return Boolean(recordId) && ["provider", "practice"].includes(scopedRecordType);
}

function configureScope() {
  if (!isScopedRecord()) return;
  elements.recordType.value = scopedRecordType;
  elements.recordType.disabled = true;
  elements.recordTypeControl.hidden = true;
}

function renderScopeCopy() {
  if (!isScopedRecord()) return;
  const recordName = state.recordName || state.activity[0]?.record_name || recordTypeLabel(scopedRecordType);
  const label = recordTypeLabel(scopedRecordType).toLowerCase();
  elements.heading.textContent = `${recordName} activity`;
  elements.description.textContent = `Every document-driven change recorded for this ${label}.`;
  document.title = `${recordName} Activity | Cozema Health`;
}

async function loadActivity() {
  try {
    const path = isScopedRecord()
      ? `/entities/${encodeURIComponent(entityId)}/${scopedRecordType === "practice" ? "groups" : "providers"}/${encodeURIComponent(recordId)}/activity?limit=500`
      : `/entities/${encodeURIComponent(entityId)}/activity?limit=500`;
    const response = await fetch(path, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`Activity could not be loaded (${response.status}).`);
    const payload = await response.json();
    state.activity = payload.activity || [];
    state.recordName = payload.record?.record_name || null;
    renderScopeCopy();
    populateCategories();
    render();
  } catch (error) {
    elements.count.classList.remove("is-loading");
    elements.count.textContent = "Unavailable";
    elements.body.innerHTML = `<tr><td colspan="5" class="record-table-empty">${escapeHtml(error.message)}</td></tr>`;
  }
}

elements.search.addEventListener("input", () => { resetVisible(); render(); });
elements.recordType.addEventListener("change", () => { resetVisible(); render(); });
elements.category.addEventListener("change", () => { resetVisible(); render(); });
elements.loadMore.addEventListener("click", () => { state.visible += PAGE_SIZE; render(); });
configureScope();
loadActivity();
