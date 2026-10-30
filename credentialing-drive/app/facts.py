"""Canonical fact boundary between extraction output and Firestore normalization."""

# Some legacy flattened fields remain accepted so existing imports keep their
# behavior. Everything outside this list is extraction context, not a provider fact.
FACT_EXTRACTION_FIELDS = (
    "entity_name",
    "group_name",
    "group",
    "provider",
    "locations",
    # Accepted only to migrate older extraction responses into the unified locations field.
    "provider_locations",
    "payers",
    "payer_enrollments",
    "licenses",
    "specialties",
    "education",
    "liability_insurance",
    "expiration_dates",
    "summary",
    "type_2_npi",
    "tax_id",
    "dba",
    "provider_name",
    "npi",
    "credentials",
    "provider_address",
)


def factual_extraction(extraction):
    """Retain only fields eligible to become canonical provider facts."""
    return {
        field_name: extraction.get(field_name)
        for field_name in FACT_EXTRACTION_FIELDS
        if field_name in extraction
    }
