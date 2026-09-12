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


def first_present(item, field_names):
    for field_name in field_names:
        value = item.get(field_name)
        if value not in (None, ""):
            return value
    return None


def credential_label(category, item):
    if category == "license":
        parts = [item.get("state"), item.get("type") or item.get("license_type")]
        label = " ".join(str(part) for part in parts if part)
        number = first_present(item, ("license_number", "number"))
        return f"{label or 'License'} #{number}" if number else label or "License"

    if category == "board_certification":
        specialty = item.get("name") or item.get("specialty")
        board = item.get("certifying_board") or item.get("board")
        if specialty and board:
            return f"{specialty} ({board})"
        return specialty or board or "Board certification"

    if category == "liability_insurance":
        carrier = item.get("carrier_name") or item.get("carrier")
        policy_number = first_present(item, ("policy_number", "number"))
        if carrier and policy_number:
            return f"{carrier} policy #{policy_number}"
        return carrier or (f"Policy #{policy_number}" if policy_number else "Liability insurance")

    return "Unclassified credential"


def structured_expiration_candidates(provider):
    sources = (
        ("licenses", "license", ("expiration_date", "expires_at", "expiry_date")),
        (
            "specialties",
            "board_certification",
            ("expiration_date", "expires_at", "board_expiration_date"),
        ),
        (
            "liability_insurance",
            "liability_insurance",
            ("expiration_date", "expires_at", "policy_expiration_date"),
        ),
    )
    candidates = []

    for collection_name, category, expiration_fields in sources:
        for index, item in enumerate(provider.get(collection_name) or []):
            if not isinstance(item, dict):
                continue

            raw_expiration = first_present(item, expiration_fields)
            expiration_date = parse_expiration_date(raw_expiration)
            if not expiration_date:
                continue

            expiration_field = next(
                field_name
                for field_name in expiration_fields
                if item.get(field_name) == raw_expiration
            )
            candidates.append(
                {
                    "category": category,
                    "label": credential_label(category, item),
                    "identifier": first_present(
                        item, ("license_number", "policy_number", "number")
                    ),
                    "expiration_date": expiration_date,
                    "field_path": f"{collection_name}[{index}].{expiration_field}",
                    "index": index,
                }
            )

    return candidates


def expiration_issue(candidate):
    expiration_date = candidate["expiration_date"]
    days_until_expiration = (expiration_date - date.today()).days
    category = candidate["category"]
    issue_prefix = "expired" if days_until_expiration < 0 else "expiring"
    if category == "unclassified":
        issue_id = f"{issue_prefix}-{expiration_date.isoformat()}"
    else:
        issue_id = f"{issue_prefix}-{category}-{candidate['index']}-{expiration_date.isoformat()}"

    issue_details = {
        "expiration_date": expiration_date.isoformat(),
        "credential_category": category,
        "credential_label": candidate["label"],
        "credential_identifier": candidate.get("identifier"),
    }
    if days_until_expiration < 0:
        return build_issue(
            issue_id,
            "expired",
            "high",
            [candidate["field_path"]],
            days_past_expiration=abs(days_until_expiration),
            **issue_details,
        )
    if days_until_expiration <= EXPIRING_WINDOW_DAYS:
        return build_issue(
            issue_id,
            "expiring",
            "medium",
            [candidate["field_path"]],
            days_until_expiration=days_until_expiration,
            **issue_details,
        )
    return None


def expiration_issues(provider):
    candidates = structured_expiration_candidates(provider)
    issues = [issue for candidate in candidates if (issue := expiration_issue(candidate))]
    structured_dates = {candidate["expiration_date"] for candidate in candidates}

    for raw_value in provider.get("expiration_dates") or []:
        expiration_date = parse_expiration_date(raw_value)
        if not expiration_date or expiration_date in structured_dates:
            continue

        issue = expiration_issue(
            {
                "category": "unclassified",
                "label": "Unclassified credential",
                "expiration_date": expiration_date,
                "field_path": "expiration_dates",
                "index": 0,
            }
        )
        if issue:
            issues.append(issue)
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
