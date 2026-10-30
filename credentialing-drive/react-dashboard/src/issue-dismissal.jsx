import { useState } from "react";
import { entityPath, fetchJson } from "./api.js";
import { issueLabel } from "./utils.js";

function issueTarget(issue) {
  const recordType = issue?.record_type || issue?.recordType || (issue?.group_id ? "practice" : "provider");
  const recordId = issue?.recordId || issue?.provider_id || issue?.group_id || issue?.record?.id;
  return { recordType, recordId, issueId: issue?.id };
}

export function TrashIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></svg>;
}

export function IssueDismissButton({ issue, onDismiss, className = "" }) {
  return <button
    className={`issue-dismiss-button ${className}`.trim()}
    type="button"
    onClick={(event) => {
      event.preventDefault();
      event.stopPropagation();
      onDismiss(issue);
    }}
    onKeyDown={(event) => event.stopPropagation()}
    aria-label={`Dismiss ${issueLabel(issue)}`}
    title="Dismiss issue"
  ><TrashIcon /></button>;
}

export function IssueDismissalDialog({ issue, error, onCancel, onConfirm, saving = false }) {
  if (!issue) return null;
  return <div
    className="provider-delete-backdrop"
    role="presentation"
    onMouseDown={() => !saving && onCancel()}
  >
    <section
      className="provider-delete-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="dismiss-issue-title"
      onMouseDown={(event) => event.stopPropagation()}
    >
      <h2 id="dismiss-issue-title">Dismiss this issue?</h2>
      <p><strong>{issueLabel(issue)}</strong> will be hidden from open-issue counts, lists, reports, and expiration follow-up. The underlying credentialing data and audit trail remain unchanged.</p>
      {error && <p className="connection-error" role="alert">{error}</p>}
      <div className="provider-delete-actions">
        <button className="secondary-button" type="button" onClick={onCancel} disabled={saving}>Cancel</button>
        <button className="primary-button danger-primary-button" type="button" onClick={onConfirm} disabled={saving}>{saving ? "Dismissing..." : "Dismiss issue"}</button>
      </div>
    </section>
  </div>;
}

export function useIssueDismissal(entityId, { onDismissed } = {}) {
  const [pendingIssue, setPendingIssue] = useState(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);

  const requestDismissal = (issue) => {
    setError("");
    setMessage("");
    setPendingIssue(issue);
  };

  const cancelDismissal = () => {
    if (!saving) setPendingIssue(null);
  };

  const confirmDismissal = async () => {
    if (!pendingIssue) return;
    const { recordType, recordId, issueId } = issueTarget(pendingIssue);
    if (!recordId || !issueId) {
      setError("This issue is missing the record information needed to dismiss it.");
      return;
    }

    setSaving(true);
    setError("");
    const collection = recordType === "practice" ? "groups" : "providers";
    try {
      await fetchJson(entityPath(entityId, `/${collection}/${encodeURIComponent(recordId)}/issues/${encodeURIComponent(issueId)}/remove`), { method: "POST" });
      const dismissedIssue = pendingIssue;
      setPendingIssue(null);
      setMessage("Issue dismissed.");
      onDismissed?.(dismissedIssue);
    } catch (requestError) {
      setError(`${requestError.message || "We could not dismiss this issue."} The issue remains open.`);
    } finally {
      setSaving(false);
    }
  };

  return { pendingIssue, error, message, saving, requestDismissal, cancelDismissal, confirmDismissal };
}
