const entityId = new URLSearchParams(window.location.search).get("entity") || "dummy-client";
const providerId = window.location.pathname.split("/")[2];

const elements = {
  loading: document.querySelector("#record-loading"),
  error: document.querySelector("#record-error"),
  page: document.querySelector("#record-page"),
  client: document.querySelector("#crumb-client"),
  crumbProvider: document.querySelector("#crumb-provider"),
  name: document.querySelector("#provider-name"),
  subtitle: document.querySelector("#provider-subtitle"),
  health: document.querySelector("#record-health"),
  identity: document.querySelector("#identity-grid"),
  address: document.querySelector("#provider-address"),
  locations: document.querySelector("#location-list"),
  licenses: document.querySelector("#license-list"),
  specialties: document.querySelector("#specialty-list"),
  education: document.querySelector("#education-list"),
  insurance: document.querySelector("#insurance-list"),
  payers: document.querySelector("#payer-list"),
  issues: document.querySelector("#issue-list"),
  summary: document.querySelector("#summary-list"),
  activityLink: document.querySelector("#provider-activity-link"),
};

function escapeHtml(value) {
  return String(value ?? "Field value not captured")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function value(value, fieldLabel = "Field value") {
  if (value === null || value === undefined || value === "") return `${fieldLabel} not captured`;
  if (typeof value === "object") return formatAddress(value) || `${fieldLabel} not captured`;
  return String(value);
}

function firstAvailable(object, keys) {
  for (const key of keys) {
    if (object?.[key] !== null && object?.[key] !== undefined && object[key] !== "") return object[key];
  }
  return null;
}

function expirationState(rawDate) {
  if (!rawDate) return null;
  const value = String(rawDate);
  const expirationDate = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00` : value);
  if (Number.isNaN(expirationDate.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  expirationDate.setHours(0, 0, 0, 0);
  return expirationDate < today ? "expired" : null;
}

function expirationBadge(rawDate) {
  return expirationState(rawDate) === "expired" ? '<span class="expiration-badge">Expired</span>' : "";
}

function formatAddress(address) {
  if (!address) return "";
  if (typeof address === "string") return address;
  const lines = [
    address.line1 || address.address_line_1 || address.street,
    address.line2 || address.address_line_2,
    [address.city, address.state || address.state_code, address.postal_code || address.zip].filter(Boolean).join(", "),
    address.country,
  ].filter(Boolean);
  return lines.join(" · ");
}

function locationLabel(location) {
  if (typeof location === "string") return location;
  if (!location || typeof location !== "object") return "";
  const displayName = firstAvailable(location, ["display_name", "name", "location_name"]);
  if (typeof displayName === "string") return displayName;
  return formatAddress(displayName) || formatAddress(location.address) || "";
}

function providerName(provider) {
  const profile = provider.provider || {};
  return profile.name || [profile.first_name, profile.middle_name, profile.last_name].filter(Boolean).join(" ") || "Unnamed provider";
}

function humanize(value) {
  return String(value || "")
    .replace("provider.", "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function issueLabel(issue) {
  const field = issue.display_label || humanize((issue.affected_fields || []).join(", "));
  const credential = issue.credential_label || field || "Credential";
  if (issue.type === "expiring") return `${credential} expires in ${issue.days_until_expiration} days`;
  if (issue.type === "expired") return `${credential} is expired`;
  if (issue.type === "missing_data") return `Missing ${field || "critical provider data"}`;
  if (issue.type === "discrepancy") return `Conflicting ${field || "provider information"}`;
  return humanize(issue.type) || "Record needs review";
}

function issueTypeLabel(issue) {
  return String(issue.type || "review").replaceAll("_", " ");
}

function emptyCard(message) {
  return `<div class="record-empty">${escapeHtml(message)}</div>`;
}

function tableEmpty(message, columnCount) {
  return `<tr><td colspan="${columnCount}" class="record-table-empty">${escapeHtml(message)}</td></tr>`;
}

function renderIdentity(provider) {
  const profile = provider.provider || {};
  const fields = [
    ["Provider type", firstAvailable(profile, ["provider_type", "type", "credentials"])],
    ["First name", profile.first_name],
    ["Middle name", profile.middle_name],
    ["Last name", profile.last_name],
    ["Gender", profile.gender],
    ["Date of birth", firstAvailable(profile, ["date_of_birth", "dob"])],
    ["NPI", profile.npi],
    ["CAQH ID", firstAvailable(profile, ["caqh_id", "caqh"])],
  ];
  elements.identity.innerHTML = fields.map(([label, fieldValue]) => `<div class="data-field"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value(fieldValue, label))}</strong></div>`).join("");
  elements.address.textContent = value(firstAvailable(profile, ["address", "provider_address"]), "Provider address");
}

function providerLocations(provider) {
  const directLocations = provider.provider_locations || provider.locations || [];
  const affiliations = provider.affiliations || [];
  const affiliatedLocations = affiliations.flatMap((affiliation) => (affiliation.locations || []).map((location) => ({
    ...location,
    practice_name: affiliation.group?.legal_name || affiliation.group?.name,
  })));
  return [...directLocations, ...affiliatedLocations];
}

function renderLocations(provider) {
  const locations = providerLocations(provider);
  if (!locations.length) {
    elements.locations.innerHTML = emptyCard("No provider locations have been captured yet.");
    return;
  }
  elements.locations.innerHTML = locations.map((location) => {
    const locationData = typeof location === "object" ? location : { display_name: location };
    const title = firstAvailable(locationData, ["display_name", "name", "location_name"]);
    const details = formatAddress(locationData.address) || firstAvailable(locationData, ["phone", "email"]);
    return `<article class="location-card"><span class="location-type">${escapeHtml(value(locationData.type, "Location type"))}</span><h3>${escapeHtml(value(title, "Location name"))}</h3><p>${escapeHtml(value(details, "Location contact details"))}</p>${locationData.practice_name ? `<span class="practice-note">${escapeHtml(locationData.practice_name)}</span>` : ""}</article>`;
  }).join("");
}

function renderLicenses(provider) {
  const licenses = Array.isArray(provider.licenses) ? provider.licenses : [];
  if (!licenses.length) {
    elements.licenses.innerHTML = tableEmpty("No license information has been captured yet.", 5);
    return;
  }
  elements.licenses.innerHTML = licenses.map((license) => {
    const item = typeof license === "object" ? license : { type: license };
    const expiration = firstAvailable(item, ["expiration_date", "expires_at", "expiry_date"]);
    const state = expirationState(expiration);
    return `<tr class="${state ? `is-${state}` : ""}"><td>${escapeHtml(value(firstAvailable(item, ["type", "license_type"]), "License type"))}</td><td>${escapeHtml(value(firstAvailable(item, ["license_number", "number"]), "License number"))}</td><td>${escapeHtml(value(firstAvailable(item, ["state", "issued_state"]), "License state"))}</td><td>${escapeHtml(value(firstAvailable(item, ["issue_date", "issued_date"]), "License issue date"))}</td><td class="${state ? `expiration-date is-${state}` : ""}">${escapeHtml(value(expiration, "License expiration date"))}${expirationBadge(expiration)}</td></tr>`;
  }).join("");
}

function renderSpecialties(provider) {
  const specialties = Array.isArray(provider.specialties) ? provider.specialties : [];
  if (!specialties.length) {
    elements.specialties.innerHTML = emptyCard("No specialty or board certification information has been captured yet.");
    return;
  }
  elements.specialties.innerHTML = specialties.map((specialty) => {
    const item = typeof specialty === "object" ? specialty : { name: specialty };
    const certified = firstAvailable(item, ["board_certified", "is_board_certified"]);
    const expiration = firstAvailable(item, ["expiration_date", "expires_at", "board_expiration_date"]);
    const state = expirationState(expiration);
    return `<article class="certification-card ${state ? `is-${state}` : ""}"><p>${escapeHtml(value(firstAvailable(item, ["name", "specialty"]), "Specialty"))}</p>${expirationBadge(expiration)}<strong>${certified === true || String(certified).toLowerCase() === "yes" ? "Board certified" : "Board certification status not captured"}</strong><dl><div><dt>Board</dt><dd>${escapeHtml(value(firstAvailable(item, ["certifying_board", "board"]), "Certifying board"))}</dd></div><div><dt>Certified</dt><dd>${escapeHtml(value(firstAvailable(item, ["certification_date", "certified_date"]), "Certification date"))}</dd></div><div><dt>Expires</dt><dd class="${state ? `expiration-date is-${state}` : ""}">${escapeHtml(value(expiration, "Certification expiration date"))}</dd></div></dl></article>`;
  }).join("");
}

function renderEducation(provider) {
  const education = Array.isArray(provider.education) ? provider.education : [];
  if (!education.length) {
    elements.education.innerHTML = emptyCard("No education or training information has been captured yet.");
    return;
  }
  elements.education.innerHTML = education.map((entry) => {
    const item = typeof entry === "object" ? entry : { institution_name: entry };
    return `<article class="timeline-item"><span></span><div><p>${escapeHtml(value(firstAvailable(item, ["education_type", "type"]), "Education type"))}</p><h3>${escapeHtml(value(firstAvailable(item, ["institution_name", "institution"]), "Institution name"))}</h3><div class="timeline-meta">${escapeHtml(value(firstAvailable(item, ["degree", "degree_received"]), "Degree")) }${item.specialty ? ` · ${escapeHtml(item.specialty)}` : ""}</div></div></article>`;
  }).join("");
}

function renderInsurance(provider) {
  const insurance = provider.liability_insurance || provider.liability_insurances || [];
  if (!Array.isArray(insurance) || !insurance.length) {
    elements.insurance.innerHTML = emptyCard("No liability coverage information has been captured yet.");
    return;
  }
  elements.insurance.innerHTML = insurance.map((policy) => {
    const item = typeof policy === "object" ? policy : { insurance_type: policy };
    const expiration = firstAvailable(item, ["expiration_date", "expires_at", "policy_expiration_date"]);
    const state = expirationState(expiration);
    return `<article class="coverage-card ${state ? `is-${state}` : ""}"><p>${escapeHtml(value(firstAvailable(item, ["insurance_type", "type"]), "Insurance type"))}</p>${expirationBadge(expiration)}<h3>${escapeHtml(value(item.carrier_name, "Insurance carrier"))}</h3><div class="coverage-row"><span>Policy #</span><strong>${escapeHtml(value(item.policy_number, "Policy number"))}</strong></div><div class="coverage-row"><span>Coverage period</span><strong class="${state ? `expiration-date is-${state}` : ""}">${escapeHtml(value(item.effective_date, "Policy effective date"))} – ${escapeHtml(value(expiration, "Policy expiration date"))}</strong></div><div class="coverage-row"><span>Claim / aggregate</span><strong>${escapeHtml(value(item.claim_amount, "Claim amount"))} / ${escapeHtml(value(item.aggregate_amount, "Aggregate amount"))}</strong></div></article>`;
  }).join("");
}

