from datetime import date, datetime

EXPIRING_WINDOW_DAYS = 90


def parse_expiration_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None

    normalized_value = value.strip()
    for date_format in (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%m/%d/%y",
        "%B %d, %Y",
        "%b %d, %Y",
    ):
        try:
            return datetime.strptime(normalized_value, date_format).date()
        except ValueError:
            continue
    return None


def revision_field_changes(value, prefix=""):
    if not isinstance(value, dict):
        return
    if "current" in value and "previous" in value:
        yield prefix, value
        return
    for field_name, nested_value in value.items():
        field_path = f"{prefix}.{field_name}" if prefix else field_name
        yield from revision_field_changes(nested_value, field_path)


def build_issue(issue_id, issue_type, severity, affected_fields, **details):
    return {
        "id": issue_id,
        "type": issue_type,
        "severity": severity,
        "affected_fields": affected_fields,
        **details,
    }


def discrepancy_issues(provider_ref):
    issues = []
    for revision_snapshot in provider_ref.collection("revisions").stream():
        revision = revision_snapshot.to_dict() or {}
        for field_path, change in revision_field_changes(revision.get("changes") or {}):
            if change["current"] == change["previous"]:
                continue
            severity = "critical" if field_path == "provider.npi" else "high"
            issues.append(
                build_issue(
                    f"discrepancy-{revision_snapshot.id}-{field_path}",
                    "discrepancy",
                    severity,
                    [field_path],
                    previous_value=change["previous"],
                    current_value=change["current"],
                    revision_id=revision_snapshot.id,
                    file_name=revision.get("file_name"),
                    drive_file_id=revision.get("drive_file_id"),
                )
            )
    return issues


def expiration_issues(provider):
    issues = []
    today = date.today()
    for raw_value in provider.get("expiration_dates") or []:
        expiration_date = parse_expiration_date(raw_value)
        if not expiration_date:
            continue
        days_until_expiration = (expiration_date - today).days
        if days_until_expiration < 0:
            issues.append(
                build_issue(
                    f"expired-{expiration_date.isoformat()}",
                    "expired",
                    "high",
                    ["expiration_dates"],
                    expiration_date=expiration_date.isoformat(),
                    days_past_expiration=abs(days_until_expiration),
                )
            )
        elif days_until_expiration <= EXPIRING_WINDOW_DAYS:
            issues.append(
                build_issue(
                    f"expiring-{expiration_date.isoformat()}",
                    "expiring",
                    "medium",
                    ["expiration_dates"],
                    expiration_date=expiration_date.isoformat(),
                    days_until_expiration=days_until_expiration,
                )
            )
    return issues


def missing_data_issues(provider):
    profile = provider.get("provider") or {}
    missing_fields = [
        f"provider.{field_name}"
        for field_name in ("name", "npi")
        if not str(profile.get(field_name) or "").strip()
    ]
    if not missing_fields:
        return []
    return [
        build_issue(
            "missing-critical-provider-data", "missing_data", "high", missing_fields
        )
    ]


def calculate_provider_issues(provider_ref, provider):
    return (
        discrepancy_issues(provider_ref)
        + expiration_issues(provider)
        + missing_data_issues(provider)
    )
