const params = new URLSearchParams(window.location.search);
const entityId = params.get("entity") || "dummy-client";
const PAGE_SIZE = 12;

const elements = {
  count: document.querySelector("#issue-count"),
  providerCount: document.querySelector("#provider-count"),
  expiredCount: document.querySelector("#expired-count"),
  expiringCount: document.querySelector("#expiring-count"),
  heading: document.querySelector("#issue-results-heading"),
  groups: document.querySelector("#issue-groups"),
  search: document.querySelector("#issue-search"),
  severity: document.querySelector("#severity-filter"),
  type: document.querySelector("#type-filter"),
  recordType: document.querySelector("#record-type-filter"),
  quickFilters: [...document.querySelectorAll("[data-quick-filter]")],
  resultCount: document.querySelector("#issue-result-count"),
  loadMore: document.querySelector("#load-more-issues"),
};

let providers = [];
let practices = [];
let quickFilter = "all";
let recordType = "all";
let visibleGroups = PAGE_SIZE;
const expandedGroups = new Set();

const requestedSeverity = params.get("severity");
if (["critical", "high", "medium", "low"].includes(requestedSeverity)) {
  elements.severity.value = requestedSeverity;
}

function setSummaryValue(element, value) {
  element.classList.remove("is-loading");
  element.setAttribute("aria-busy", "false");
  element.textContent = value;
}

function escapeHtml(value) {
  return String(value ?? "Field value not captured")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function humanizeField(value) {
  return String(value || "record information")
    .replace("provider.", "").replace("group.", "")
    .replace(/^provider_locations\[[^\]]+\]\./, "location ")
    .replace(/^locations(?:\[[^\]]+\]|\.[^.]+)\./, "location ")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function issueLabel(issue) {
  const field = issue.display_label || humanizeField((issue.affected_fields || []).join(", "));
  if (issue.type === "expired") return `${issue.credential_label || field} is expired`;
  if (issue.type === "expiring") return `${issue.credential_label || field} expires soon`;
  if (issue.type === "missing_data") return `Missing ${field}`;
  if (issue.type === "discrepancy") return `Conflicting ${field}`;
  return issue.type?.replaceAll("_", " ") || "Needs review";
}

function issueTypeLabel(issue) {
  return String(issue.type || "review").replaceAll("_", " ");
}

function priority(issue) {
  return { critical: 0, high: 1, medium: 2, low: 3 }[issue.severity] ?? 4;
}

function sourceContext(issue) {
  return issue.type === "missing_data" ? "Not applicable" : issue.file_name || "Source file not captured";
}

function recordName(record) {
  return record.kind === "provider"
    ? record.data.provider?.name || "Provider name not captured"
    : record.data.legal_name || record.data.name || "Practice name not captured";
}

function recordIdentifier(record) {
  return record.kind === "provider"
    ? record.data.provider?.npi || "NPI not captured"
    : record.data.type_2_npi || "Type 2 NPI not captured";
}

function recordLabel(record) {
  return record.kind === "provider" ? "Provider" : "Practice";
}

function recordUrl(record) {
  const path = record.kind === "provider" ? "providers" : "practices";
  return `/${path}/${encodeURIComponent(record.data.id)}/view?entity=${encodeURIComponent(entityId)}`;
}

function locationLabel(location) {
  if (!location || typeof location !== "object") return null;
  for (const field of ["display_name", "name", "location_name"]) {
    if (typeof location[field] === "string" && location[field].trim()) return location[field].trim();
  }
  const address = location.address || location.display_name;
  if (typeof address === "string" && address.trim()) return address.trim();
  if (address && typeof address === "object") {
    const street = address.line1 || address.street || address.address;
    const locality = [address.city, address.state].filter(Boolean).join(", ");
    return [street, locality].filter(Boolean).join(", ") || null;
  }
  return null;
}

function locationForIssue(record, issue) {
  const fieldPath = (issue.affected_fields || [])[0] || "";
  const indexMatch = fieldPath.match(/^(?:provider_locations|locations)\[(\d+)]\./);
  const idMatch = fieldPath.match(/^locations\.([^.]+)\./);
  const locations = record.kind === "provider" ? record.data.provider_locations : record.data.locations;
  let location;
  if (indexMatch) location = locations?.[Number(indexMatch[1])];
  if (idMatch) location = locations?.find((candidate) => candidate.id === idMatch[1]);
  return locationLabel(location);
}

function issueScope(record, issue) {
  const location = locationForIssue(record, issue);
  return location ? `Location: ${location}` : `${recordLabel(record)} record`;
}

