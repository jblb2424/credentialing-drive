const query = new URLSearchParams(window.location.search);
const entityId = query.get("entity") || "dummy-client";
const pathSegments = window.location.pathname.split("/");
const recordKind = pathSegments[1] === "practices" ? "practice" : "provider";
const recordId = pathSegments[2];
const issueId = pathSegments[4];

const elements = {
  loading: document.querySelector("#issue-loading"),
  error: document.querySelector("#issue-error"),
  content: document.querySelector("#issue-content"),
  providerLink: document.querySelector("#issue-provider-link"),
  back: document.querySelector("#back-to-provider"),
  title: document.querySelector("#issue-title"),
  severity: document.querySelector("#issue-severity"),
  type: document.querySelector("#issue-type"),
  description: document.querySelector("#issue-description"),
  summary: document.querySelector("#issue-summary"),
  details: document.querySelector("#issue-details"),
  actionTitle: document.querySelector("#next-action-title"),
  action: document.querySelector("#next-action"),
  providerAction: document.querySelector("#issue-provider-action"),
};

function escapeHtml(value) {
  return String(value ?? "Field value not captured")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function humanize(value) {
  return String(value || "")
    .replace("provider.", "").replace("group.", "")
    .replace(/^provider_locations\[[^\]]+\]\./, "location ")
    .replace(/^locations(?:\[[^\]]+\]|\.[^.]+)\./, "location ")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function issueTitle(issue) {
  if (issue.type === "missing_data" && issue.display_label) return `Missing: ${issue.display_label}`;
  return issue.credential_label
    ? `${humanize(issue.type)}: ${issue.credential_label}`
    : humanize(issue.type) || "Credentialing issue";
}

function issueTypeLabel(issue) {
  return String(issue.type || "review").replaceAll("_", " ");
}

function issueDescription(issue) {
  const recordLabel = recordKind === "provider" ? "provider" : "practice";
  const field = issue.display_label || humanize((issue.affected_fields || []).join(", "));
  const credential = issue.credential_label || field || "A credential";
  if (issue.type === "expired") return `${credential} requires immediate attention because it is past its expiration date.`;
  if (issue.type === "expiring") return `${credential} is approaching expiration and should be renewed or verified.`;
  if (issue.type === "missing_data") return `This ${recordLabel} record is missing ${field || "critical information"} needed for credentialing.`;
  if (issue.type === "discrepancy") return `Two source documents report conflicting information for ${field || `this ${recordLabel} record`}.`;
  return `This ${recordLabel} record needs review.`;
}

function nextAction(issue) {
  const recordLabel = recordKind === "provider" ? "provider" : "practice";
  if (issue.type === "expired") return ["Resolve an expired credential", "Verify renewal with the provider, obtain the current document, and update the record once confirmed."];
  if (issue.type === "expiring") return ["Start renewal follow-up", "Confirm the renewal status and request updated documentation before the credential expires."];
  if (issue.type === "missing_data") return ["Complete critical information", `Request the missing fields from the ${recordLabel} or an authoritative credentialing source.`];
  if (issue.type === "discrepancy") return ["Verify the conflicting values", "Compare the listed source documents and retain the value supported by the primary source."];
  return ["Review the record", "Review the source information and take the appropriate credentialing action."];
}

function detailRows(issue) {
  const entries = [
    ["Missing information", issue.display_label],
    ["Credential", issue.credential_label],
    ["Credential category", humanize(issue.credential_category)],
    ["Credential identifier", issue.credential_identifier],
    ["Expiration date", issue.expiration_date],
    ["Days until expiration", issue.days_until_expiration],
    ["Days past expiration", issue.days_past_expiration],
    ["Previous value", issue.previous_value],
    ["Previous source file", issue.previous_file_name],
    ["Current value", issue.current_value],
    ["Source file", issue.file_name],
  ].filter(([, value]) => value !== undefined && value !== null && value !== "");
  return entries.length ? entries : [["Status", "No additional source details are available for this issue."]];
}

async function loadIssue() {
  if (!recordId || !issueId) throw new Error("The record or issue identifier is missing from this address.");
  const collection = recordKind === "provider" ? "providers" : "groups";
  const response = await fetch(`/entities/${encodeURIComponent(entityId)}/${collection}/${encodeURIComponent(recordId)}/issues/${encodeURIComponent(issueId)}`);
  if (!response.ok) throw new Error(`Issue could not be loaded (${response.status}).`);
  const payload = await response.json();
  const issue = payload.issue;
  const record = recordKind === "provider" ? payload.provider : payload.group;
  const label = recordKind === "provider" ? "Provider" : "Practice";
  const identifierLabel = recordKind === "provider" ? "NPI" : "Type 2 NPI";
  const identifier = recordKind === "provider" ? record.npi : record.type_2_npi;
  const path = recordKind === "provider" ? "providers" : "practices";
  const recordUrl = `/${path}/${encodeURIComponent(record.id)}/view?entity=${encodeURIComponent(entityId)}`;
  const action = nextAction(issue);

  elements.providerLink.textContent = record.name || `${label} record`;
  elements.providerLink.href = recordUrl;
  elements.back.href = recordUrl;
  elements.back.textContent = `← Back to ${label.toLowerCase()}`;
  elements.providerAction.href = recordUrl;
  elements.providerAction.textContent = `Open ${label.toLowerCase()} record →`;
  elements.title.textContent = issueTitle(issue);
  elements.severity.textContent = issue.severity || "review";
  elements.severity.className = `severity-tag severity-${issue.severity || "low"}`;
  elements.type.textContent = issueTypeLabel(issue);
  elements.type.className = `issue-type-tag issue-type-${issue.type || "review"}`;
  elements.description.textContent = issueDescription(issue);
  elements.summary.innerHTML = [
    [label, record.name || `${label} name not captured`],
    [identifierLabel, identifier || `${identifierLabel} not captured`],
    ["Issue type", issueTitle(issue)],
    ["Severity", issue.severity || "review"],
  ].map(([summaryLabel, value]) => `<div><span>${escapeHtml(summaryLabel)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  elements.details.innerHTML = detailRows(issue).map(([detailLabel, value]) => `<div><dt>${escapeHtml(detailLabel)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
  elements.actionTitle.textContent = action[0];
  elements.action.textContent = action[1];
  elements.loading.hidden = true;
  elements.content.hidden = false;
}

loadIssue().catch((error) => {
  elements.loading.hidden = true;
  elements.error.hidden = false;
  elements.error.textContent = error.message;
});