function allEnrollments(provider) {
  const direct = Array.isArray(provider.payer_enrollments) ? provider.payer_enrollments.map((payer) => ({ ...payer, source: "Provider" })) : [];
  const affiliated = (provider.affiliations || []).flatMap((affiliation) => (affiliation.payer_enrollments || []).map((payer) => ({
    ...payer,
    group_name: affiliation.group?.legal_name || affiliation.group?.name,
    locations: affiliation.locations || [],
    source: "Practice",
  })));
  return [...direct, ...affiliated];
}

function renderPayers(provider) {
  const enrollments = allEnrollments(provider);
  if (!enrollments.length) {
    elements.payers.innerHTML = tableEmpty("No payer enrollment information has been captured yet.", 4);
    return;
  }
  elements.payers.innerHTML = enrollments.map((enrollment) => {
    const locations = (enrollment.locations || enrollment.location_names || []).map(locationLabel).filter(Boolean).join(", ");
    return `<tr><td class="payer-name"><strong>${escapeHtml(value(enrollment.payer_name || enrollment.name, "Payer name"))}</strong><span class="table-note">${escapeHtml(enrollment.source || "Provider")}</span></td><td>${escapeHtml(value(enrollment.group_name || enrollment.practice_name, "Practice"))}</td><td>${escapeHtml(value(locations, "Participating locations"))}</td><td class="payer-status"><span class="enrollment-status">${escapeHtml(humanize(enrollment.status || enrollment.enrollment_status || "unknown"))}</span></td></tr>`;
  }).join("");
}