function issueMatches(issue, record, query) {
  if (elements.severity.value && issue.severity !== elements.severity.value) return false;
  if (elements.type.value && issue.type !== elements.type.value) return false;
  if (quickFilter === "expired" && issue.type !== "expired") return false;
  if (quickFilter === "expiring" && issue.type !== "expiring") return false;
  const searchable = [recordName(record), recordIdentifier(record), issueLabel(issue), issueScope(record, issue), sourceContext(issue)]
    .join(" ").toLowerCase();
  return !query || searchable.includes(query);
}

function recordsForCurrentFilter() {
  if (recordType === "provider") return providers.map((data) => ({ kind: "provider", data }));
  if (recordType === "practice") return practices.map((data) => ({ kind: "practice", data }));
  return [
    ...providers.map((data) => ({ kind: "provider", data })),
    ...practices.map((data) => ({ kind: "practice", data })),
  ];
}

function visibleIssueRecords() {
  const query = elements.search.value.trim().toLowerCase();
  return recordsForCurrentFilter().flatMap((record) => (record.data.issues || [])
    .filter((issue) => issueMatches(issue, record, query))
    .map((issue) => ({ record, issue })));
}

function groupByRecord(records) {
  const grouped = new Map();
  for (const item of records) {
    const key = `${item.record.kind}:${item.record.data.id}`;
    if (!grouped.has(key)) grouped.set(key, { record: item.record, issues: [], scope: recordLabel(item.record) });
    grouped.get(key).issues.push(item.issue);
  }
  return [...grouped.values()];
}

function sortGroups(groups) {
  return groups
    .map((group) => ({ ...group, issues: [...group.issues].sort((left, right) => priority(left) - priority(right)) }))
    .sort((left, right) => {
      const priorityDifference = priority(left.issues[0]) - priority(right.issues[0]);
      if (priorityDifference) return priorityDifference;
      const countDifference = right.issues.length - left.issues.length;
      return countDifference || recordName(left.record).localeCompare(recordName(right.record));
    });
}

function issueRow(record, issue) {
  return `<button class="issue-group-row ${issue.type === "expired" ? "is-expired" : ""}" type="button" data-record-kind="${escapeHtml(record.kind)}" data-record-id="${escapeHtml(record.data.id)}" data-issue-id="${escapeHtml(issue.id)}"><span class="issue-row-summary"><strong>${escapeHtml(issueLabel(issue))}</strong><span class="issue-scope">${escapeHtml(issueScope(record, issue))}</span></span><span class="issue-row-context"><span class="issue-tags"><span class="severity-tag severity-${escapeHtml(issue.severity || "low")}">${escapeHtml(issue.severity || "review")}</span><span class="issue-type-tag issue-type-${escapeHtml(issue.type || "review")}">${escapeHtml(issueTypeLabel(issue))}</span></span><span class="issue-source-context">${escapeHtml(sourceContext(issue))}</span><span class="open-record" aria-hidden="true">→</span></span></button>`;
}

function groupTitle(group) {
  return recordName(group.record);
}

function groupDescription(group) {
  const identifierLabel = group.record.kind === "provider" ? "NPI" : "Type 2 NPI";
  return `${identifierLabel} ${recordIdentifier(group.record)}`;
}

function groupKey(group) {
  return `${recordType}:${group.record.kind}:${group.record.data.id}:${group.scope}`;
}

function issueGroup(group) {
  const issueCount = `${group.issues.length} ${group.issues.length === 1 ? "issue" : "issues"}`;
  const eyebrow = recordLabel(group.record);
  const key = groupKey(group);
  const expanded = expandedGroups.has(key);
  return `<article class="issue-provider-group ${expanded ? "is-expanded" : ""}"><button class="issue-provider-heading" type="button" data-group-toggle="${escapeHtml(key)}" aria-expanded="${expanded}"><span><span class="eyebrow">${eyebrow}</span><strong>${escapeHtml(groupTitle(group))}</strong><small>${escapeHtml(groupDescription(group))}</small></span><span class="issue-provider-actions"><span class="issue-count-badge">${escapeHtml(issueCount)}</span><span class="group-expand-label">View issues <b>⌄</b></span></span></button><div class="issue-group-body" ${expanded ? "" : "hidden"}><div class="issue-group-list">${group.issues.map((issue) => issueRow(group.record, issue)).join("")}</div><a class="issue-provider-record-link" href="${recordUrl(group.record)}">Open ${recordLabel(group.record).toLowerCase()} record →</a></div></article>`;
}

