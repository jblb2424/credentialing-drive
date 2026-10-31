import { issueTypeLabel, value } from "./utils.js";

export function issueRowClassName(baseClass, issue) {
  return `${baseClass}${issue?.type === "expired" ? " issue-row--expired" : ""}`;
}

export function IssueStatusTags({ issue }) {
  return <>
    <span className={`severity-tag severity-${issue.severity}`}>{value(issue.severity, "high")}</span>
    <span className={`issue-type-tag issue-type-${issue.type}`}>{issueTypeLabel(issue.type)}</span>
  </>;
}

export function ExpirationStatusTag({ record }) {
  const expired = record.type === "expired";
  const days = expired ? record.days_past_expiration : record.days_until_expiration;
  return <span className={`severity-tag status-${record.type}`}>{expired ? `${days} days past due` : `${days} days remaining`}</span>;
}
