import { useDeferredValue, useEffect, useRef, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AuthControls } from "./auth.jsx";
import { entityPath, fetchJson, fetchWithAuth } from "./api.js";
import { IssueDismissalDialog, IssueDismissButton, TrashIcon, useIssueDismissal } from "./issue-dismissal.jsx";
import { useWorkspace } from "./workspace.jsx";
import { DEFAULT_ENTITY_ID, addressLabel, categoryLabel, dateLabel, expirationState, fieldLabel, issueLabel, issueTypeLabel, locationLabel, priority, providerData, recordName, shortDate, timestampLabel, value } from "./utils.js";

const navItems = [
  ["overview", "▦", "Overview", "/"],
  ["providers", "◉", "Providers", "/providers/view"],
  ["expirations", "◷", "Expirations", "/expirations/view"],
  ["activity", "◌", "Activity", "/activity/view"],
  ["reports", "▤", "Reports", "/reports/view"],
  ["users", "◉", "Users", "/users/view"],
  ["connections", "◌", "Data connections", "/data-connections/view"],
];
const EDITABLE_RECORD_ROLES = new Set(["super_admin", "customer_admin", "manager", "credentialing_specialist"]);

function BrandWordmark() {
  return <>Cozema<span className="brand-accent">Health</span></>;
}

function DetailActionsMenu({ actionLabel, onAction }) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const closeIfOutside = (event) => {
      if (!menuRef.current?.contains(event.target)) setOpen(false);
    };
    const closeOnEscape = (event) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", closeIfOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeIfOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return <div className="detail-actions-menu" ref={menuRef}>
    <button className="detail-actions-trigger" type="button" aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen((current) => !current)}>Actions<span aria-hidden="true">•••</span></button>
    {open && <div className="detail-actions-popover" role="menu">
      <button className="detail-actions-danger" type="button" role="menuitem" onClick={() => { setOpen(false); onAction(); }}><TrashIcon />{actionLabel}</button>
    </div>}
  </div>;
}

function useEntity() {
  const workspace = useWorkspace();
  const [params] = useSearchParams();
  // The API derives access from the authenticated workspace organization. This
  // client ID only builds routes for that already-authorized workspace.
  return workspace?.user?.client_id || params.get("entity") || DEFAULT_ENTITY_ID;
}

function useRequest(path) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const [refreshCount, setRefreshCount] = useState(0);
  useEffect(() => {
    if (!path) {
      setState({ loading: false, data: null, error: null });
      return undefined;
    }
    let active = true;
    setState({ loading: true, data: null, error: null });
    fetchJson(path).then((data) => active && setState({ loading: false, data, error: null })).catch((error) => active && setState({ loading: false, data: null, error }));
    return () => { active = false; };
  }, [path, refreshCount]);
  return { ...state, refresh: () => setRefreshCount((count) => count + 1) };
}

function href(path, entityId) {
  return `${path}${path.includes("?") ? "&" : "?"}entity=${encodeURIComponent(entityId)}`;
}

function normalizedLocationIdentityPart(value) {
  return typeof value === "string"
    ? value.trim().toLocaleLowerCase().replace(/[^a-z0-9]+/g, " ").trim()
    : "";
}

function locationIdentityKey(location) {
  const address = location?.address;
  const name = normalizedLocationIdentityPart(
    location?.display_name || location?.name || (typeof address === "object" ? address?.display_name : ""),
  );
  const addressText = normalizedLocationIdentityPart(
    typeof address === "string"
      ? address
      : address?.line_1 || address?.address_line_1 || address?.street || location?.line_1 || location?.address_line_1 || location?.street,
  );

  return name || addressText ? `${name}:${addressText}` : "";
}

