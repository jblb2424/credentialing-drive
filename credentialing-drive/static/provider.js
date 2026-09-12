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
};

function escapeHtml(value) {
  return String(value ?? "Not yet captured")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function value(value) {
  if (value === null || value === undefined || value === "") return "Not yet captured";
  if (typeof value === "object") return formatAddress(value) || "Not yet captured";
  return String(value);
}

function firstAvailable(object, keys) {
  for (const key of keys) {
    if (object?.[key] !== null && object?.[key] !== undefined && object[key] !== "") return object[key];
  }
  return null;
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
  const field = humanize((issue.affected_fields || []).join(", "));
  if (issue.type === "expiring") return `${field || "Credential"} expires in ${issue.days_until_expiration} days`;
  if (issue.type === "expired") return `${field || "Credential"} is expired`;
  if (issue.type === "missing_data") return `Missing ${field || "critical provider data"}`;
  if (issue.type === "discrepancy") return `Conflicting ${field || "provider information"}`;
  return humanize(issue.type) || "Record needs review";
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
  elements.identity.innerHTML = fields.map(([label, fieldValue]) => `<div class="data-field"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value(fieldValue))}</strong></div>`).join("");
  elements.address.textContent = value(firstAvailable(profile, ["address", "provider_address"]));
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
    return `<article class="location-card"><span class="location-type">${escapeHtml(value(locationData.type || "Practice location"))}</span><h3>${escapeHtml(value(title))}</h3><p>${escapeHtml(value(details))}</p>${locationData.practice_name ? `<span class="practice-note">${escapeHtml(locationData.practice_name)}</span>` : ""}</article>`;
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
    return `<tr><td>${escapeHtml(value(firstAvailable(item, ["type", "license_type"])))}</td><td>${escapeHtml(value(firstAvailable(item, ["license_number", "number"])))}</td><td>${escapeHtml(value(firstAvailable(item, ["state", "issued_state"])))}</td><td>${escapeHtml(value(firstAvailable(item, ["issue_date", "issued_date"])))}</td><td>${escapeHtml(value(firstAvailable(item, ["expiration_date", "expires_at"])))}</td></tr>`;
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
    return `<article class="certification-card"><p>${escapeHtml(value(firstAvailable(item, ["name", "specialty"])))}</p><strong>${certified === true || String(certified).toLowerCase() === "yes" ? "Board certified" : "Board status not captured"}</strong><dl><div><dt>Board</dt><dd>${escapeHtml(value(firstAvailable(item, ["certifying_board", "board"])))}</dd></div><div><dt>Certified</dt><dd>${escapeHtml(value(firstAvailable(item, ["certification_date", "certified_date"])))}</dd></div><div><dt>Expires</dt><dd>${escapeHtml(value(firstAvailable(item, ["expiration_date", "expires_at"])))}</dd></div></dl></article>`;
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
    return `<article class="timeline-item"><span></span><div><p>${escapeHtml(value(firstAvailable(item, ["education_type", "type"])))}</p><h3>${escapeHtml(value(firstAvailable(item, ["institution_name", "institution"])))}</h3><div class="timeline-meta">${escapeHtml(value(firstAvailable(item, ["degree", "degree_received"]))) }${item.specialty ? ` · ${escapeHtml(item.specialty)}` : ""}</div></div></article>`;
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
    return `<article class="coverage-card"><p>${escapeHtml(value(firstAvailable(item, ["insurance_type", "type"])))}</p><h3>${escapeHtml(value(item.carrier_name))}</h3><div class="coverage-row"><span>Policy #</span><strong>${escapeHtml(value(item.policy_number))}</strong></div><div class="coverage-row"><span>Coverage period</span><strong>${escapeHtml(value(item.effective_date))} – ${escapeHtml(value(item.expiration_date))}</strong></div><div class="coverage-row"><span>Claim / aggregate</span><strong>${escapeHtml(value(item.claim_amount))} / ${escapeHtml(value(item.aggregate_amount))}</strong></div></article>`;
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
    const locations = (enrollment.locations || enrollment.location_names || []).map((location) => typeof location === "object" ? location.display_name || location.name : location).filter(Boolean).join(", ");
    return `<tr><td><strong>${escapeHtml(value(enrollment.payer_name || enrollment.name))}</strong><span class="table-note">${escapeHtml(enrollment.source || "Provider")}</span></td><td>${escapeHtml(value(enrollment.group_name || enrollment.practice_name))}</td><td>${escapeHtml(value(locations))}</td><td><span class="enrollment-status">${escapeHtml(humanize(enrollment.status || enrollment.enrollment_status || "unknown"))}</span></td></tr>`;
  }).join("");
}

function renderIssues(provider) {
  const issues = Array.isArray(provider.issues) ? provider.issues : [];
  elements.health.innerHTML = issues.length ? `<span class="health-dot attention"></span>${issues.length} open ${issues.length === 1 ? "issue" : "issues"}` : '<span class="health-dot"></span>Record in good standing';
  if (!issues.length) {
    elements.issues.innerHTML = '<p class="aside-empty">No outstanding issues on this record.</p>';
    return;
  }
  elements.issues.innerHTML = issues.map((issue) => `<a class="record-issue" href="/providers/${encodeURIComponent(provider.id)}/issues/${encodeURIComponent(issue.id)}/view?entity=${entityId}"><p>${escapeHtml(issueLabel(issue))}</p><span class="severity-tag severity-${escapeHtml(issue.severity || "low")}">${escapeHtml(issue.severity || "review")}</span><span class="issue-link">View issue →</span></a>`).join("");
}

function renderSummary(provider) {
  const affiliations = provider.affiliations || [];
  const locations = providerLocations(provider);
  const rows = [
    ["Record ID", provider.id],
    ["Practices", affiliations.length],
    ["Locations", locations.length],
    ["Source records", "Managed by import history"],
  ];
  elements.summary.innerHTML = rows.map(([label, summaryValue]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value(summaryValue))}</strong></div>`).join("");
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
