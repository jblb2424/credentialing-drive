const params = new URLSearchParams(window.location.search);
const entityId = params.get("entity") || "dummy-client";
const countElement = document.querySelector("#practice-count");
const tableElement = document.querySelector("#practice-table-body");

function escapeHtml(value) {
  return String(value ?? "Not captured")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function render(practices) {
  countElement.textContent = practices.length;
  if (!practices.length) {
    tableElement.innerHTML = '<tr><td colspan="5" class="record-table-empty">No practices have been captured yet.</td></tr>';
    return;
  }
  tableElement.innerHTML = practices.map((practice) => `<tr class="expiration-row" tabindex="0" role="button" data-practice-id="${escapeHtml(practice.id)}"><td><strong>${escapeHtml(practice.legal_name || practice.name || "Unnamed practice")}</strong></td><td>${escapeHtml(practice.type_2_npi)}</td><td>${escapeHtml(practice.tax_id)}</td><td>${escapeHtml(practice.dba)}</td><td class="open-record">→</td></tr>`).join("");
}

function openPractice(practiceId) {
  window.location.assign(`/practices/${encodeURIComponent(practiceId)}/view?entity=${encodeURIComponent(entityId)}`);
}

tableElement.addEventListener("click", (event) => {
  const row = event.target.closest("[data-practice-id]");
  if (row) openPractice(row.dataset.practiceId);
});
tableElement.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    const row = event.target.closest("[data-practice-id]");
    if (row) openPractice(row.dataset.practiceId);
  }
});

fetch(`/entities/${encodeURIComponent(entityId)}/groups?limit=500`)
  .then((response) => {
    if (!response.ok) throw new Error(`Practices could not be loaded (${response.status}).`);
    return response.json();
  })
  .then((response) => render(response.groups || []))
  .catch((error) => {
    tableElement.innerHTML = `<tr><td colspan="5" class="record-table-empty">${escapeHtml(error.message)}</td></tr>`;
  });
