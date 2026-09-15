const query = new URLSearchParams(window.location.search);
const entityId = query.get("entity") || "dummy-client";
const pathSegments = window.location.pathname.split("/");
const providerId = pathSegments[2];
const issueId = pathSegments[4];

const elements = {
  loading: document.querySelector("#issue-loading"),
  error: document.querySelector("#issue-error"),
  content: document.querySelector("#issue-content"),
  providerLink: document.querySelector("#issue-provider-link"),
  back: document.querySelector("#back-to-provider"),
  title: document.querySelector("#issue-title"),
  severity: document.querySelector("#issue-severity"),
  description: document.querySelector("#issue-description"),
  summary: document.querySelector("#issue-summary"),
  details: document.querySelector("#issue-details"),
  actionTitle: document.querySelector("#next-action-title"),
  action: document.querySelector("#next-action"),
  providerAction: document.querySelector("#issue-provider-action"),
};

function escapeHtml(value) {
  return String(value ?? "Not available")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function humanize(value) {
  return String(value || "")
    .replace("provider.", "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function issueTitle(issue) {
  return issue.credential_label
    ? `${humanize(issue.type)}: ${issue.credential_label}`
    : humanize(issue.type) || "Credentialing issue";
}

function issueDescription(issue) {
  const field = humanize((issue.affected_fields || []).join(", "));
  const credential = issue.credential_label || field || "A credential";
  if (issue.type === "expired") return `${credential} requires immediate attention because it is past its expiration date.`;
  if (issue.type === "expiring") return `${credential} is approaching expiration and should be renewed or verified.`;
  if (issue.type === "missing_data") return `This provider record is missing critical information needed for credentialing.`;
  if (issue.type === "discrepancy") return `Two source documents report conflicting information for ${field || "this provider record"}.`;
  return "This provider record needs review.";
}

function nextAction(issue) {
  if (issue.type === "expired") return ["Resolve an expired credential", "Verify renewal with the provider, obtain the current document, and update the record once confirmed."];
  if (issue.type === "expiring") return ["Start renewal follow-up", "Confirm the renewal status and request updated documentation before the credential expires."];
  if (issue.type === "missing_data") return ["Complete critical information", "Request the missing fields from the provider or an authoritative credentialing source."];
  if (issue.type === "discrepancy") return ["Verify the conflicting values", "Compare the listed source documents and retain the value supported by the primary source."];
  return ["Review the record", "Review the source information and take the appropriate credentialing action."];
}

function detailRows(issue) {
  const entries = [
    ["Credential", issue.credential_label],
    ["Credential category", humanize(issue.credential_category)],
    ["Credential identifier", issue.credential_identifier],
    ["Expiration date", issue.expiration_date],
    ["Days until expiration", issue.days_until_expiration],
    ["Days past expiration", issue.days_past_expiration],
    ["Previous value", issue.previous_value],
    ["Previous source file", issue.previous_file_name],
    ["Current value", issue.current_value],
    ["Current source file", issue.file_name],
    ["Drive file ID", issue.drive_file_id],
    ["Revision ID", issue.revision_id],
  ].filter(([, value]) => value !== undefined && value !== null && value !== "");
  return entries.length ? entries : [["Status", "No additional source details are available for this issue."]];
}

async function loadIssue() {
  if (!providerId || !issueId) throw new Error("The provider or issue identifier is missing from this address.");
  const response = await fetch(`/entities/${encodeURIComponent(entityId)}/providers/${encodeURIComponent(providerId)}/issues/${encodeURIComponent(issueId)}`);
  if (!response.ok) throw new Error(`Issue could not be loaded (${response.status}).`);
  const { issue, provider } = await response.json();
  const providerUrl = `/providers/${encodeURIComponent(provider.id)}/view?entity=${encodeURIComponent(entityId)}`;
  const action = nextAction(issue);

  elements.providerLink.textContent = provider.name || "Provider";
  elements.providerLink.href = providerUrl;
  elements.back.href = providerUrl;
  elements.providerAction.href = providerUrl;
  elements.title.textContent = issueTitle(issue);
  elements.severity.textContent = issue.severity || "review";
  elements.severity.className = `severity-tag severity-${issue.severity || "low"}`;
  elements.description.textContent = issueDescription(issue);
  elements.summary.innerHTML = [
    ["Provider", provider.name || "Not available"],
    ["NPI", provider.npi || "Not captured"],
    ["Issue type", issueTitle(issue)],
    ["Severity", issue.severity || "review"],
  ].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  elements.details.innerHTML = detailRows(issue).map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
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
