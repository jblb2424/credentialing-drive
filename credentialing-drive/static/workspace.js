const section = window.location.pathname.split("/")[1] || "overview";
const content = {
  enrollment: ["Enrollment", "Manage payer enrollment workflows across practices and providers.", "Enrollment workflows are coming soon", "This area will centralize payer participation, packet status, and enrollment milestones."],
  activity: ["Activity", "Review the document-driven changes made across this client.", "No revisions yet", "Revision history will appear here after a credentialing document is processed."],
  reports: ["Reports", "Review operational credentialing performance across your client.", "No reports yet", "Reporting views will appear here as the client data model grows."],
  settings: ["Settings", "Configure your client workspace and connected services.", "No settings yet", "Client configuration options will appear here when they are ready."],
};
const [title, description, emptyTitle, emptyDescription] = content[section] || content.enrollment;
document.title = `${title} | Cozema Health`;
document.querySelector("#workspace-title").textContent = title;
document.querySelector("#workspace-description").textContent = description;
document.querySelector("#empty-title").textContent = emptyTitle;
document.querySelector("#empty-description").textContent = emptyDescription;
document.querySelector(`[data-nav="${section}"]`)?.classList.add("active");
