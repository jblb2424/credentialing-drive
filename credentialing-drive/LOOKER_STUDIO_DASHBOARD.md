# Credentialing Dashboard

Use the BigQuery dataset `credentialing_reporting` as the Looker Studio data source.

## Pages

### Credentialing Overview

Source: `provider_dashboard_overview`, `provider_expiration_alerts`, and `provider_issues`.

- Scorecard: total providers from `provider_dashboard_overview`.
- Scorecard: expiring in 30 days from `provider_expiration_alerts` filtered to `days_until_expiration` between 0 and 30.
- Scorecard: open issues from `provider_issues`.
- Attention Required table: provider name, group, issue type, due date, and days until due.
- Filters: entity, group, provider, and expiration date range.

### Providers

Source: `provider_dashboard_overview`.

- Searchable provider table: provider name, NPI, credentials, entity, group, locations, payers, and next expiration date.
- Enable chart interaction as a filter so selecting a provider filters the detail panels.
- Use `provider_id` as the drill field and hidden join key.

### Provider Detail

Source: `provider_dashboard_overview` and `provider_expiration_alerts`.

- Detail scorecards: provider name, NPI, entity, and group.
- Lists: locations, payers, licenses, and expiration dates.
- Expiration table: raw expiration value, normalized expiration date, and days until expiration.
- Carry the `provider_id` filter from the Providers table into this page.

## Data Contracts

- `provider_dashboard_overview`: one latest snapshot per provider.
- `provider_expiration_alerts`: one row per provider expiration date.
- `provider_issues`: missing NPI, expired credentials, and credentials expiring within 30 days.

No source PDF bytes or OCR text are available in these reporting views.