function uniqueLocations(locations) {
  const seen = new Set();
  return locations.filter((location, index) => {
    // A provider can belong to multiple practices that reference the same place.
    // Use stable real-world fields so the provider profile does not repeat it.
    const key = locationIdentityKey(location) || `record:${location?.id || index}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function LoadingCount({ label }) {
  return <span className="inline-count is-loading" aria-busy="true"><span className="loading-skeleton" aria-hidden="true" /><span className="sr-only">Loading {label}</span></span>;
}

const DRIVE_INTAKE_POLL_INTERVAL_MS = 15_000;

function hasSameIntakeStatus(left, right) {
  return left?.active_document_count === right?.active_document_count
    && left?.detected_document_count === right?.detected_document_count
    && left?.queued_document_count === right?.queued_document_count
    && left?.processing_document_count === right?.processing_document_count
    && left?.retrying_document_count === right?.retrying_document_count;
}

function useDriveIntakeStatus(entityId) {
  const [status, setStatus] = useState(null);
  const requestInFlight = useRef(false);

  useEffect(() => {
    let active = true;
    let pollId;

    const loadStatus = async () => {
      if (requestInFlight.current || document.visibilityState !== "visible") return;
      requestInFlight.current = true;
      try {
        const nextStatus = await fetchJson(entityPath(entityId, "/drive/intake-status"));
        if (active) setStatus((current) => hasSameIntakeStatus(current, nextStatus) ? current : nextStatus);
      } catch {
        // Preserve the last successful value. A badge failure must not disrupt the workspace.
      } finally {
        requestInFlight.current = false;
      }
    };

    const stopPolling = () => {
      if (pollId) window.clearInterval(pollId);
      pollId = undefined;
    };
    const startPolling = () => {
      if (pollId || document.visibilityState !== "visible") return;
      void loadStatus();
      pollId = window.setInterval(loadStatus, DRIVE_INTAKE_POLL_INTERVAL_MS);
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") startPolling();
      else stopPolling();
    };

    startPolling();
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      active = false;
      stopPolling();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [entityId]);

  return status;
}

function Shell({ active, children, refresh }) {
  const entityId = useEntity();
  const workspace = useWorkspace();
  const canManageMembers = ["super_admin", "customer_admin", "manager"].includes(workspace?.user?.role);
  const intakeStatus = useDriveIntakeStatus(entityId);
  const activeDocumentCount = intakeStatus?.active_document_count || 0;
  const isProcessingDocuments = activeDocumentCount > 0;
  const intakeLabel = isProcessingDocuments
    ? `Processing ${activeDocumentCount} ${activeDocumentCount === 1 ? "document" : "documents"}`
    : "Live data";
  return <div className="app-shell">
    <aside className="sidebar">
      <Link className="brand" to={href("/", entityId)}><span className="brand-mark">C</span><span><BrandWordmark /></span></Link>
      <p className="workspace-label">Workspace</p>
      <nav className="main-nav" aria-label="Primary navigation">
        {navItems.filter(([id]) => id !== "users" || canManageMembers).map(([id, icon, label, path]) => <NavLink key={id} className={active === id ? "active" : ""} to={href(path, entityId)}><span className="nav-icon">{icon}</span>{label}</NavLink>)}
      </nav>
      <div className="sidebar-footer"><p>Data refreshes from your connected credentialing workspace.</p></div>
    </aside>
    <main className={active === "overview" ? "content" : "workspace-content"}>
      <header className="topbar"><div className="mobile-brand"><BrandWordmark /></div><div className={`status-line${isProcessingDocuments ? " is-processing" : ""}`} aria-live="polite" aria-busy={isProcessingDocuments}><span className={isProcessingDocuments ? "intake-spinner" : "live-dot"} aria-hidden="true" />{intakeLabel}</div><AuthControls />{refresh && <button className="refresh-button" type="button" onClick={refresh}><span>↻</span>Refresh data</button>}</header>
      {children}
    </main>
  </div>;
}

function EmptyRow({ columns, children }) { return <tr><td colSpan={columns} className="record-table-empty">{children}</td></tr>; }
function InlineLoading({ text }) { return <span className="loading-inline"><span className="spinner" />{text}</span>; }

function Greeting() {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

function recordIssues(record) { return record?.issues || record?.issue_summary?.issues || []; }
function allIssues(summaries) {
  return summaries.flatMap((summary) => {
    const recordType = summary.record_type || "provider";
    const record = recordType === "provider"
      ? { id: summary.record_id, provider: { name: summary.record_name, npi: summary.record_identifier } }
      : { id: summary.record_id, legal_name: summary.record_name, type_2_npi: summary.record_identifier };
    const data = recordType === "provider" ? providerData(record) : record;
    return recordIssues(summary).map((issue) => ({
      ...issue,
      record,
      record_type: recordType,
      recordType,
      recordId: summary.record_id,
      recordName: summary.record_name,
      provider_id: recordType === "provider" ? summary.record_id : issue.provider_id,
      group_id: recordType === "practice" ? summary.record_id : issue.group_id,
      provider_name: issue.provider_name || (recordType === "provider" ? recordName(record) : undefined),
      provider_npi: issue.provider_npi || (recordType === "provider" ? data.npi : undefined),
      group_name: issue.group_name || (recordType === "practice" ? recordName(record) : undefined),
    }));
  });
}

function optimisticRecordUpdate(type, record, fields) {
  if (type === "practice") return { ...record, ...fields };

  const profileFields = {};
  const collectionFields = {};
  for (const [key, fieldValue] of Object.entries(fields)) {
    if (["licenses", "specialties", "education", "liability_insurance", "payer_enrollments"].includes(key)) collectionFields[key] = fieldValue;
    else profileFields[key] = fieldValue;
  }
  return {
    ...record,
    ...collectionFields,
    provider: { ...providerData(record), ...profileFields },
  };
}

function Dashboard() {
  const entityId = useEntity();
  const workspace = useWorkspace();
  const [refreshKey, setRefreshKey] = useState(0);
  const groups = useRequest(`${entityPath(entityId, "/groups?limit=500")}&refresh=${refreshKey}`);
  const providers = useRequest(`${entityPath(entityId, "/providers?limit=500")}&refresh=${refreshKey}`);
  const issueSummaries = useRequest(`${entityPath(entityId, `/issues?refresh=${refreshKey}`)}`);
  const connections = useRequest(entityPath(entityId, "/connections"));
  const dismissal = useIssueDismissal(entityId, { onDismissed: () => setRefreshKey((key) => key + 1) });
  const groupRecords = groups.data?.groups || [];
  const providerRecords = providers.data?.providers || [];
  const issues = allIssues(issueSummaries.data?.issues || []);
  const expiring = issues.filter((issue) => issue.type === "expiring");
  const critical = issues.filter((issue) => issue.severity === "critical").sort((left, right) => priority(left) - priority(right)).slice(0, 5);
  const canManageIssues = ["super_admin", "customer_admin", "manager", "credentialing_specialist"].includes(workspace?.user?.role);
  const canManageDocuments = ["super_admin", "customer_admin", "credentialing_specialist"].includes(workspace?.user?.role);
  const connectionReady = !connections.loading && !connections.error;
  const drive = connections.data?.google_drive;
  const driveConnected = Boolean(drive?.connected);
  const intakeFolderConfigured = Boolean(drive?.intake_folder_configured);
  const intakeReady = Boolean(drive?.intake_ready);
  useEffect(() => { document.title = "Credentialing Operations | Cozema Health"; }, []);
  return <Shell active="overview" refresh={() => setRefreshKey((key) => key + 1)}>
    <section className="hero"><p className="eyebrow">Credentialing overview</p><h1>{Greeting()}</h1><p className="lede">Keep enrollment moving by resolving the provider records that need attention first.</p></section>
    {connectionReady && <section className="connection-banner"><div><p className="eyebrow">{intakeReady ? "AI credentialing intake" : intakeFolderConfigured ? "Document intake" : "Workspace setup"}</p><h2 className="connection-banner-title"><svg className="ai-sparkles-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3-1.9 5.1L5 10l5.1 1.9L12 17l1.9-5.1L19 10l-5.1-1.9L12 3Z" /><path d="m19 15-.8 2.2L16 18l2.2.8L19 21l.8-2.2L22 18l-2.2-.8L19 15Z" /><path d="m5 15-.55 1.45L3 17l1.45.55L5 19l.55-1.45L7 17l-1.45-.55L5 15Z" /></svg>{intakeReady ? "AI is handling your credentialing intake" : intakeFolderConfigured ? "Preparing your AI credentialing intake" : driveConnected ? "Finish setting up your intake folder" : "Connect your credentialing documents"}</h2><p>{intakeReady ? "Files added to your connected folder are transformed by AI into provider and practice records, with missing information, discrepancies, and expirations flagged for review." : intakeFolderConfigured ? <>Your Google Drive intake folder, <strong>{value(drive?.intake_folder_name, "your selected folder")}</strong>, is being prepared for AI intake. Files added here will be transformed into provider and practice records, with missing information, discrepancies, and expirations flagged for review.</> : driveConnected ? "Choose the Google Drive folder that should receive credentialing documents. Monitoring starts as soon as the folder is confirmed." : "Connect Google Drive to begin bringing new provider and practice documents into this workspace."}</p></div><Link className="primary-button" to={href("/data-connections/view", entityId)}>{intakeReady || intakeFolderConfigured ? "View data connections" : driveConnected && canManageDocuments ? "Choose intake folder" : canManageDocuments ? "Connect Google Drive" : "View data connections"}</Link></section>}
    <section className="metrics" aria-label="Credentialing summary">
      <Link className="metric-card metric-link" to={href("/practices/view", entityId)}><p>Practices</p>{groups.loading ? <LoadingCount label="practices" /> : <strong>{groups.error ? "Unavailable" : groupRecords.length}</strong>}<span>Browse all practices</span></Link>
      <Link className="metric-card metric-primary metric-link" to={href("/providers/view", entityId)}><p>Provider records</p>{providers.loading ? <LoadingCount label="providers" /> : <strong>{providers.error ? "Unavailable" : providerRecords.length}</strong>}<span>Browse all provider records</span></Link>
      <Link className="metric-card metric-issue metric-link" to={href("/issues/view", entityId)}><p>Open issues</p>{issueSummaries.loading ? <LoadingCount label="issues" /> : <strong>{issueSummaries.error ? "Unavailable" : issues.length}</strong>}<span>Review all outstanding issues</span></Link>
      <Link className="metric-card metric-warning metric-link" to={href("/expirations/view?status=expiring", entityId)}><p>Credentials expiring soon</p>{issueSummaries.loading ? <LoadingCount label="expiring credentials" /> : <strong>{issueSummaries.error ? "Unavailable" : expiring.length}</strong>}<span>Within the next 90 days</span></Link>
    </section>
    <section className="attention-panel"><div className="section-heading"><div><p className="eyebrow">Priority work</p><h2>Attention required</h2></div></div>
      <div className="attention-list">{issueSummaries.loading ? <div className="loading-row"><span className="spinner" />Loading critical issues...</div> : issueSummaries.error ? <div className="empty-state">{issueSummaries.error.message}</div> : critical.length ? critical.map((issue, index) => <CriticalIssue key={`${issue.id || issueLabel(issue)}-${index}`} issue={issue} entityId={entityId} canManageIssues={canManageIssues} onDismiss={dismissal.requestDismissal} />) : <div className="empty-state">No critical issues require immediate attention.</div>}</div>
      {(dismissal.message || dismissal.error) && <p className={`operation-notice ${dismissal.error ? "is-error" : ""}`} role={dismissal.error ? "alert" : "status"}>{dismissal.error || dismissal.message}</p>}
    </section>
    <section className="groups-section"><div className="section-heading"><div><p className="eyebrow">Practice network</p><h2>Practices</h2></div><Link to={href("/practices/view", entityId)}>Browse all practices</Link></div><div className="group-list">{groups.loading ? <div className="loading-row"><span className="spinner" />Loading practices...</div> : groups.error ? <div className="empty-state">{groups.error.message}</div> : groupRecords.map((group) => <Link className="group-chip" key={group.id} to={href(`/practices/${encodeURIComponent(group.id)}/view`, entityId)}>{value(group.legal_name || group.name, "Unnamed practice")}</Link>)}</div></section>
    <IssueDismissalDialog issue={dismissal.pendingIssue} error={dismissal.error} saving={dismissal.saving} onCancel={dismissal.cancelDismissal} onConfirm={dismissal.confirmDismissal} />
  </Shell>;
}

function CriticalIssue({ issue, entityId, canManageIssues, onDismiss }) {
  const record = issue.record || issue.provider || issue.group || {};
  const type = issue.record_type || (issue.group_id ? "practice" : "provider");
  const id = issue.provider_id || issue.group_id || record.id;
  const link = id ? href(`/${type === "practice" ? "practices" : "providers"}/${encodeURIComponent(id)}/issues/${encodeURIComponent(issue.id)}/view`, entityId) : href("/issues/view", entityId);
  return <div className={`attention-row ${issue.type === "expired" ? "is-expired" : ""}`}><Link className="attention-provider" to={link}>{value(issue.provider_name || issue.group_name || recordName(record))}<span>{value(issue.provider_npi || record?.provider?.npi, "NPI not captured")}</span></Link><Link className="attention-detail" to={link}>{issueLabel(issue)}</Link><span className="issue-tags"><span className={`severity-tag severity-${issue.severity}`}>{value(issue.severity, "high")}</span><span className={`issue-type-tag issue-type-${issue.type}`}>{issueTypeLabel(issue.type)}</span>{canManageIssues && <IssueDismissButton issue={issue} onDismiss={onDismiss} />}</span></div>;
}

function Directory({ type }) {
  const entityId = useEntity();
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const collection = type === "provider" ? "providers" : "groups";
  const request = useRequest(entityPath(entityId, `/${collection}?limit=500`));
  const records = request.data?.[collection] || [];
  const filtered = records.filter((record) => {
    const data = type === "provider" ? providerData(record) : record;
    const haystack = type === "provider" ? [data.name, data.npi, data.provider_type, ...(data.credentials || [])] : [data.legal_name, data.name, data.type_2_npi, data.tax_id_number, data.dba];
    return haystack.join(" ").toLowerCase().includes(deferredQuery.toLowerCase());
  });
  const title = type === "provider" ? "Providers" : "Practices";
  document.title = `${title} | Cozema Health`;
  return <Shell active="providers"><section className="workspace-hero"><p className="eyebrow">{type === "provider" ? "Provider directory" : "Practice network"}</p><h1>{title}</h1><p className="lede">{type === "provider" ? "Every credentialing provider record for this client." : "Every practice and organization credentialing record for this client."}</p></section>
    <section className="directory-section"><div className="section-heading"><div><p className="eyebrow">{request.loading ? "Loading records" : `${filtered.length} records`}</p><h2>{type === "provider" ? "Provider records" : "Practice records"}</h2></div><div className="directory-actions"><label className="search-field">⌕<input value={query} onChange={(event) => setQuery(event.target.value)} type="search" placeholder={`Search ${type === "provider" ? "providers" : "practices"}`} /></label></div></div>
      <div className="provider-table-wrap"><table className="provider-table"><thead>{type === "provider" ? <tr><th>Provider</th><th>NPI</th><th>Type</th><th>Practices</th><th>Record health</th></tr> : <tr><th>Practice</th><th>Type 2 NPI</th><th>Tax ID</th><th>DBA</th><th>Record health</th></tr>}</thead><tbody>{request.loading ? <EmptyRow columns="5"><InlineLoading text={`Loading ${title.toLowerCase()}...`} /></EmptyRow> : request.error ? <EmptyRow columns="5">{request.error.message}</EmptyRow> : filtered.length ? filtered.map((record) => <DirectoryRow key={record.id} record={record} type={type} entityId={entityId} />) : <EmptyRow columns="5">No records match the current search.</EmptyRow>}</tbody></table></div>
    </section></Shell>;
}

function DirectoryRow({ record, type, entityId }) {
  const navigate = useNavigate();
  const data = type === "provider" ? providerData(record) : record;
  const issueCount = Number(record.issue_count ?? recordIssues(record).length);
  const path = href(`/${type === "provider" ? "providers" : "practices"}/${encodeURIComponent(record.id)}/view`, entityId);
  const open = () => navigate(path);
  const key = (event) => { if (event.key === "Enter" || event.key === " ") open(); };
  return <tr onClick={open} onKeyDown={key} role="button" tabIndex="0"><td><strong>{value(data.name || data.legal_name, "Name not captured")}</strong></td>{type === "provider" ? <><td>{value(data.npi, "NPI not captured")}</td><td>{value(data.provider_type, "Provider type not captured")}</td><td>{value(record.practices?.length || record.group_ids?.length, "0")}</td></> : <><td>{value(data.type_2_npi, "Type 2 NPI not captured")}</td><td>{value(data.tax_id_number, "Tax ID number not captured")}</td><td>{value(data.dba, "DBA not captured")}</td></>}<td><span className={`record-status ${issueCount ? "needs-review" : ""}`}>{issueCount ? `${issueCount} issue${issueCount === 1 ? "" : "s"}` : "Complete"}</span></td></tr>;
}

function Expirations() {
  const entityId = useEntity();
  const workspace = useWorkspace();
  const [params] = useSearchParams();
  const status = params.get("status");
  const request = useRequest(entityPath(entityId, "/expirations?limit=5000"));
  const dismissal = useIssueDismissal(entityId, { onDismissed: request.refresh });
  const records = (request.data?.expirations || []).filter((record) => !status || record.type === status);
  const canManageIssues = EDITABLE_RECORD_ROLES.has(workspace?.user?.role);
  document.title = "Expirations | Cozema Health";
  return <Shell active="expirations"><section className="workspace-hero"><p className="eyebrow">Credential lifecycle</p><h1>Expirations</h1><p className="lede">Every captured credential expiration across this client, with expired records first and upcoming dates in order.</p></section><section className="workspace-panel"><div className="section-heading"><div><p className="eyebrow">{status === "expiring" ? "Next 90 days" : "All captured dates"}</p><h2>{request.loading ? <LoadingCount label="expiration count" /> : records.length} {status === "expiring" ? "credentials expiring soon" : "expiration records"}</h2></div></div><div className="provider-table-wrap"><table className="provider-table expiration-table"><thead><tr><th>Credential</th><th>Provider</th><th>Expiration date</th><th>Status</th>{canManageIssues && <th className="expiration-action-heading"><span className="sr-only">Actions</span></th>}</tr></thead><tbody>{request.loading ? <EmptyRow columns={canManageIssues ? 5 : 4}><InlineLoading text="Loading expiration records..." /></EmptyRow> : request.error ? <EmptyRow columns={canManageIssues ? 5 : 4}>{request.error.message}</EmptyRow> : records.length ? records.map((record, index) => <ExpirationRow record={record} entityId={entityId} canManageIssues={canManageIssues} onDismiss={dismissal.requestDismissal} key={`${record.provider_id}-${record.id}-${index}`} />) : <EmptyRow columns={canManageIssues ? 5 : 4}>No credential expiration dates have been captured yet.</EmptyRow>}</tbody></table></div>{(dismissal.message || dismissal.error) && <p className={`operation-notice ${dismissal.error ? "is-error" : ""}`} role={dismissal.error ? "alert" : "status"}>{dismissal.error || dismissal.message}</p>}</section><IssueDismissalDialog issue={dismissal.pendingIssue} error={dismissal.error} saving={dismissal.saving} onCancel={dismissal.cancelDismissal} onConfirm={dismissal.confirmDismissal} /></Shell>;
}

function ExpirationRow({ record, entityId, canManageIssues, onDismiss }) {
  const navigate = useNavigate();
  const open = () => navigate(href(`/providers/${encodeURIComponent(record.provider_id)}/view`, entityId));
  const keyDown = (event) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === "Enter" || event.key === " ") open();
  };
  return <tr className={`expiration-row ${record.type === "expired" ? "is-expired" : ""}`} tabIndex="0" role="button" onClick={open} onKeyDown={keyDown}><td>{value(record.credential_label, "Unclassified credential")}<span className="expiration-subtitle">{issueTypeLabel(record.credential_category || "unclassified")}</span></td><td><strong>{value(record.provider_name)}</strong><span className="expiration-subtitle">{value(record.provider_npi, "NPI not captured")}</span></td><td className={`expiration-date ${record.type === "expired" ? "is-expired" : ""}`}>{shortDate(record.expiration_date)}</td><td><span className={`severity-tag status-${record.type}`}>{record.type === "expired" ? `${record.days_past_expiration} days past due` : `${record.days_until_expiration} days remaining`}</span></td>{canManageIssues && <td className="expiration-action"><IssueDismissButton issue={record} onDismiss={onDismiss} /></td>}</tr>;
}

function Issues() {
  const entityId = useEntity();
  const [scope, setScope] = useState("all");
  const [severity, setSeverity] = useState("");
  const [issueType, setIssueType] = useState("");
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [visible, setVisible] = useState(12);
  const issueRequest = useRequest(entityPath(entityId, "/issues"));
  const rows = allIssues(issueRequest.data?.issues || [])
    .filter((issue) => (scope === "all" || issue.recordType === scope) && (!severity || issue.severity === severity) && (!issueType || issue.type === issueType) && [issue.recordName, issueLabel(issue), issue.source_file_name, issue.location_name].join(" ").toLowerCase().includes(deferredQuery.toLowerCase()))
    .sort((left, right) => priority(left) - priority(right));
  const groups = Object.values(rows.reduce((result, issue) => { const key = `${issue.recordType}:${issue.recordId}`; if (!result[key]) result[key] = { ...issue, items: [] }; result[key].items.push(issue); return result; }, {}));
  const visibleGroups = groups.slice(0, visible);
  const loading = issueRequest.loading;
  useEffect(() => { document.title = "Open issues | Cozema Health"; }, []);
  return <Shell active="providers"><section className="workspace-hero"><p className="eyebrow">Credentialing operations</p><h1>Open issues</h1><p className="lede">Every outstanding discrepancy, missing field, and expiration that needs follow-up for this client.</p></section><section className="directory-section"><div className="section-heading"><div><p className="eyebrow">Needs attention</p><h2>{loading ? <LoadingCount label="issues" /> : `${rows.length} issues shown`}</h2></div></div><div className="issue-controls"><label className="issue-search">⌕<input value={query} onChange={(event) => { setQuery(event.target.value); setVisible(12); }} type="search" placeholder="Search providers, practices, locations, or issues" /></label><label>Severity<select value={severity} onChange={(event) => { setSeverity(event.target.value); setVisible(12); }}><option value="">All severities</option><option value="critical">Critical</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label><label>Issue type<select value={issueType} onChange={(event) => { setIssueType(event.target.value); setVisible(12); }}><option value="">All issue types</option><option value="missing_data">Missing data</option><option value="discrepancy">Discrepancy</option><option value="expired">Expired</option><option value="expiring">Expiring soon</option></select></label><label>Filter by<select value={scope} onChange={(event) => { setScope(event.target.value); setVisible(12); }}><option value="all">All records</option><option value="provider">Providers</option><option value="practice">Practices</option></select></label></div><div className="issue-groups">{loading ? <div className="loading-row"><span className="spinner" />Loading open issues...</div> : issueRequest.error ? <div className="empty-state">{issueRequest.error.message}</div> : visibleGroups.length ? visibleGroups.map((group) => <IssueGroup group={group} entityId={entityId} key={`${group.recordType}-${group.recordId}`} />) : <div className="empty-state">No issues match the selected filters.</div>}</div>{groups.length > visible && <div className="issue-load-more"><span>Showing {visibleGroups.length} of {groups.length} records</span><button className="secondary-button" type="button" onClick={() => setVisible((count) => count + 12)}>Load more records</button></div>}</section></Shell>;
}

function IssueGroup({ group, entityId }) {
  const [expanded, setExpanded] = useState(true);
  const recordPath = href(`/${group.recordType === "practice" ? "practices" : "providers"}/${encodeURIComponent(group.recordId)}/view`, entityId);
  return <article className={`issue-provider-group ${expanded ? "is-expanded" : ""}`}><button className="issue-provider-heading" type="button" onClick={() => setExpanded((current) => !current)}><span><p className="eyebrow">{group.recordType === "practice" ? "Practice" : "Provider"}</p><strong>{group.recordName}</strong><small>{group.recordType === "provider" ? value(providerData(group.record).npi, "NPI not captured") : value(group.record?.type_2_npi, "Type 2 NPI not captured")}</small></span><span className="issue-provider-actions"><span className="issue-count-badge">{group.items.length} issue{group.items.length === 1 ? "" : "s"}</span><span className="group-expand-label">{expanded ? "Hide issues" : "Show issues"}</span></span></button>{expanded && <div className="issue-group-list">{group.items.map((issue, index) => <IssueRow issue={issue} entityId={entityId} key={`${issue.id || issueLabel(issue)}-${index}`} />)}<Link className="issue-provider-record-link" to={recordPath}>Open {group.recordType} record</Link></div>}</article>;
}

function IssueRow({ issue, entityId }) {
  const navigate = useNavigate();
  const path = href(`/${issue.recordType === "practice" ? "practices" : "providers"}/${encodeURIComponent(issue.recordId)}/issues/${encodeURIComponent(issue.id)}/view`, entityId);
  const open = () => navigate(path);
  const source = issue.type === "missing_data" ? "Not applicable" : value(issue.source_file_name || issue.file_name, "Source file not captured");
  return <button className={`issue-group-row ${issue.type === "expired" ? "is-expired" : ""}`} type="button" onClick={open}><span className="issue-row-summary"><strong>{issueLabel(issue)}</strong><span className="issue-scope">{value(issue.location_name || issue.credential_label, issue.recordType === "practice" ? "Practice record" : "Provider record")}</span></span><span className="issue-row-context"><span className="issue-tags"><span className={`severity-tag severity-${issue.severity}`}>{value(issue.severity, "high")}</span><span className={`issue-type-tag issue-type-${issue.type}`}>{issueTypeLabel(issue.type)}</span></span><span className="issue-source-context">{source}</span></span></button>;
}

function Reports() {
  const entityId = useEntity();
  const catalog = useRequest(entityPath(entityId, "/reports"));
  const groups = useRequest(entityPath(entityId, "/groups?limit=500"));
  const providers = useRequest(entityPath(entityId, "/providers?limit=500"));
  const [reportType, setReportType] = useState("");
  const [filters, setFilters] = useState({ practice_id: "", provider_id: "", issue_type: "", expiration_status: "" });
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const definitions = catalog.data?.reports || [];
  const definition = definitions.find((item) => item.id === reportType) || definitions[0];
  useEffect(() => { document.title = "Reports | Cozema Health"; }, []);
  useEffect(() => { if (!reportType && definitions[0]) setReportType(definitions[0].id); }, [reportType, definitions]);
  const preview = async () => {
    const selected = reportType || definition?.id;
    if (!selected) return;
    setLoading(true);
    setReport(null);
    try {
      const query = new URLSearchParams(Object.entries(filters).filter(([, item]) => item));
      setReport(await fetchJson(entityPath(entityId, `/reports/${encodeURIComponent(selected)}?${query}`)));
    } catch (error) { setReport({ error }); } finally { setLoading(false); }
  };
  useEffect(() => { if (definition) preview(); }, [reportType]); // Initial preview mirrors the vanilla builder.
  const exportReport = async () => {
    const query = new URLSearchParams(Object.entries(filters).filter(([, item]) => item));
    try {
      const response = await fetchWithAuth(entityPath(entityId, `/reports/${encodeURIComponent(reportType || definition?.id)}/export?${query}`));
      if (!response.ok) throw new Error(`Export failed (${response.status})`);
      const file = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = file;
      link.download = "credentialing-report.csv";
      link.click();
      URL.revokeObjectURL(file);
    } catch (error) {
      setReport({ error });
    }
  };
  return <Shell active="reports"><section className="workspace-hero reports-hero"><p className="eyebrow">Operational reporting</p><h1>Create a report.</h1><p className="lede">Choose a focused report, refine its scope, preview the live data, and export a CSV when it is ready to share.</p></section><section className="report-template-section"><div className="section-heading"><div><p className="eyebrow">Report templates</p><h2>What do you need to review?</h2></div></div><div className="report-templates">{catalog.loading ? <div className="loading-row"><span className="spinner" />Loading report templates...</div> : definitions.map((item) => <button className={`report-template ${item.id === (reportType || definition?.id) ? "selected" : ""}`} type="button" key={item.id} onClick={() => setReportType(item.id)}><span>Template</span><strong>{item.title}</strong><p>{item.description}</p><b>Use report</b></button>)}</div></section><section className="workspace-panel report-builder"><div className="section-heading"><div><p className="eyebrow">Report builder</p><h2>Set the scope</h2></div><button className="secondary-button" type="button" disabled={!report || report.error} onClick={exportReport}>Export CSV</button></div><p className="workspace-caption">{definition?.description || "Select a report template to begin."}</p><form className="report-filters" onSubmit={(event) => { event.preventDefault(); preview(); }}><FilterSelect label="Report type" value={reportType || definition?.id || ""} onChange={setReportType} options={definitions.map((item) => [item.id, item.title])} /><FilterSelect label="Practice" value={filters.practice_id} onChange={(item) => setFilters((old) => ({ ...old, practice_id: item }))} options={(groups.data?.groups || []).map((group) => [group.id, value(group.legal_name || group.name, "Unnamed practice")])} empty="All practices" /><FilterSelect label="Provider" value={filters.provider_id} onChange={(item) => setFilters((old) => ({ ...old, provider_id: item }))} options={(providers.data?.providers || []).map((provider) => [provider.id, recordName(provider, "Unnamed provider")])} empty="All providers" /><FilterSelect label="Issue type" value={filters.issue_type} onChange={(item) => setFilters((old) => ({ ...old, issue_type: item }))} options={[["missing_data", "Missing data"], ["discrepancy", "Discrepancy"], ["expired", "Expired"], ["expiring", "Expiring soon"]]} empty="All issue types" /><FilterSelect label="Expiration status" value={filters.expiration_status} onChange={(item) => setFilters((old) => ({ ...old, expiration_status: item }))} options={[["expiring", "Expiring soon"], ["expired", "Expired"]]} empty="All expiration statuses" /><button className="primary-button" type="submit">Preview report</button></form><p className="report-note">Exports are generated on demand and are not stored by this application.</p></section><ReportPreview report={report} loading={loading} entityId={entityId} /></Shell>;
}

function FilterSelect({ label, value: selected, onChange, options, empty }) { return <label>{label}<select value={selected} onChange={(event) => onChange(event.target.value)}>{empty && <option value="">{empty}</option>}{options.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>; }

function ReportPreview({ report, loading, entityId }) {
  const navigate = useNavigate();
  return <section className="workspace-panel report-preview" aria-live="polite"><div className="section-heading"><div><p className="eyebrow">Live preview</p><h2>{loading ? "Building report" : report?.error ? "Report unavailable" : report?.title || "Preparing report"}</h2></div><span className={`report-row-count ${loading ? "is-loading" : ""}`}>{loading ? <LoadingCount label="report preview" /> : report && !report.error ? `${report.row_count} ${report.row_count === 1 ? "row" : "rows"}` : ""}</span></div><div className="provider-table-wrap"><table className="provider-table report-table"><thead>{report?.columns && <tr>{report.columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr>}</thead><tbody>{loading ? <EmptyRow columns="1"><InlineLoading text="Building live report preview..." /></EmptyRow> : report?.error ? <EmptyRow columns="1">{report.error.message}</EmptyRow> : report?.rows?.length ? report.rows.map((row, index) => <tr className={row._provider_id ? "report-row" : ""} key={index} onClick={() => row._provider_id && navigate(href(`/providers/${encodeURIComponent(row._provider_id)}/view`, entityId))}>{report.columns.map((column) => <td key={column.key}>{value(row[column.key])}</td>)}</tr>) : <EmptyRow columns={report?.columns?.length || 1}>No records match the current report filters.</EmptyRow>}</tbody></table></div></section>;
}

function Activity() {
  const entityId = useEntity();
  const [params] = useSearchParams();
  const recordId = params.get("record_id");
  const scopedType = params.get("record_type");
  const scoped = recordId && ["provider", "practice"].includes(scopedType);
  const path = scoped ? entityPath(entityId, `/${scopedType === "practice" ? "groups" : "providers"}/${encodeURIComponent(recordId)}/activity?limit=500`) : entityPath(entityId, "/activity?limit=500");
  const request = useRequest(path);
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [recordType, setRecordType] = useState(scoped ? scopedType : "all");
  const [category, setCategory] = useState("");
  const [visible, setVisible] = useState(25);
  const activity = request.data?.activity || [];
  const categories = [...new Set(activity.map((entry) => entry.document_category).filter(Boolean))].sort();
  const filtered = activity.filter((entry) => (recordType === "all" || entry.record_type === recordType) && (!category || entry.document_category === category) && [entry.record_name, entry.file_name, categoryLabel(entry.document_category), ...(entry.changed_fields || []).map(fieldLabel)].join(" ").toLowerCase().includes(deferredQuery.toLowerCase()));
  const display = filtered.slice(0, visible);
  const recordTitle = request.data?.record?.record_name || activity[0]?.record_name || (scopedType === "practice" ? "Practice" : "Provider");
  useEffect(() => { document.title = scoped ? `${recordTitle} Activity | Cozema Health` : "Activity | Cozema Health"; }, [scoped, recordTitle]);
  return <Shell active="activity"><section className="workspace-hero activity-hero"><p className="eyebrow">Client audit trail</p><h1>{scoped ? `${recordTitle} activity` : "Activity"}</h1><p className="lede">{scoped ? `Every recorded change to this ${scopedType}.` : "Track document imports and authorized manual updates across provider and practice records."}</p></section><section className="workspace-panel"><div className="section-heading"><div><p className="eyebrow">Revision history</p><h2>{request.loading ? <LoadingCount label="revisions" /> : filtered.length} revisions</h2></div></div><div className="activity-controls"><label className="issue-search">⌕<input value={query} onChange={(event) => { setQuery(event.target.value); setVisible(25); }} type="search" placeholder="Search records, files, or changed fields" /></label>{!scoped && <FilterSelect label="Record type" value={recordType} onChange={(item) => { setRecordType(item); setVisible(25); }} options={[["all", "All records"], ["provider", "Providers"], ["practice", "Practices"]]} />}{<FilterSelect label="Document category" value={category} onChange={(item) => { setCategory(item); setVisible(25); }} options={categories.map((item) => [item, categoryLabel(item)])} empty="All document categories" />}</div><div className="provider-table-wrap activity-table-wrap"><table className="provider-table activity-table"><thead><tr><th>When</th><th>Record</th><th>Source document</th><th>Category</th><th>What changed</th></tr></thead><tbody>{request.loading ? <EmptyRow columns="5"><InlineLoading text="Loading revision history..." /></EmptyRow> : request.error ? <EmptyRow columns="5">{request.error.message}</EmptyRow> : display.length ? display.map((entry, index) => <ActivityRow entry={entry} key={`${entry.id || entry.file_name}-${index}`} />) : <EmptyRow columns="5">No revisions match the selected filters.</EmptyRow>}</tbody></table></div>{filtered.length > visible && <div className="activity-load-more"><span>Showing {display.length} of {filtered.length} revisions</span><button className="secondary-button" type="button" onClick={() => setVisible((count) => count + 25)}>Load more revisions</button></div>}</section></Shell>;
}

function ActivityRow({ entry }) {
  const fields = (entry.changed_fields || []).map(fieldLabel);
  return <tr className="activity-row"><td><span className="activity-date">{timestampLabel(entry.recorded_at)}</span></td><td><strong>{value(entry.record_name)}</strong><span className="activity-record-type">{entry.record_type === "practice" ? "Practice" : "Provider"}</span></td><td>{value(entry.file_name, "File name not captured")}{entry.actor?.display_name && <span className="activity-actor">Updated by {entry.actor.display_name}</span>}</td><td><span className="activity-category">{categoryLabel(entry.document_category)}</span></td><td>{fields.length ? <span className="activity-changes">{fields.slice(0, 3).map((item) => <span key={item}>{item}</span>)}{fields.length > 3 && <span>+{fields.length - 3} more</span>}</span> : <span className="activity-change-empty">Imported record data</span>}</td></tr>;
}

const memberRoles = [
  ["customer_admin", "Customer Admin"],
  ["manager", "Manager"],
  ["credentialing_specialist", "Credentialing Specialist"],
  ["client", "Client"],
];

function Users() {
  const entityId = useEntity();
  const workspace = useWorkspace();
  const canManageMembers = ["super_admin", "customer_admin", "manager"].includes(workspace?.user?.role);
  const usesBetterAuth = workspace?.auth_provider === "better_auth";
  const betterAuthBaseUrl = workspace?.auth_base_url;
  const organizationId = workspace?.user?.better_auth_org_id;
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("client");
  const [message, setMessage] = useState("");
  const [actionId, setActionId] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const organizationReady = !usesBetterAuth || Boolean(organizationId);
  const request = useRequest(!organizationReady ? null : usesBetterAuth
    ? `${betterAuthBaseUrl}/api/auth/organization/list-members?organizationId=${encodeURIComponent(organizationId || "")}&refresh=${refreshKey}`
    : entityPath(entityId, `/members?refresh=${refreshKey}`));
  const members = (request.data?.members || []).map((member) => ({
    ...member,
    display_name: member.display_name || member.user?.name,
    email: member.email || member.user?.email,
  }));
  useEffect(() => { document.title = "Users | Cozema Health"; }, []);
  const refreshMembers = () => setRefreshKey((value) => value + 1);
  const invite = async (event) => {
    event.preventDefault();
    if (!organizationReady) {
      setMessage("This client workspace is still being configured. Refresh the page in a moment and try again.");
      return;
    }
    setActionId("invite");
    setMessage("");
    try {
      const path = usesBetterAuth
        ? `${betterAuthBaseUrl}/api/auth/organization/invite-member`
        : entityPath(entityId, "/members/invitations");
      const body = usesBetterAuth ? { email, role, organizationId } : { email, role };
      await fetchJson(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setEmail("");
      setMessage("Invitation sent.");
      refreshMembers();
    } catch (error) { setMessage(error.message); } finally { setActionId(""); }
  };
  const changeRole = async (memberId, nextRole) => {
    setActionId(memberId);
    setMessage("");
    try {
      const path = usesBetterAuth
        ? `${betterAuthBaseUrl}/api/auth/organization/update-member-role`
        : entityPath(entityId, `/members/${encodeURIComponent(memberId)}`);
      const method = usesBetterAuth ? "POST" : "PATCH";
      const body = usesBetterAuth ? { memberId, role: nextRole, organizationId } : { role: nextRole };
      await fetchJson(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setMessage("Role updated.");
      refreshMembers();
    } catch (error) { setMessage(error.message); } finally { setActionId(""); }
  };
  const remove = async (member) => {
    if (!window.confirm(`Remove ${member.display_name || member.email} from this client?`)) return;
    setActionId(member.id);
    setMessage("");
    try {
      const path = usesBetterAuth
        ? `${betterAuthBaseUrl}/api/auth/organization/remove-member`
        : entityPath(entityId, `/members/${encodeURIComponent(member.id)}`);
      const method = usesBetterAuth ? "POST" : "DELETE";
      const body = usesBetterAuth ? JSON.stringify({ memberIdOrEmail: member.id, organizationId }) : undefined;
      await fetchJson(path, { method, headers: usesBetterAuth ? { "Content-Type": "application/json" } : undefined, body });
      setMessage("User removed from this client.");
      refreshMembers();
    } catch (error) { setMessage(error.message); } finally { setActionId(""); }
  };
  return <Shell active="users"><section className="workspace-hero"><p className="eyebrow">Client administration</p><h1>Users</h1><p className="lede">Invite your team, assign a workspace role, and remove access when it is no longer needed.</p></section>{!canManageMembers ? <section className="workspace-panel workspace-empty"><div><strong>Users are managed by your administrator.</strong><p>Your current role can view credentialing data but cannot manage client access.</p></div></section> : !organizationReady ? <section className="workspace-panel workspace-empty"><div><strong>Preparing this client workspace.</strong><p>Client access is being configured. Refresh this page in a moment to manage users.</p></div></section> : <><section className="workspace-panel user-invite-panel"><div className="section-heading"><div><p className="eyebrow">Invite a user</p><h2>Add someone to this client</h2></div></div><form className="user-invite-form" onSubmit={invite}><label>Email address<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@organization.com" required /></label><FilterSelect label="Role" value={role} onChange={setRole} options={memberRoles} /><button className="primary-button" type="submit" disabled={actionId === "invite"}>{actionId === "invite" ? "Sending..." : "Send invitation"}</button></form>{message && <p className="user-action-message" role="status">{message}</p>}</section><section className="workspace-panel users-list"><div className="section-heading"><div><p className="eyebrow">Client users</p><h2>{request.loading ? <LoadingCount label="users" /> : `${members.length} ${members.length === 1 ? "user" : "users"}`}</h2></div></div><div className="provider-table-wrap"><table className="provider-table users-table"><thead><tr><th>User</th><th>Email</th><th>Role</th><th /></tr></thead><tbody>{request.loading ? <EmptyRow columns="4"><InlineLoading text="Loading users..." /></EmptyRow> : request.error ? <EmptyRow columns="4">{request.error.message}</EmptyRow> : members.length ? members.map((member) => <tr key={member.id}><td><strong>{value(member.display_name, "User")}</strong></td><td>{value(member.email, "Email not captured")}</td><td><select aria-label={`Role for ${member.display_name || member.email}`} value={member.role || "client"} disabled={member.id === workspace?.user?.uid || actionId === member.id} onChange={(event) => changeRole(member.id, event.target.value)}>{memberRoles.map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></td><td>{member.id === workspace?.user?.uid ? <span className="user-self-label">You</span> : <button className="text-button danger-button" type="button" disabled={actionId === member.id} onClick={() => remove(member)}>{actionId === member.id ? "Working..." : "Remove"}</button>}</td></tr>) : <EmptyRow columns="4">No users belong to this client yet.</EmptyRow>}</tbody></table></div></section></>}</Shell>;
}

function Workspace({ section }) {
  const copy = { enrollment: ["Enrollment", "Manage payer enrollment workflows across practices and providers.", "Enrollment workflows are coming soon", "This area will centralize payer participation, packet status, and enrollment milestones."] }[section];
  useEffect(() => { document.title = `${copy[0]} | Cozema Health`; }, [copy]);
  return <Shell active={section}><section className="workspace-hero"><p className="eyebrow">Workspace</p><h1>{copy[0]}</h1><p className="lede">{copy[1]}</p></section><section className="workspace-panel"><div className="workspace-empty"><div><strong>{copy[2]}</strong><p>{copy[3]}</p></div></div></section></Shell>;
}

function LegacyDataConnections() {
  const entityId = useEntity();
  const workspace = useWorkspace();
  const [params] = useSearchParams();
  const connectionRequest = useRequest(entityPath(entityId, "/connections"));
  const [connectionError, setConnectionError] = useState("");
  const [connectionMessage, setConnectionMessage] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);
  const [folderPickerOpen, setFolderPickerOpen] = useState(false);
  const [folders, setFolders] = useState([]);
  const [foldersLoading, setFoldersLoading] = useState(false);
  const [folderError, setFolderError] = useState("");
  const [folderQuery, setFolderQuery] = useState("");
  const [selectingFolderId, setSelectingFolderId] = useState("");
  const [startingMonitoring, setStartingMonitoring] = useState(false);
  const automaticMonitoringAttempt = useRef(false);
  const canManageDocuments = ["super_admin", "customer_admin", "credentialing_specialist"].includes(workspace?.user?.role);
  const drive = connectionRequest.data?.google_drive;
  const visibleFolders = folders.filter((folder) => folder.name?.toLowerCase().includes(folderQuery.trim().toLowerCase()));
  const startConnection = () => {
    setConnecting(true);
    setConnectionError("");
    setConnectionMessage("");
    fetchJson(entityPath(entityId, "/oauth/google/start"))
      .then(({ authorization_url: authorizationUrl }) => {
        if (!authorizationUrl) throw new Error("Google Drive authorization could not be started.");
        window.location.assign(authorizationUrl);
      })
      .catch((error) => {
        setConnecting(false);
        setConnectionError(error.message || "We could not start the Google Drive connection.");
      });
  };
  const disconnectDrive = () => {
    setDisconnecting(true);
    setConnectionError("");
    setConnectionMessage("");
    fetchJson(entityPath(entityId, "/connections/google-drive"), { method: "DELETE" })
      .then(() => {
        setConfirmingDisconnect(false);
        setConnectionMessage("Google Drive disconnected. Existing credentialing records were not changed.");
        connectionRequest.refresh();
      })
      .catch((error) => setConnectionError(error.message || "We could not disconnect Google Drive."))
      .finally(() => setDisconnecting(false));
  };
  const openFolderPicker = () => {
    setFolderPickerOpen(true);
    setFoldersLoading(true);
    setFolderQuery("");
    setConnectionError("");
    fetchJson(entityPath(entityId, "/drive/folders"))
      .then((result) => setFolders(result.folders || []))
      .catch((error) => setConnectionError(error.message || "We could not load Google Drive folders."))
      .finally(() => setFoldersLoading(false));
  };
  const selectFolder = (folder) => {
    setSelectingFolderId(folder.id);
    setConnectionError("");
    fetchJson(entityPath(entityId, "/drive/intake-folder"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_id: folder.id }),
    })
      .then(({ folder: selectedFolder }) => {
        setFolderPickerOpen(false);
        setConnectionMessage(`${selectedFolder.name} is now the intake folder and document monitoring is active.`);
        connectionRequest.refresh();
      })
      .catch((error) => setConnectionError(error.message || "We could not save the intake folder."))
      .finally(() => setSelectingFolderId(""));
  };
  const startMonitoring = () => {
    setStartingMonitoring(true);
    setConnectionError("");
    fetchJson(entityPath(entityId, "/drive/watch"), { method: "POST" })
      .then(() => {
        setConnectionMessage("Document monitoring is active for the selected intake folder.");
        connectionRequest.refresh();
      })
      .catch((error) => setConnectionError(error.message || "We could not start document monitoring."))
      .finally(() => setStartingMonitoring(false));
  };
  useEffect(() => {
    if (!drive?.connected || !drive.intake_folder_configured || drive.watching || !canManageDocuments || automaticMonitoringAttempt.current) return;
    automaticMonitoringAttempt.current = true;
    startMonitoring();
  }, [drive?.connected, drive?.intake_folder_configured, drive?.watching, canManageDocuments]);
  useEffect(() => { document.title = "Data connections | Cozema Health"; }, []);
  return <Shell active="connections"><section className="workspace-hero"><p className="eyebrow">Workspace setup</p><h1>Data connections</h1><p className="lede">Securely connect the document sources this client uses for credentialing intake.</p></section><section className="workspace-panel connection-panel"><div className="section-heading"><div><p className="eyebrow">Document source</p><h2>Google Drive</h2></div><span className={`connection-status ${drive?.connected ? "is-connected" : ""}`}>{drive?.connected ? "Connected" : "Not connected"}</span></div>{connectionRequest.loading ? <div className="loading-row"><span className="spinner" />Checking Google Drive connection...</div> : connectionRequest.error ? <div className="empty-state">{connectionRequest.error.message}</div> : <div className="connection-content"><div className="connection-copy"><p>{drive?.connected ? "This workspace can securely access its connected Google Drive account." : "Authorize the Google account that owns this client's credentialing intake folder."}</p><div className="ai-intake-note"><span className="ai-magic-mark" aria-hidden="true">✦</span><div><strong>AI-assisted intake</strong><p>Once connected, files added to this Drive are read and sorted by AI into the appropriate provider and practice records. Review any flagged issues before relying on the data.</p></div></div>{drive?.connected && <dl className="connection-details"><div><dt>Intake folder</dt><dd>{drive.intake_folder_configured ? value(drive.intake_folder_name, "Selected folder") : "Not selected yet"}</dd></div><div><dt>Document monitoring</dt><dd>{drive.watching ? "Active" : startingMonitoring ? "Activating..." : drive.intake_folder_configured ? "Activating..." : "Starts after folder selection"}</dd></div></dl>}{params.get("connected") === "google-drive" && <p className="connection-success" role="status">Google Drive connected successfully. Choose the intake folder next.</p>}{connectionMessage && <p className="connection-success" role="status">{connectionMessage}</p>}{connectionError && <p className="connection-error" role="alert">{connectionError}</p>}</div><div className="connection-actions">{canManageDocuments && !drive?.connected && <button className="primary-button" type="button" onClick={startConnection} disabled={connecting}>{connecting ? "Opening Google..." : "Connect Google Drive"}</button>}{canManageDocuments && drive?.connected && <div className="connection-manage-actions">{drive.intake_folder_configured ? <button className="secondary-button" type="button" onClick={openFolderPicker}>Change intake folder</button> : <button className="primary-button" type="button" onClick={openFolderPicker}>Choose intake folder</button>}{confirmingDisconnect ? <div className="connection-confirmation"><p>Disconnect this Google Drive? New files will no longer be monitored or imported.</p><div><button className="secondary-button" type="button" onClick={() => setConfirmingDisconnect(false)} disabled={disconnecting}>Cancel</button><button className="primary-button danger-primary-button" type="button" onClick={disconnectDrive} disabled={disconnecting}>{disconnecting ? "Disconnecting..." : "Disconnect Drive"}</button></div></div> : <button className="text-button danger-button" type="button" onClick={() => setConfirmingDisconnect(true)}>Disconnect Drive</button>}</div>}{!canManageDocuments && !drive?.connected && <p className="connection-read-only">A workspace administrator or credentialing specialist can connect Google Drive.</p>}</div></div>}</section>{folderPickerOpen && <div className="folder-picker-backdrop" role="presentation"><section className="folder-picker" role="dialog" aria-modal="true" aria-labelledby="folder-picker-title"><div className="folder-picker-header"><div><p className="eyebrow">Google Drive</p><h2 id="folder-picker-title">Choose intake folder</h2><p>Select the folder whose new files should be processed for this client. Monitoring will start automatically.</p></div><button className="dialog-close" type="button" onClick={() => setFolderPickerOpen(false)} aria-label="Close folder picker">×</button></div><label className="folder-picker-search">Search folders<input type="search" value={folderQuery} onChange={(event) => setFolderQuery(event.target.value)} placeholder="Filter accessible Google Drive folders" autoFocus /></label><div className="folder-picker-list">{foldersLoading ? <InlineLoading text="Loading Google Drive folders..." /> : visibleFolders.length ? visibleFolders.map((folder) => <button className="folder-picker-option" type="button" key={folder.id} onClick={() => selectFolder(folder)} disabled={Boolean(selectingFolderId)}><span className="folder-picker-icon" aria-hidden="true">□</span><span><strong>{folder.name}</strong><small>{folder.modifiedTime ? `Modified ${shortDate(folder.modifiedTime)}` : "Google Drive folder"}</small></span>{selectingFolderId === folder.id && <span className="spinner" />}</button>) : <p className="folder-picker-empty">No accessible folders match that search.</p>}</div></section></div>}</Shell>;
}

function DataConnections() {
  const entityId = useEntity();
  const workspace = useWorkspace();
  const [params] = useSearchParams();
  const connectionRequest = useRequest(entityPath(entityId, "/connections"));
  const [connectionError, setConnectionError] = useState("");
  const [connectionMessage, setConnectionMessage] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);
  const [folderPickerOpen, setFolderPickerOpen] = useState(false);
  const [folders, setFolders] = useState([]);
  const [foldersLoading, setFoldersLoading] = useState(false);
  const [folderError, setFolderError] = useState("");
  const [folderQuery, setFolderQuery] = useState("");
  const [selectingFolderId, setSelectingFolderId] = useState("");
  const [repairingMonitoring, setRepairingMonitoring] = useState(false);
  const automaticMonitoringAttempt = useRef(false);
  const canManageDocuments = ["super_admin", "customer_admin", "credentialing_specialist"].includes(workspace?.user?.role);
  const drive = connectionRequest.data?.google_drive;
  const visibleFolders = folders.filter((folder) => folder.name?.toLowerCase().includes(folderQuery.trim().toLowerCase()));

  const startConnection = () => {
    setConnecting(true);
    setConnectionError("");
    setConnectionMessage("");
    fetchJson(entityPath(entityId, "/oauth/google/start"))
      .then(({ authorization_url: authorizationUrl }) => {
        if (!authorizationUrl) throw new Error("Google Drive authorization could not be started.");
        window.location.assign(authorizationUrl);
      })
      .catch((error) => {
        setConnecting(false);
        setConnectionError(error.message || "We could not start the Google Drive connection.");
      });
  };

  const disconnectDrive = () => {
    setDisconnecting(true);
    setConnectionError("");
    setConnectionMessage("");
    fetchJson(entityPath(entityId, "/connections/google-drive"), { method: "DELETE" })
      .then(() => {
        setConfirmingDisconnect(false);
        setConnectionMessage("Google Drive disconnected. Existing credentialing records were not changed.");
        connectionRequest.refresh();
      })
      .catch((error) => setConnectionError(error.message || "We could not disconnect Google Drive."))
      .finally(() => setDisconnecting(false));
  };

  const openFolderPicker = () => {
    setFolderPickerOpen(true);
    setFoldersLoading(true);
    setFolderQuery("");
    setFolderError("");
    setConnectionError("");
    fetchJson(entityPath(entityId, "/drive/folders"))
      .then((result) => setFolders(result.folders || []))
      .catch((error) => setFolderError(error.message || "We could not load Google Drive folders."))
      .finally(() => setFoldersLoading(false));
  };

  const selectFolder = (folder) => {
    setSelectingFolderId(folder.id);
    setFolderError("");
    fetchJson(entityPath(entityId, "/drive/intake-folder"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_id: folder.id }),
    })
      .then(({ folder: selectedFolder, intake_ready: isReady }) => {
        if (!isReady) throw new Error("Google Drive did not confirm document monitoring.");
        setFolderPickerOpen(false);
        setConnectionMessage(`${selectedFolder.name} is now the intake folder and monitoring is active.`);
        connectionRequest.refresh();
      })
      .catch((error) => setFolderError(error.message || "We could not save the intake folder."))
      .finally(() => setSelectingFolderId(""));
  };

  useEffect(() => { document.title = "Data connections | Cozema Health"; }, []);

  useEffect(() => {
    // Older connections may have a folder from before selection and monitoring
    // became one atomic operation. Repair those connections without user action.
    if (!drive?.connected || !drive.intake_folder_configured || drive.intake_ready || !canManageDocuments || automaticMonitoringAttempt.current) return undefined;

    automaticMonitoringAttempt.current = true;
    let active = true;
    setRepairingMonitoring(true);
    setConnectionError("");
    fetchJson(entityPath(entityId, "/drive/watch"), { method: "POST" })
      .then(({ intake_ready: isReady }) => {
        if (!isReady) throw new Error("Google Drive did not confirm document monitoring.");
        if (active) {
          setConnectionMessage("Document monitoring is active for the selected intake folder.");
          connectionRequest.refresh();
        }
      })
      .catch((error) => active && setConnectionError(error.message || "We could not automatically activate document monitoring. Choose the intake folder again to retry."))
      .finally(() => active && setRepairingMonitoring(false));

    return () => { active = false; };
  }, [canManageDocuments, drive?.connected, drive?.intake_folder_configured, drive?.intake_ready, entityId]);

  return <Shell active="connections">
    <section className="workspace-hero">
      <p className="eyebrow">Workspace setup</p>
      <h1>Data connections</h1>
      <p className="lede">Securely connect the document sources this client uses for credentialing intake.</p>
    </section>
    <section className="workspace-panel connection-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Document source</p>
          <div className="connection-source-title"><h2>Google Drive</h2><span className={`connection-status ${drive?.connected ? "is-connected" : ""}`}>{drive?.connected ? "Connected" : "Not connected"}</span></div>
        </div>
      </div>
      {connectionRequest.loading ? <div className="loading-row"><span className="spinner" />Checking Google Drive connection...</div> : connectionRequest.error ? <div className="empty-state">{connectionRequest.error.message}</div> : <div className="connection-content">
        <div className="connection-copy">
          <p>{drive?.connected ? "This workspace can securely access its connected Google Drive account." : "Authorize the Google account that owns this client's credentialing intake folder."}</p>
          <div className="ai-intake-note">
            <span className="ai-magic-mark" aria-hidden="true">✦</span>
            <div>
              <strong>AI-assisted intake</strong>
              <p>Once connected, files added to this Drive are read and sorted by AI into the appropriate provider and practice records. Review any flagged issues before relying on the data.</p>
            </div>
          </div>
          {drive?.connected && (canManageDocuments ? <button className="connection-intake-folder" type="button" onClick={openFolderPicker}>
            <span>Intake folder</span>
            <strong>{drive.intake_folder_configured ? value(drive.intake_folder_name, "Selected folder") : "Not selected yet"}</strong>
            <small>{drive.intake_ready ? "Monitoring active - change folder" : repairingMonitoring ? "Activating document monitoring..." : drive.intake_folder_configured ? "Preparing document monitoring..." : "Select a folder to begin"}</small>
          </button> : <dl className="connection-details">
            <div>
              <dt>Intake folder</dt>
              <dd>{drive.intake_folder_configured ? value(drive.intake_folder_name, "Selected folder") : "Not selected yet"}</dd>
            </div>
          </dl>)}
          {params.get("connected") === "google-drive" && <p className="connection-success" role="status">Google Drive connected successfully. Choose the intake folder next.</p>}
          {connectionMessage && <p className="connection-success" role="status">{connectionMessage}</p>}
          {connectionError && <p className="connection-error" role="alert">{connectionError}</p>}
        </div>
        <div className="connection-actions">
          {canManageDocuments && !drive?.connected && <button className="primary-button" type="button" onClick={startConnection} disabled={connecting}>{connecting ? "Opening Google..." : "Connect Google Drive"}</button>}
          {canManageDocuments && drive?.connected && <div className="connection-manage-actions">
            {confirmingDisconnect ? <div className="connection-confirmation">
              <p>Disconnect this Google Drive? New files will no longer be monitored or imported.</p>
              <div>
                <button className="secondary-button" type="button" onClick={() => setConfirmingDisconnect(false)} disabled={disconnecting}>Cancel</button>
                <button className="primary-button danger-primary-button" type="button" onClick={disconnectDrive} disabled={disconnecting}>{disconnecting ? "Disconnecting..." : "Disconnect Drive"}</button>
              </div>
            </div> : <button className="secondary-button connection-disconnect-button" type="button" onClick={() => setConfirmingDisconnect(true)}>Disconnect</button>}
          </div>}
          {!canManageDocuments && !drive?.connected && <p className="connection-read-only">A workspace administrator or credentialing specialist can connect Google Drive.</p>}
        </div>
      </div>}
    </section>
    {folderPickerOpen && <div className="folder-picker-backdrop" role="presentation">
      <section className="folder-picker" role="dialog" aria-modal="true" aria-labelledby="folder-picker-title">
        <div className="folder-picker-header">
          <div>
            <p className="eyebrow">Google Drive</p>
            <h2 id="folder-picker-title">Choose intake folder</h2>
            <p>Select the folder whose new files should be processed for this client. Monitoring starts automatically as part of this selection.</p>
          </div>
          <button className="dialog-close" type="button" onClick={() => setFolderPickerOpen(false)} aria-label="Close folder picker">×</button>
        </div>
        <label className="folder-picker-search">Search folders<input type="search" value={folderQuery} onChange={(event) => setFolderQuery(event.target.value)} placeholder="Filter accessible Google Drive folders" autoFocus /></label>
        {folderError && <p className="connection-error folder-picker-error" role="alert">{folderError}</p>}
        <div className="folder-picker-list">
          {foldersLoading ? <InlineLoading text="Loading Google Drive folders..." /> : visibleFolders.length ? visibleFolders.map((folder) => <button className="folder-picker-option" type="button" key={folder.id} onClick={() => selectFolder(folder)} disabled={Boolean(selectingFolderId)}><span className="folder-picker-icon" aria-hidden="true">□</span><span><strong>{folder.name}</strong><small>{folder.modifiedTime ? `Modified ${shortDate(folder.modifiedTime)}` : "Google Drive folder"}</small></span>{selectingFolderId === folder.id && <span className="spinner" />}</button>) : <p className="folder-picker-empty">No accessible folders match that search.</p>}
        </div>
      </section>
    </div>}
  </Shell>;
}

function ProviderRecord() {
  const entityId = useEntity();
  const workspace = useWorkspace();
  const navigate = useNavigate();
  const { providerId } = useParams();
  const request = useRequest(entityPath(entityId, `/providers/${encodeURIComponent(providerId)}`));
  const [editingSection, setEditingSection] = useState(null);
  const [optimisticRecord, setOptimisticRecord] = useState(null);
  const [saveMessage, setSaveMessage] = useState("");
  const [saveError, setSaveError] = useState("");
  const [linkedLocationDialogOpen, setLinkedLocationDialogOpen] = useState(false);
  const [affiliationsDialogOpen, setAffiliationsDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const saveSequence = useRef(0);
  // Provider details return the demographic fields under `provider`, while
  // credentials and issues live alongside it in the same response.
  const record = optimisticRecord || request.data;
  const data = providerData(record);
  const affiliations = (record?.affiliations || []).filter((affiliation) => affiliation.group?.id);
  const locations = uniqueLocations(affiliations.flatMap((affiliation) => affiliation.locations || []));
  const licenses = data.licenses || record?.licenses || [];
  const specialties = data.specialties || record?.specialties || [];
  const education = data.education || record?.education || [];
  const insurance = data.liability_insurance || data.insurance || record?.liability_insurance || [];
  const payers = record?.payer_enrollments || data.payers || data.payer_enrollment || record?.payers || [];
  const issues = recordIssues(record);
  const canEdit = EDITABLE_RECORD_ROLES.has(workspace?.user?.role);
  useEffect(() => { if (record) document.title = `${value(data.name, "Provider")} | Cozema Health`; }, [record, data.name]);
  useEffect(() => { setOptimisticRecord(null); setSaveMessage(""); setSaveError(""); setLinkedLocationDialogOpen(false); setAffiliationsDialogOpen(false); setDeleteDialogOpen(false); setDeleteError(""); }, [providerId]);
  if (request.loading) return <RecordLoading />;
  if (request.error) return <RecordError error={request.error} />;
  const edit = (section) => canEdit ? () => setEditingSection(section) : undefined;
  const saveSection = (fields) => {
    const previousRecord = record;
    const sequence = ++saveSequence.current;
    setOptimisticRecord(optimisticRecordUpdate("provider", previousRecord, fields));
    setEditingSection(null);
    setSaveError("");
    setSaveMessage("Changes saved locally. Syncing...");
    void fetchJson(entityPath(entityId, `/providers/${encodeURIComponent(providerId)}`), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fields }) })
      .then((savedRecord) => {
        if (sequence !== saveSequence.current) return;
        setOptimisticRecord(savedRecord);
        setSaveMessage("Changes saved.");
      })
      .catch((error) => {
        if (sequence !== saveSequence.current) return;
        setOptimisticRecord(previousRecord);
        setSaveMessage("");
        setSaveError(`${error.message || "We could not save your changes."} Your prior values have been restored.`);
      });
  };
  const savePracticeLocationLink = async (selectedGroupId, locationIds) => {
    setSaveError("");
    setSaveMessage("Saving practice location assignment...");
    try {
      const savedGroup = await fetchJson(entityPath(entityId, `/groups/${encodeURIComponent(selectedGroupId)}/providers/${encodeURIComponent(providerId)}`), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ location_ids: locationIds }),
      });
      const existingAffiliation = (record.affiliations || []).find((affiliation) => affiliation.group?.id === selectedGroupId);
      const membership = (savedGroup.providers || []).find((provider) => provider.id === providerId);
      const savedLocationIds = membership?.location_ids || locationIds;
      const nextAffiliation = {
        ...existingAffiliation,
        id: existingAffiliation?.id || `${providerId}-${selectedGroupId}`,
        provider_id: providerId,
        group_id: selectedGroupId,
        location_ids: savedLocationIds,
        group: {
          id: savedGroup.id,
          legal_name: savedGroup.legal_name,
          name: savedGroup.name,
          type_2_npi: savedGroup.type_2_npi,
        },
        locations: (savedGroup.locations || []).filter((location) => savedLocationIds.includes(location.id)),
      };
      setOptimisticRecord({
        ...record,
        affiliations: [
          ...(record.affiliations || []).filter((affiliation) => affiliation.group?.id !== selectedGroupId),
          nextAffiliation,
        ],
      });
      setSaveMessage("Practice location assignment saved.");
    } catch (error) {
      setSaveMessage("");
      setSaveError(error.message || "We could not save this practice location assignment.");
      throw error;
    }
  };
  const unlinkPractice = async (selectedGroupId) => {
    setSaveError("");
    setSaveMessage("Unlinking practice...");
    try {
      await fetchJson(entityPath(entityId, `/groups/${encodeURIComponent(selectedGroupId)}/providers/${encodeURIComponent(providerId)}`), { method: "DELETE" });
      setOptimisticRecord({
        ...record,
        affiliations: (record.affiliations || []).filter((affiliation) => affiliation.group?.id !== selectedGroupId),
      });
      setSaveMessage("Practice unlinked.");
    } catch (error) {
      setSaveMessage("");
      setSaveError(error.message || "We could not unlink this practice.");
      throw error;
    }
  };
  const deleteProviderRecord = () => {
    setDeleting(true);
    setDeleteError("");
    void fetchJson(entityPath(entityId, `/providers/${encodeURIComponent(providerId)}`), { method: "DELETE" })
      .then(() => navigate(href("/providers/view", entityId)))
      .catch((error) => {
        setDeleting(false);
        setDeleteError(error.message || "We could not delete this provider record.");
      });
  };
  return <><RecordShell type="provider" record={record} title={value(data.name, "Provider record")} entityId={entityId} recordId={providerId} issues={issues} locations={locations} actions={canEdit ? <DetailActionsMenu actionLabel="Delete provider" onAction={() => setDeleteDialogOpen(true)} /> : null}>
    <section className="record-section identity-section"><SectionHeading eyebrow="Provider plan data" title="Provider profile" onEdit={edit("profile")} /><div className="identity-grid"><Info label="Provider type" value={data.provider_type} empty="Provider type not captured" /><Info label="First name" value={data.first_name || data.name?.split(" ")?.[0]} empty="First name not captured" /><Info label="Middle name" value={data.middle_name} empty="Middle name not captured" /><Info label="Last name" value={data.last_name || data.name?.split(" ")?.slice(-1)?.[0]} empty="Last name not captured" /><Info label="Gender" value={data.gender} empty="Gender not captured" /><Info label="Date of birth" value={data.date_of_birth || data.dob} empty="Date of birth not captured" /><Info label="NPI" value={data.npi} empty="NPI not captured" /><Info label="CAQH ID" value={data.caqh_id} empty="CAQH ID not captured" /></div></section>
    <section className="record-section"><SectionHeading eyebrow="Contact" title="Provider address" onEdit={edit("address")} /><p className="record-copy">{addressLabel(data.address || data.provider_address)}</p></section>
    <section className="record-section"><SectionHeading eyebrow="Care delivery" title="Locations" onEdit={canEdit && affiliations.length ? () => setLinkedLocationDialogOpen(true) : undefined} /><LocationCards locations={locations} /></section>
    <section className="record-section"><SectionHeading eyebrow="Practice network" title="Affiliated practices" onEdit={canEdit ? () => setAffiliationsDialogOpen(true) : undefined} /><AffiliatedPracticesTable affiliations={affiliations} entityId={entityId} /></section>
    <section className="record-section"><SectionHeading eyebrow="Credentials" title="Licenses" onEdit={edit("licenses")} /><LicenseTable licenses={licenses} /></section>
    <section className="record-section"><SectionHeading eyebrow="Expertise" title="Specialties" onEdit={edit("specialties")} /><SpecialtyCards specialties={specialties} /></section>
    <section className="record-section"><SectionHeading eyebrow="Training" title="Education" onEdit={edit("education")} /><EducationTimeline records={education} /></section>
    <section className="record-section"><SectionHeading eyebrow="Risk" title="Liability insurance" onEdit={edit("liability_insurance")} /><InsuranceCards policies={insurance} /></section>
    <section className="record-section"><SectionHeading eyebrow="Network" title="Payer enrollment" onEdit={edit("payer_enrollments")} /><PayerTable payers={payers} /></section>
  </RecordShell>{(saveMessage || saveError) && <p className={`record-operation-notice ${saveError ? "is-error" : ""}`} role={saveError ? "alert" : "status"}>{saveError || saveMessage}</p>}{editingSection && <SectionEditDialog type="provider" section={editingSection} record={record} onClose={() => setEditingSection(null)} onSave={saveSection} />}{linkedLocationDialogOpen && <ProviderPracticeLocationLinkDialog entityId={entityId} providerId={providerId} affiliations={affiliations} restrictToAffiliations onClose={() => setLinkedLocationDialogOpen(false)} onSave={savePracticeLocationLink} onUnlinkPractice={unlinkPractice} />}{affiliationsDialogOpen && <ProviderPracticeLocationLinkDialog entityId={entityId} providerId={providerId} affiliations={affiliations} onClose={() => setAffiliationsDialogOpen(false)} onSave={savePracticeLocationLink} onUnlinkPractice={unlinkPractice} />}{deleteDialogOpen && <ProviderDeleteDialog providerName={value(data.name, "this provider")} deleting={deleting} error={deleteError} onCancel={() => !deleting && setDeleteDialogOpen(false)} onDelete={deleteProviderRecord} />}</>;
}

function ProviderDeleteDialog({ providerName, deleting, error, onCancel, onDelete }) {
  return <div className="provider-delete-backdrop" role="presentation" onMouseDown={onCancel}>
    <section className="provider-delete-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-provider-title" onMouseDown={(event) => event.stopPropagation()}>
      <p className="eyebrow">Permanent action</p>
      <h2 id="delete-provider-title">Delete {providerName}?</h2>
      <p>This cannot be undone. The provider record, linked affiliations, issues, revisions, and identity mappings will be removed from this workspace.</p>
      {error && <p className="connection-error" role="alert">{error}</p>}
      <div className="provider-delete-actions">
        <button className="secondary-button" type="button" onClick={onCancel} disabled={deleting}>Cancel</button>
        <button className="primary-button danger-primary-button" type="button" onClick={onDelete} disabled={deleting}>{deleting ? "Deleting..." : "Delete provider"}</button>
      </div>
    </section>
  </div>;
}

function PracticeRecord() {
  const entityId = useEntity();
  const workspace = useWorkspace();
  const { groupId } = useParams();
  const request = useRequest(entityPath(entityId, `/groups/${encodeURIComponent(groupId)}`));
  const [editingSection, setEditingSection] = useState(null);
  const [optimisticRecord, setOptimisticRecord] = useState(null);
  const [saveMessage, setSaveMessage] = useState("");
  const [saveError, setSaveError] = useState("");
  const [providerLinksDialogOpen, setProviderLinksDialogOpen] = useState(false);
  const saveSequence = useRef(0);
  const record = optimisticRecord || request.data?.group || request.data;
  const issues = recordIssues(record);
  const canEdit = EDITABLE_RECORD_ROLES.has(workspace?.user?.role);
  useEffect(() => { if (record) document.title = `${value(record.legal_name || record.name, "Practice")} | Cozema Health`; }, [record]);
  useEffect(() => { setOptimisticRecord(null); setSaveMessage(""); setSaveError(""); setProviderLinksDialogOpen(false); }, [groupId]);
  if (request.loading) return <RecordLoading />;
  if (request.error) return <RecordError error={request.error} />;
  const edit = (section) => canEdit ? () => setEditingSection(section) : undefined;
  const saveSection = (fields) => {
    const previousRecord = record;
    const sequence = ++saveSequence.current;
    setOptimisticRecord(optimisticRecordUpdate("practice", previousRecord, fields));
    setEditingSection(null);
    setSaveError("");
    setSaveMessage("Changes saved locally. Syncing...");
    void fetchJson(entityPath(entityId, `/groups/${encodeURIComponent(groupId)}`), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fields }) })
      .then((savedRecord) => {
        if (sequence !== saveSequence.current) return;
        setOptimisticRecord(savedRecord);
        setSaveMessage("Changes saved.");
      })
      .catch((error) => {
        if (sequence !== saveSequence.current) return;
        setOptimisticRecord(previousRecord);
        setSaveMessage("");
        setSaveError(`${error.message || "We could not save your changes."} Your prior values have been restored.`);
      });
  };
  const saveProviderLocationLink = async (providerId, locationIds) => {
    setSaveError("");
    setSaveMessage("Saving provider location assignment...");
    try {
      const savedRecord = await fetchJson(entityPath(entityId, `/groups/${encodeURIComponent(groupId)}/providers/${encodeURIComponent(providerId)}`), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ location_ids: locationIds }),
      });
      setOptimisticRecord(savedRecord);
      setSaveMessage("Provider location assignment saved.");
    } catch (error) {
      setSaveMessage("");
      setSaveError(error.message || "We could not save this provider location assignment.");
      throw error;
    }
  };
  const unlinkProvider = async (providerId) => {
    setSaveError("");
    setSaveMessage("Unlinking provider...");
    try {
      const savedRecord = await fetchJson(entityPath(entityId, `/groups/${encodeURIComponent(groupId)}/providers/${encodeURIComponent(providerId)}`), { method: "DELETE" });
      setOptimisticRecord(savedRecord);
      setSaveMessage("Provider unlinked.");
    } catch (error) {
      setSaveMessage("");
      setSaveError(error.message || "We could not unlink this provider.");
      throw error;
    }
  };
  return <><RecordShell type="practice" record={record} title={value(record?.legal_name || record?.name, "Practice record")} entityId={entityId} recordId={groupId} issues={issues} locations={record.locations || []}>
    <section className="record-section identity-section"><SectionHeading eyebrow="Organization" title="Practice profile" onEdit={edit("profile")} /><div className="identity-grid"><Info label="Legal name" value={record.legal_name || record.name} empty="Legal name not captured" /><Info label="DBA" value={record.dba} empty="DBA not captured" /><Info label="Type 2 NPI" value={record.type_2_npi} empty="Type 2 NPI not captured" /><Info label="Tax ID number" value={record.tax_id_number || record.tax_id} empty="Tax ID number not captured" /></div></section>
    <section className="record-section"><SectionHeading eyebrow="Care delivery" title="Locations" onEdit={edit("locations")} /><LocationCards locations={record.locations || []} /></section>
    <section className="record-section"><SectionHeading eyebrow="Provider network" title="Linked providers" onEdit={canEdit ? () => setProviderLinksDialogOpen(true) : undefined} /><LinkedProvidersTable providers={record.providers || []} locations={record.locations || []} entityId={entityId} /></section>
    <section className="record-section"><SectionHeading eyebrow="Primary source" title="W-9 information" onEdit={edit("w9")} />{record.w_9 || record.w9 ? <div className="source-card"><strong>{value(record.w_9?.file_name || record.w9?.file_name, "W-9 source")}</strong></div> : <div className="empty-inline">No W-9 information has been captured yet.</div>}</section>
  </RecordShell>{(saveMessage || saveError) && <p className={`record-operation-notice ${saveError ? "is-error" : ""}`} role={saveError ? "alert" : "status"}>{saveError || saveMessage}</p>}{editingSection && <SectionEditDialog type="practice" section={editingSection} record={record} onClose={() => setEditingSection(null)} onSave={saveSection} />}{providerLinksDialogOpen && <ProviderLocationLinkDialog entityId={entityId} groupId={groupId} providers={record.providers || []} locations={record.locations || []} onClose={() => setProviderLinksDialogOpen(false)} onSave={saveProviderLocationLink} onUnlinkProvider={unlinkProvider} />}</>;
}

function RecordLoading() { return <main className="record-state-page"><div className="record-loading"><span className="spinner" />Loading record...</div></main>; }
function RecordError({ error }) { return <main className="record-state-page"><section className="record-error"><p className="eyebrow">Record unavailable</p><h1>We could not load this record.</h1><p className="lede">{error.message}</p></section></main>; }

function RecordShell({ type, record, title, entityId, recordId, issues, locations = [], actions, children }) {
  const navigate = useNavigate();
  const activityPath = href(`/activity/view?record_id=${encodeURIComponent(recordId)}&record_type=${type}`, entityId);
  return <div className="record-page"><header className="record-topbar"><Link className="brand" to={href("/", entityId)}><span className="brand-mark">C</span><span><BrandWordmark /></span></Link></header><main className="record-content"><section className="record-hero"><div className="record-hero-heading"><div><p className="eyebrow">{type === "practice" ? "Practice / group" : "Provider record"}</p><h1>{title}</h1><p className="lede">{type === "practice" ? "Organization credentialing profile" : "Provider credentialing profile"}</p></div>{actions}</div><nav className="record-tabs"><a href="#profile">{type === "practice" ? "Practice profile" : "Provider profile"}</a><a href="#issues">Open issues</a><a href={activityPath}>Revision history</a></nav></section><div className="record-layout"><div className="record-sections" id="profile">{children}</div><aside className="record-aside"><section className="aside-card"><p className="eyebrow">At a glance</p><Info label="Open issues" value={issues.length} /><Info label="Locations" value={locations.length} /><Link className="aside-link" to={activityPath}>View revision history</Link></section><section className="aside-card" id="issues"><p className="eyebrow">Record integrity</p><h2>Open issues</h2><div className="record-issues">{issues.length ? issues.map((issue, index) => <button type="button" key={`${issue.id || issueLabel(issue)}-${index}`} className="record-issue" onClick={() => navigate(href(`/${type === "practice" ? "practices" : "providers"}/${encodeURIComponent(recordId)}/issues/${encodeURIComponent(issue.id)}/view`, entityId))}><strong>{issueLabel(issue)}</strong><span className="issue-tags"><span className={`severity-tag severity-${issue.severity}`}>{value(issue.severity, "high")}</span><span className={`issue-type-tag issue-type-${issue.type}`}>{issueTypeLabel(issue.type)}</span></span><b>View issue</b></button>) : <div className="empty-inline">No open issues.</div>}</div></section></aside></div></main></div>;
}

const SECTION_EDIT_CONFIG = {
  provider: {
    profile: { title: "Provider profile", fields: [["provider_type", "Provider type"], ["first_name", "First name"], ["middle_name", "Middle name"], ["last_name", "Last name"], ["gender", "Gender"], ["date_of_birth", "Date of birth"], ["npi", "NPI"], ["caqh_id", "CAQH ID"]] },
    address: { title: "Provider address", fields: [["address", "Address"]] },
    licenses: { title: "Licenses", collection: "licenses", fields: [["type", "License type"], ["license_number", "License number"], ["state", "State"], ["issue_date", "Issue date"], ["expiration_date", "Expiration date"]] },
    specialties: { title: "Specialties", collection: "specialties", fields: [["name", "Specialty"], ["board_certified", "Board certified", "boolean"], ["board", "Certifying board"], ["expiration_date", "Expiration date"]] },
    education: { title: "Education", collection: "education", fields: [["education_type", "Education type"], ["institution_name", "Institution"], ["degree", "Degree"], ["specialty", "Specialty"], ["start_date", "Start date"], ["end_date", "End date"]] },
    liability_insurance: { title: "Liability insurance", collection: "liability_insurance", fields: [["insurance_type", "Insurance type"], ["carrier_name", "Carrier"], ["policy_number", "Policy number"], ["effective_date", "Effective date"], ["expiration_date", "Expiration date"], ["claim_amount", "Claim amount"], ["aggregate_amount", "Aggregate amount"]] },
    payer_enrollments: { title: "Payer enrollment", collection: "payer_enrollments", fields: [["payer_name", "Payer"], ["group_name", "Practice"], ["location_names", "Locations", "list"], ["enrollment_status", "Enrollment status"]] },
  },
  practice: {
    profile: { title: "Practice profile", fields: [["legal_name", "Legal name"], ["dba", "DBA"], ["type_2_npi", "Type 2 NPI"], ["tax_id", "Tax ID number"]] },
    locations: { title: "Locations", collection: "locations", fields: [["display_name", "Location name"], ["type", "Location type"], ["address", "Address"], ["phone", "Phone"], ["email", "Email"], ["practice_hours", "Practice hours"], ["faxes", "Fax"]] },
    w9: { title: "W-9 information", w9: true, fields: [["file_name", "W-9 reference"]] },
  },
};

function SectionEditDialog({ type, section, record, onClose, onSave, onLinkExistingLocation }) {
  const config = SECTION_EDIT_CONFIG[type][section];
  const source = type === "provider" ? providerData(record) : record;
  const initialItems = config.collection ? (record?.[config.collection] || []) : [];
  const valueFor = (item, key, inputType) => {
    const rawValue = key === "address" ? item?.address || item?.provider_address : key === "tax_id" ? item?.tax_id || item?.tax_id_number : item?.[key];
    if (inputType === "boolean") {
      const normalized = String(rawValue || "").trim().toLowerCase();
      return rawValue === true || ["true", "yes", "y"].includes(normalized) ? "true" : "false";
    }
    if (inputType === "list") return Array.isArray(rawValue) ? rawValue.join(", ") : rawValue || "";
    return rawValue && typeof rawValue === "object" ? addressLabel(rawValue) : rawValue || "";
  };
  const initialValues = Object.fromEntries(config.fields.map(([key, , inputType]) => [key, valueFor(config.w9 ? source.w9 || source.w_9 || {} : source, key, inputType)]));
  const initialCollection = initialItems.map((item) => {
    const preservedFields = Object.fromEntries(
      Object.entries(item).filter(
        ([key, itemValue]) => key !== "id" && (itemValue === null || typeof itemValue !== "object" || Array.isArray(itemValue)),
      ),
    );
    return {
      ...preservedFields,
      ...(item.id ? { id: item.id } : {}),
      ...Object.fromEntries(config.fields.map(([key, , inputType]) => [key, valueFor(item, key, inputType)])),
    };
  });
  const [values, setValues] = useState(initialValues);
  const [items, setItems] = useState(initialCollection);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const updateItem = (index, key, nextValue) => setItems((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: nextValue } : item));
  const addItem = () => setItems((current) => [...current, Object.fromEntries(config.fields.map(([key, , inputType]) => [key, inputType === "boolean" ? "false" : ""]))]);
  const payload = () => {
    if (config.collection) {
      const configuredKeys = new Set(config.fields.map(([key]) => key));
      const collection = items.map((item) => {
        const preservedFields = Object.fromEntries(
          Object.entries(item).filter(([key]) => key !== "id" && !configuredKeys.has(key)),
        );
        const configuredValues = Object.fromEntries(
          config.fields.map(([key, , inputType]) => [
            key,
            inputType === "boolean"
              ? item[key] === "true"
              : inputType === "list"
                ? item[key].split(",").map((part) => part.trim()).filter(Boolean)
                : item[key],
          ]),
        );
        return { ...preservedFields, ...(item.id ? { id: item.id } : {}), ...configuredValues };
      });
      return { [config.collection]: collection };
    }
    if (config.w9) return { w9: { file_name: values.file_name } };
    return values;
  };
  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      onSave(payload());
    } catch (requestError) {
      setError(requestError.message);
      setSaving(false);
    }
  };
  return <div className="record-edit-backdrop" role="presentation"><form className="record-edit-dialog" onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="edit-record-title"><div className="record-edit-header"><div><p className="eyebrow">Manual correction</p><h2 id="edit-record-title">Edit {config.title}</h2><p>Changes are recorded in revision history as a manual update.</p></div><button className="dialog-close" type="button" onClick={onClose} aria-label="Close edit dialog">×</button></div>{config.collection ? <div className="record-edit-collection">{items.map((item, index) => <fieldset key={index}><legend>{config.title} {index + 1}</legend><button className="text-button danger-button collection-remove" type="button" onClick={() => setItems((current) => current.filter((_, itemIndex) => itemIndex !== index))}>Remove</button><div className="record-edit-fields">{config.fields.map(([key, label, inputType]) => <SectionEditField key={key} label={label} type={inputType} value={item[key]} onChange={(nextValue) => updateItem(index, key, nextValue)} />)}</div></fieldset>)}<div className="record-edit-collection-actions"><button className="secondary-button collection-add" type="button" onClick={addItem}>Add entry <span>+</span></button>{onLinkExistingLocation && <button className="secondary-button" type="button" onClick={onLinkExistingLocation}>Link existing location</button>}</div></div> : <div className="record-edit-fields">{config.fields.map(([key, label, inputType]) => <SectionEditField key={key} label={label} type={inputType} value={values[key]} onChange={(nextValue) => setValues((current) => ({ ...current, [key]: nextValue }))} />)}</div>}{error && <p className="record-edit-error" role="alert">{error}</p>}<div className="record-edit-actions"><button className="secondary-button" type="button" onClick={onClose} disabled={saving}>Cancel</button><button className="primary-button" type="submit" disabled={saving}>{saving ? "Saving..." : "Save changes"}</button></div></form></div>;
}

function SectionEditField({ label, type, value: fieldValue, onChange }) { return <label>{label}{type === "boolean" ? <select value={fieldValue} onChange={(event) => onChange(event.target.value)}><option value="true">Yes</option><option value="false">No</option></select> : <input value={fieldValue} onChange={(event) => onChange(event.target.value)} inputMode={label.includes("NPI") ? "numeric" : undefined} />}</label>; }

function ProviderLocationLinkDialog({ entityId, groupId, providers: linkedProviders, locations, onClose, onSave, onUnlinkProvider }) {
  const providersRequest = useRequest(entityPath(entityId, "/providers?limit=500"));
  const [providerId, setProviderId] = useState("");
  const [locationIds, setLocationIds] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const linkedProvidersById = new Map(linkedProviders.map((provider) => [provider.id, provider]));
  const providers = [...(providersRequest.data?.providers || [])].sort((left, right) => recordName(left).localeCompare(recordName(right)));
  const selectedProvider = providers.find((provider) => provider.id === providerId);
  const isExistingLink = linkedProvidersById.has(providerId);
  const selectProvider = (nextProviderId) => {
    setProviderId(nextProviderId);
    setLocationIds(linkedProvidersById.get(nextProviderId)?.location_ids || []);
    setError("");
  };
  const toggleLocation = (locationId) => {
    setLocationIds((current) => current.includes(locationId) ? current.filter((id) => id !== locationId) : [...current, locationId]);
    setError("");
  };
  const submit = async (event) => {
    event.preventDefault();
    if (!providerId) {
      setError("Select a provider to link.");
      return;
    }
    if (!locationIds.length) {
      setError("Select at least one practice location.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onSave(providerId, locationIds);
      onClose();
    } catch (requestError) {
      setError(requestError.message || "We could not save this provider location assignment.");
      setSaving(false);
    }
  };
  const unlinkProvider = async () => {
    if (!providerId || !isExistingLink) return;
    setSaving(true);
    setError("");
    try {
      await onUnlinkProvider(providerId);
      onClose();
    } catch (requestError) {
      setError(requestError.message || "We could not unlink this provider.");
      setSaving(false);
    }
  };
  return <div className="record-edit-backdrop" role="presentation">
    <form className="record-edit-dialog provider-location-link-dialog" onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="link-provider-title">
      <div className="record-edit-header"><div><p className="eyebrow">Provider network</p><h2 id="link-provider-title">Manage linked providers</h2><p>Choose a provider and the practice locations where they provide care. Changes are recorded in revision history.</p></div><button className="dialog-close" type="button" onClick={onClose} disabled={saving} aria-label="Close linked providers dialog">×</button></div>
      {!locations.length ? <p className="record-edit-error" role="alert">Add a practice location before linking a provider.</p> : <>
        <label className="relationship-location-select">Provider<select value={providerId} onChange={(event) => selectProvider(event.target.value)} disabled={providersRequest.loading || saving}><option value="">Select a provider</option>{providers.map((provider) => <option key={provider.id} value={provider.id}>{recordName(provider)}{linkedProvidersById.has(provider.id) ? " (linked)" : ""}</option>)}</select></label>
        {providersRequest.loading && <InlineLoading text="Loading providers..." />}
        {providersRequest.error && <p className="record-edit-error" role="alert">{providersRequest.error.message}</p>}
        {selectedProvider && <p className="relationship-location-selected">{recordName(selectedProvider)}{value(providerData(selectedProvider).npi, "NPI not captured") !== "NPI not captured" ? ` · NPI ${providerData(selectedProvider).npi}` : ""}</p>}
        <fieldset className="provider-location-checkboxes"><legend>Practice locations</legend>{locations.map((location) => <label key={location.id}><input type="checkbox" checked={locationIds.includes(location.id)} onChange={() => toggleLocation(location.id)} disabled={!providerId || saving} /><span><strong>{locationLabel(location)}</strong><small>{addressLabel(location.address || location)}</small></span></label>)}</fieldset>
        {isExistingLink && <p className="relationship-location-help">Uncheck a location and save to unlink only that location from this provider.</p>}
      </>}
      {error && <p className="record-edit-error" role="alert">{error}</p>}
      <div className="record-edit-actions">{isExistingLink && <button className="text-button danger-button relationship-unlink-button" type="button" onClick={unlinkProvider} disabled={saving}>Unlink provider</button>}<button className="secondary-button" type="button" onClick={onClose} disabled={saving}>Cancel</button><button className="primary-button" type="submit" disabled={saving || !providerId || !locationIds.length || !locations.length || providersRequest.loading || Boolean(providersRequest.error)}>{saving ? "Saving..." : isExistingLink ? "Save location assignment" : "Link provider"}</button></div>
    </form>
  </div>;
}

function ProviderPracticeLocationLinkDialog({ entityId, providerId, affiliations, restrictToAffiliations = false, onClose, onSave, onUnlinkPractice }) {
  const groupsRequest = useRequest(restrictToAffiliations ? null : entityPath(entityId, "/groups?limit=500"));
  const [groupId, setGroupId] = useState("");
  const groupRequest = useRequest(groupId ? entityPath(entityId, `/groups/${encodeURIComponent(groupId)}`) : null);
  const [locationIds, setLocationIds] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const groups = (restrictToAffiliations
    ? affiliations.map((affiliation) => affiliation.group).filter(Boolean)
    : groupsRequest.data?.groups || []
  ).slice().sort((left, right) => value(left.legal_name || left.name).localeCompare(value(right.legal_name || right.name)));
  const selectedGroup = groupRequest.data?.group || groupRequest.data;
  const locations = selectedGroup?.locations || [];
  const existingAffiliation = affiliations.find((affiliation) => affiliation.group?.id === groupId);
  const selectGroup = (nextGroupId) => {
    setGroupId(nextGroupId);
    setLocationIds(affiliations.find((affiliation) => affiliation.group?.id === nextGroupId)?.location_ids || []);
    setError("");
  };
  const toggleLocation = (locationId) => {
    setLocationIds((current) => current.includes(locationId) ? current.filter((id) => id !== locationId) : [...current, locationId]);
    setError("");
  };
  const submit = async (event) => {
    event.preventDefault();
    if (!groupId) {
      setError("Select a practice to link.");
      return;
    }
    if (!locationIds.length) {
      setError("Select at least one practice location.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onSave(groupId, locationIds);
      onClose();
    } catch (requestError) {
      setError(requestError.message || "We could not save this practice location assignment.");
      setSaving(false);
    }
  };
  const unlinkPractice = async () => {
    if (!groupId || !existingAffiliation) return;
    setSaving(true);
    setError("");
    try {
      await onUnlinkPractice(groupId);
      onClose();
    } catch (requestError) {
      setError(requestError.message || "We could not unlink this practice.");
      setSaving(false);
    }
  };
  return <div className="record-edit-backdrop" role="presentation">
    <form className="record-edit-dialog provider-location-link-dialog" onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="link-practice-title">
      <div className="record-edit-header"><div><p className="eyebrow">Practice network</p><h2 id="link-practice-title">{restrictToAffiliations ? "Link an affiliated-practice location" : "Manage affiliated practices"}</h2><p>{restrictToAffiliations ? "Choose a location that already belongs to one of this provider's affiliated practices." : "Choose a practice and the locations where this provider provides care. Changes are recorded in revision history."}</p></div><button className="dialog-close" type="button" onClick={onClose} disabled={saving} aria-label="Close affiliated practices dialog">×</button></div>
      <label className="relationship-location-select">Practice<select value={groupId} onChange={(event) => selectGroup(event.target.value)} disabled={(!restrictToAffiliations && groupsRequest.loading) || saving}><option value="">Select a practice</option>{groups.map((group) => <option key={group.id} value={group.id}>{value(group.legal_name || group.name, "Practice name not captured")}{!restrictToAffiliations && affiliations.some((affiliation) => affiliation.group?.id === group.id) ? " (linked)" : ""}</option>)}</select></label>
      {!restrictToAffiliations && groupsRequest.loading && <InlineLoading text="Loading practices..." />}
      {!restrictToAffiliations && groupsRequest.error && <p className="record-edit-error" role="alert">{groupsRequest.error.message}</p>}
      {groupId && groupRequest.loading && <InlineLoading text="Loading practice locations..." />}
      {groupRequest.error && <p className="record-edit-error" role="alert">{groupRequest.error.message}</p>}
      {selectedGroup && <p className="relationship-location-selected">{value(selectedGroup.legal_name || selectedGroup.name, "Practice name not captured")}{selectedGroup.type_2_npi ? ` · Type 2 NPI ${selectedGroup.type_2_npi}` : ""}</p>}
      {groupId && !groupRequest.loading && !groupRequest.error && !locations.length && <p className="record-edit-error" role="alert">Add a practice location before linking this provider.</p>}
      {locations.length > 0 && <fieldset className="provider-location-checkboxes"><legend>Practice locations</legend>{locations.map((location) => <label key={location.id}><input type="checkbox" checked={locationIds.includes(location.id)} onChange={() => toggleLocation(location.id)} disabled={saving} /><span><strong>{locationLabel(location)}</strong><small>{addressLabel(location.address || location)}</small></span></label>)}</fieldset>}
      {existingAffiliation && <p className="relationship-location-help">Uncheck a location and save to unlink only that location from this provider.</p>}
      {error && <p className="record-edit-error" role="alert">{error}</p>}
      <div className="record-edit-actions">{existingAffiliation && <button className="text-button danger-button relationship-unlink-button" type="button" onClick={unlinkPractice} disabled={saving}>Unlink practice</button>}<button className="secondary-button" type="button" onClick={onClose} disabled={saving}>Cancel</button><button className="primary-button" type="submit" disabled={saving || !groupId || !locationIds.length || !locations.length || groupRequest.loading || Boolean((!restrictToAffiliations && groupsRequest.error) || groupRequest.error)}>{saving ? "Saving..." : restrictToAffiliations ? "Link location" : existingAffiliation ? "Save location assignment" : "Link practice"}</button></div>
    </form>
  </div>;
}

function SectionHeading({ eyebrow, title, onEdit }) { return <div className="record-section-heading"><div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2></div>{onEdit && <button className="section-edit-button" type="button" onClick={onEdit} aria-label={`Edit ${title}`} title={`Edit ${title}`}>✎</button>}</div>; }
function Info({ label, value: item, empty }) { return <div className="info-row"><span>{label}</span><strong>{value(item, empty || `${label} not captured`)}</strong></div>; }
function LocationCards({ locations }) { return locations?.length ? <div className="location-cards">{locations.map((location, index) => <article className="location-card" key={index}><span>{value(location.type, "Unknown")}</span><strong>{locationLabel(location)}</strong><p>{addressLabel(location.address || location)}</p>{location.phone && <p>{location.phone}</p>}{location.email && <p>{location.email}</p>}</article>)}</div> : <div className="empty-inline">No location information has been captured yet.</div>; }
function LinkedProvidersTable({ providers, locations, entityId }) { const locationsById = new Map(locations.map((location) => [location.id, location])); return <div className="detail-table-wrap"><table className="detail-table"><thead><tr><th>Provider</th><th>NPI</th><th>Provider type</th><th>Locations</th></tr></thead><tbody>{providers.length ? providers.map((provider) => <tr key={provider.id}><td><Link className="relationship-record-link" to={href(`/providers/${encodeURIComponent(provider.id)}/view`, entityId)}>{value(provider.name, "Provider name not captured")}</Link></td><td>{value(provider.npi, "NPI not captured")}</td><td>{value(provider.provider_type, "Provider type not captured")}</td><td>{value((provider.location_ids || []).map((locationId) => locationLabel(locationsById.get(locationId))).filter((label) => label !== "Location not captured"), "No locations linked")}</td></tr>) : <EmptyRow columns="4">No providers are linked to this practice yet.</EmptyRow>}</tbody></table></div>; }
function AffiliatedPracticesTable({ affiliations, entityId }) { return <div className="detail-table-wrap"><table className="detail-table"><thead><tr><th>Practice</th><th>Type 2 NPI</th><th>Locations</th></tr></thead><tbody>{affiliations.length ? affiliations.map((affiliation) => <tr key={affiliation.id || affiliation.group.id}><td><Link className="relationship-record-link" to={href(`/practices/${encodeURIComponent(affiliation.group.id)}/view`, entityId)}>{value(affiliation.group.legal_name || affiliation.group.name, "Practice name not captured")}</Link></td><td>{value(affiliation.group.type_2_npi, "Type 2 NPI not captured")}</td><td>{value(affiliation.locations?.map(locationLabel), "Location not captured")}</td></tr>) : <EmptyRow columns="3">This provider is not linked to a practice yet.</EmptyRow>}</tbody></table></div>; }
function LicenseTable({ licenses }) { return <div className="provider-table-wrap"><table className="provider-table"><thead><tr><th>Type</th><th>License #</th><th>State</th><th>Issued</th><th>Expires</th></tr></thead><tbody>{licenses?.length ? licenses.map((license, index) => <tr key={index} className={expirationState(license.expiration_date || license.expires) ? "is-expired" : ""}><td>{value(license.type, "License type not captured")}</td><td>{value(license.license_number || license.number, "License number not captured")}</td><td>{value(license.state, "State not captured")}</td><td>{dateLabel(license.issue_date || license.issued)}</td><td className={expirationState(license.expiration_date || license.expires) ? "expiration-date is-expired" : ""}>{dateLabel(license.expiration_date || license.expires)}</td></tr>) : <EmptyRow columns="5">No license information has been captured yet.</EmptyRow>}</tbody></table></div>; }
function SpecialtyCards({ specialties }) { return specialties?.length ? <div className="specialty-list">{specialties.map((specialty, index) => <article className="specialty-card" key={index}><strong>{value(specialty.name || specialty.specialty)}</strong><p>{specialty.board_certified ? "Board certified" : "Board certification not captured"}{specialty.board ? ` · ${specialty.board}` : ""}</p>{specialty.expiration_date && <p className={expirationState(specialty.expiration_date) ? "expiration-date is-expired" : ""}>Expires {dateLabel(specialty.expiration_date)}</p>}</article>)}</div> : <div className="empty-inline">No specialty information has been captured yet.</div>; }
function EducationTimeline({ records }) { return records?.length ? <div className="education-timeline">{records.map((education, index) => <article key={index}><span /><div className="education-detail"><p>{value(education.education_type || education.type, "Education")}</p><strong>{value(education.institution_name || education.institution, "Institution not captured")}</strong><small>{[education.degree, education.specialty].filter(Boolean).join(" · ") || "Degree not captured"}</small></div></article>)}</div> : <div className="empty-inline">No education information has been captured yet.</div>; }
function InsuranceCards({ policies }) { return policies?.length ? <div className="insurance-cards">{policies.map((policy, index) => { const expired = expirationState(policy.expiration_date || policy.expires); return <article className={`insurance-card ${expired ? "is-expired" : ""}`} key={index}><span>{value(policy.insurance_type || policy.type, "Liability insurance")}</span><strong>{value(policy.carrier_name || policy.carrier, "Carrier not captured")}</strong><Info label="Policy #" value={policy.policy_number} empty="Policy number not captured" /><Info label="Coverage period" value={[dateLabel(policy.effective_date, "Effective date not captured"), dateLabel(policy.expiration_date || policy.expires, "Expiration date not captured")].join(" – ")} />{expired && <span className="expired-badge">Expired</span>}</article>; })}</div> : <div className="empty-inline">No liability coverage information has been captured yet.</div>; }
function PayerTable({ payers }) { return <div className="provider-table-wrap"><table className="provider-table"><thead><tr><th>Payer</th><th>Practice</th><th>Locations</th><th>Enrollment status</th></tr></thead><tbody>{payers?.length ? payers.map((payer, index) => <tr key={index}><td>{value(payer.payer_name || payer.name || payer.payer)}</td><td>{value(payer.group_name || payer.practice_name || payer.practice)}</td><td>{value(Array.isArray(payer.location_names || payer.locations) ? (payer.location_names || payer.locations).map((location) => typeof location === "string" ? location : locationLabel(location)) : payer.location_names || payer.locations, "Location not captured")}</td><td><span className="activity-category">{value(payer.enrollment_status || payer.status, "Unknown")}</span></td></tr>) : <EmptyRow columns="4">No payer enrollment information has been captured yet.</EmptyRow>}</tbody></table></div>; }

function capitalizeLabel(label) {
  const text = String(label || "");
  return text ? `${text.charAt(0).toUpperCase()}${text.slice(1)}` : text;
}

function issueDetailLabel(issue) {
  if (!["expired", "expiring"].includes(issue?.type) || !issue?.expiration_date) {
    return issueLabel(issue);
  }

  return `${value(issue.credential_label, "Credential")} Expires on ${dateLabel(issue.expiration_date)}`;
}

function IssueDetail({ type }) {
  const entityId = useEntity();
  const workspace = useWorkspace();
  const navigate = useNavigate();
  const { providerId, groupId, issueId } = useParams();
  const recordId = type === "practice" ? groupId : providerId;
  const recordPath = href(`/${type === "practice" ? "practices" : "providers"}/${encodeURIComponent(recordId)}/view`, entityId);
  const dismissal = useIssueDismissal(entityId, { onDismissed: () => navigate(recordPath, { replace: true }) });
  const request = useRequest(entityPath(entityId, `/${type === "practice" ? "groups" : "providers"}/${encodeURIComponent(recordId)}/issues/${encodeURIComponent(issueId)}`));
  const issue = request.data?.issue || request.data;
  const record = request.data?.record || request.data?.[type === "practice" ? "group" : "provider"] || issue?.record || {};
  useEffect(() => { if (issue) document.title = `${issueTypeLabel(issue.type)} | Cozema Health`; }, [issue]);
  if (request.loading) return <RecordLoading />;
  if (request.error) return <RecordError error={request.error} />;
  const title = issueDetailLabel(issue);
  const recordTitle = type === "practice" ? value(record.legal_name || record.name, "Practice") : recordName(record, "Provider");
  const dismissableIssue = { ...issue, record_type: type, recordId };
  const canManageIssues = EDITABLE_RECORD_ROLES.has(workspace?.user?.role);
  return <div className="record-shell">
    <header className="record-topbar">
      <Link className="brand record-brand" to={href("/", entityId)}>
        <span className="brand-mark">C</span><span><BrandWordmark /></span>
      </Link>
      <div className="record-actions">
        <Link className="back-link" to={recordPath}>Back to {type === "practice" ? "practice" : "provider"}</Link>
      </div>
    </header>
    <main className="issue-page">
      <nav className="breadcrumbs" aria-label="Breadcrumb">
        <Link to={href("/", entityId)}>Dashboard</Link><span>/</span><Link to={recordPath}>{recordTitle}</Link><span>/</span><strong>Issue</strong>
      </nav>
      <section className="issue-hero">
        <div className="issue-hero-action-row">
          <p className="eyebrow">Credentialing issue</p>
          {canManageIssues && <DetailActionsMenu actionLabel="Dismiss issue" onAction={() => dismissal.requestDismissal(dismissableIssue)} />}
        </div>
        <div className="issue-hero-heading">
          <h1>{title}</h1>
        </div>
        <div className="issue-hero-tags">
          <span className={`severity-tag severity-${issue.severity}`}>{capitalizeLabel(value(issue.severity, "high"))}</span>
          <span className={`issue-type-tag issue-type-${issue.type}`}>{capitalizeLabel(issueTypeLabel(issue.type))}</span>
        </div>
        <p className="lede">{issue.description || (issue.type === "missing_data" ? `${fieldLabel(issue.field_path || issue.affected_fields?.[0])} requires attention because it has not been captured.` : title)}</p>
      </section>
      <section className="issue-summary">
        <div>
          <span>{type === "practice" ? "Practice" : "Provider"}</span>
          <Link className="issue-summary-record-link" to={recordPath}>{recordTitle}</Link>
        </div>
        <Info label={type === "practice" ? "Type 2 NPI" : "NPI"} value={type === "provider" ? providerData(record).npi : record.type_2_npi} empty="Not Captured" />
        <Info label="Issue type" value={capitalizeLabel(issueTypeLabel(issue.type))} />
        <Info label="Severity" value={capitalizeLabel(issue.severity)} />
      </section>
    </main>
    <IssueDismissalDialog
      issue={dismissal.pendingIssue}
      error={dismissal.error}
      saving={dismissal.saving}
      onCancel={dismissal.cancelDismissal}
      onConfirm={dismissal.confirmDismissal}
    />
  </div>;
}

export function App() {
  return <Routes>
    <Route path="/" element={<Dashboard />} />
    <Route path="/providers/view" element={<Directory type="provider" />} />
    <Route path="/practices/view" element={<Directory type="practice" />} />
    <Route path="/expirations/view" element={<Expirations />} />
    <Route path="/issues/view" element={<Issues />} />
    <Route path="/reports/view" element={<Reports />} />
    <Route path="/activity/view" element={<Activity />} />
    <Route path="/users/view" element={<Users />} />
    <Route path="/tasks/view" element={<Activity />} />
    <Route path="/enrollment/view" element={<Workspace section="enrollment" />} />
    <Route path="/data-connections/view" element={<DataConnections />} />
    <Route path="/settings/view" element={<DataConnections />} />
    <Route path="/providers/:providerId/issues/:issueId/view" element={<IssueDetail type="provider" />} />
    <Route path="/practices/:groupId/issues/:issueId/view" element={<IssueDetail type="practice" />} />
    <Route path="/providers/:providerId/view" element={<ProviderRecord />} />
    <Route path="/practices/:groupId/view" element={<PracticeRecord />} />
    <Route path="*" element={<Dashboard />} />
  </Routes>;
}
