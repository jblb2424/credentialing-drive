const params = new URLSearchParams(window.location.search);
const entityId = params.get("entity") || "dummy-client";
const countElement = document.querySelector("#issue-count");
const tableElement = document.querySelector("#issue-table-body");

function escapeHtml(value) {
  return String(value ?? "Not captured")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function humanizeField(value) {
  return String(value || "provider information")
    .replace("provider.", "").replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function issueLabel(issue) {
  const field = humanizeField((issue.affected_fields || []).join(", "));
  if (issue.type === "expired") return `${issue.credential_label || field} is expired`;
  if (issue.type === "expiring") return `${issue.credential_label || field} expires soon`;
  if (issue.type === "missing_data") return `Missing ${field}`;
  if (issue.type === "discrepancy") return `Conflicting ${field}`;
  return issue.type?.replaceAll("_", " ") || "Needs review";
}

function priority(issue) {
  return { critical: 0, high: 1, medium: 2, low: 3 }[issue.severity] ?? 4;
}

function render(providers) {
  const issues = providers.flatMap((provider) => (provider.issues || []).map((issue) => ({ provider, issue })))
    .sort((left, right) => priority(left.issue) - priority(right.issue));
  countElement.textContent = issues.length;
  if (!issues.length) {
    tableElement.innerHTML = '<tr><td colspan="5" class="record-table-empty">No open issues. This client is in good standing.</td></tr>';
    return;
  }
  tableElement.innerHTML = issues.map(({ provider, issue }) => `<tr class="expiration-row" tabindex="0" role="button" data-provider-id="${escapeHtml(provider.id)}" data-issue-id="${escapeHtml(issue.id)}"><td><strong>${escapeHtml(issueLabel(issue))}</strong></td><td>${escapeHtml(provider.provider?.name || "Unnamed provider")}</td><td><span class="severity-tag severity-${escapeHtml(issue.severity || "low")}">${escapeHtml(issue.severity || "review")}</span></td><td>${escapeHtml(issue.file_name || "Not captured")}</td><td class="open-record">→</td></tr>`).join("");
}

function openIssue(providerId, issueId) {
  window.location.assign(`/providers/${encodeURIComponent(providerId)}/issues/${encodeURIComponent(issueId)}/view?entity=${encodeURIComponent(entityId)}`);
}

tableElement.addEventListener("click", (event) => {
  const row = event.target.closest("[data-issue-id]");
  if (row) openIssue(row.dataset.providerId, row.dataset.issueId);
});
tableElement.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    const row = event.target.closest("[data-issue-id]");
    if (row) openIssue(row.dataset.providerId, row.dataset.issueId);
  }
});

fetch(`/entities/${encodeURIComponent(entityId)}/providers?limit=500`)
  .then((response) => {
    if (!response.ok) throw new Error(`Open issues could not be loaded (${response.status}).`);
    return response.json();
  })
  .then((response) => render(response.providers || []))
  .catch((error) => {
    tableElement.innerHTML = `<tr><td colspan="5" class="record-table-empty">${escapeHtml(error.message)}</td></tr>`;
  });