function renderIssues(provider) {
  const issues = Array.isArray(provider.issues) ? provider.issues : [];
  elements.health.innerHTML = issues.length ? `<span class="health-dot attention"></span>${issues.length} open ${issues.length === 1 ? "issue" : "issues"}` : '<span class="health-dot"></span>Record in good standing';
  if (!issues.length) {
    elements.issues.innerHTML = '<p class="aside-empty">No outstanding issues on this record.</p>';
    return;
  }
  elements.issues.innerHTML = issues.map((issue) => `<a class="record-issue ${issue.type === "expired" ? "is-expired" : ""}" href="/providers/${encodeURIComponent(provider.id)}/issues/${encodeURIComponent(issue.id)}/view?entity=${entityId}"><p>${escapeHtml(issueLabel(issue))}</p><span class="issue-tags"><span class="severity-tag severity-${escapeHtml(issue.severity || "low")}">${escapeHtml(issue.severity || "review")}</span><span class="issue-type-tag issue-type-${escapeHtml(issue.type || "review")}">${escapeHtml(issueTypeLabel(issue))}</span></span><span class="issue-link">View issue →</span></a>`).join("");
}

function renderSummary(provider) {
  const affiliations = provider.affiliations || [];
  const locations = providerLocations(provider);
  const rows = [
    ["Practices", affiliations.length],
    ["Locations", locations.length],
  ];
  elements.summary.innerHTML = rows.map(([label, summaryValue]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value(summaryValue, label))}</strong></div>`).join("");
}

async function loadRecord() {
  if (!providerId) throw new Error("Provider ID is missing from the page address.");
  const [provider, entity] = await Promise.all([
    fetch(`/entities/${encodeURIComponent(entityId)}/providers/${encodeURIComponent(providerId)}`).then((response) => {
      if (!response.ok) throw new Error(`Provider record could not be loaded (${response.status}).`);
      return response.json();
    }),
    fetch(`/entities/${encodeURIComponent(entityId)}`).then((response) => response.ok ? response.json() : { name: "Client" }),
  ]);
  const profile = provider.provider || {};
  const name = providerName(provider);
  elements.client.textContent = entity.name || entity.legal_name || "Client";
  elements.crumbProvider.textContent = name;
  elements.name.textContent = name;
  elements.subtitle.textContent = [profile.credentials || profile.provider_type, profile.npi ? `NPI ${profile.npi}` : null].filter(Boolean).join(" · ") || "Credentialing profile";
  elements.activityLink.href = `/activity/view?entity=${encodeURIComponent(entityId)}&record_type=provider&record_id=${encodeURIComponent(provider.id)}`;
  renderIdentity(provider);
  renderLocations(provider);
  renderLicenses(provider);
  renderSpecialties(provider);
  renderEducation(provider);
  renderInsurance(provider);
  renderPayers(provider);
  renderIssues(provider);
  renderSummary(provider);
  elements.loading.hidden = true;
  elements.page.hidden = false;
}

loadRecord().catch((error) => {
  elements.loading.hidden = true;
  elements.error.hidden = false;
  elements.error.textContent = error.message;
});