function updateSummary(records) {
  const allRecords = [
    ...providers.map((data) => ({ kind: "provider", data })),
    ...practices.map((data) => ({ kind: "practice", data })),
  ];
  const allIssues = allRecords.flatMap((record) => record.data.issues || []);
  const affectedRecords = new Set(allRecords.filter((record) => record.data.issues?.length).map((record) => `${record.kind}:${record.data.id}`));
  setSummaryValue(elements.count, allIssues.length);
  setSummaryValue(elements.providerCount, affectedRecords.size);
  setSummaryValue(elements.expiredCount, allIssues.filter((issue) => issue.type === "expired").length);
  setSummaryValue(elements.expiringCount, allIssues.filter((issue) => issue.type === "expiring").length);
  elements.quickFilters.forEach((button) => button.classList.toggle("is-active", button.dataset.quickFilter === quickFilter));
  const shownLabel = recordType === "practice" ? "practices" : recordType === "provider" ? "providers" : "all records";
  elements.heading.textContent = `${records.length} ${records.length === 1 ? "issue" : "issues"} across ${shownLabel}`;
}

function render() {
  const records = visibleIssueRecords();
  const groups = sortGroups(groupByRecord(records));
  updateSummary(records);
  if (!records.length) {
    elements.groups.innerHTML = '<div class="workspace-empty"><div><strong>No matching issues</strong><p>Try clearing a filter or selecting another grouping.</p></div></div>';
    elements.resultCount.textContent = "";
    elements.loadMore.hidden = true;
    return;
  }
  const shownGroups = groups.slice(0, visibleGroups);
  elements.groups.innerHTML = shownGroups.map(issueGroup).join("");
  const groupLabel = recordType === "practice" ? "practices" : recordType === "provider" ? "providers" : "records";
  elements.resultCount.textContent = `Showing ${shownGroups.length} of ${groups.length} ${groupLabel}`;
  elements.loadMore.hidden = shownGroups.length >= groups.length;
}

function resetVisibleGroups() {
  visibleGroups = PAGE_SIZE;
}

function openIssue(kind, recordId, issueId) {
  const path = kind === "provider" ? "providers" : "practices";
  window.location.assign(`/${path}/${encodeURIComponent(recordId)}/issues/${encodeURIComponent(issueId)}/view?entity=${encodeURIComponent(entityId)}`);
}

elements.groups.addEventListener("click", (event) => {
  const groupToggle = event.target.closest("[data-group-toggle]");
  if (groupToggle) {
    const { groupToggle: key } = groupToggle.dataset;
    if (expandedGroups.has(key)) expandedGroups.delete(key);
    else expandedGroups.add(key);
    render();
    return;
  }
  const row = event.target.closest("[data-issue-id]");
  if (row) openIssue(row.dataset.recordKind, row.dataset.recordId, row.dataset.issueId);
});

elements.search.addEventListener("input", () => { resetVisibleGroups(); render(); });
elements.severity.addEventListener("change", () => { resetVisibleGroups(); render(); });
elements.type.addEventListener("change", () => { resetVisibleGroups(); render(); });
elements.recordType.addEventListener("change", () => {
  recordType = elements.recordType.value;
  resetVisibleGroups();
  render();
});
elements.quickFilters.forEach((button) => button.addEventListener("click", () => {
  quickFilter = button.dataset.quickFilter;
  if (quickFilter === "providers") {
    elements.type.value = "";
    elements.severity.value = "";
    recordType = "provider";
    elements.recordType.value = "provider";
  }
  resetVisibleGroups();
  render();
}));
elements.loadMore.addEventListener("click", () => { visibleGroups += PAGE_SIZE; render(); });

Promise.all([
  fetch(`/entities/${encodeURIComponent(entityId)}/providers?limit=500`).then((response) => {
    if (!response.ok) throw new Error(`Provider issues could not be loaded (${response.status}).`);
    return response.json();
  }),
  fetch(`/entities/${encodeURIComponent(entityId)}/groups?limit=500`).then((response) => {
    if (!response.ok) throw new Error(`Practice issues could not be loaded (${response.status}).`);
    return response.json();
  }),
])
  .then(([providerResponse, groupResponse]) => {
    providers = providerResponse.providers || [];
    practices = groupResponse.groups || [];
    render();
  })
  .catch((error) => {
    elements.heading.textContent = "Issues unavailable";
    elements.groups.innerHTML = `<div class="workspace-empty">${escapeHtml(error.message)}</div>`;
  });
