import re


# This is deliberately a narrow vocabulary: it prevents narrative role labels
# (for example, "Physician") from being treated as credential designations.
PROVIDER_TYPE_ALIASES = {
    "MD": "MD",
    "MEDICAL DOCTOR": "MD",
    "DO": "DO",
    "DOCTOR OF OSTEOPATHIC MEDICINE": "DO",
    "NP": "NP",
    "NURSE PRACTITIONER": "NP",
    "PA": "PA",
    "PHYSICIAN ASSISTANT": "PA",
    "RN": "RN",
    "REGISTERED NURSE": "RN",
    "LPN": "LPN",
    "LICENSED PRACTICAL NURSE": "LPN",
    "PT": "PT",
    "PHYSICAL THERAPIST": "PT",
    "OT": "OT",
    "OCCUPATIONAL THERAPIST": "OT",
    "SLP": "SLP",
    "SPEECH LANGUAGE PATHOLOGIST": "SLP",
    "DDS": "DDS",
    "DMD": "DMD",
    "DPM": "DPM",
    "OD": "OD",
    "PHARMD": "PharmD",
    "PHARMACIST": "PharmD",
}


def normalize_provider_type(value):
    """Return a controlled provider designation or None for a descriptive role."""
    normalized = re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()
    return PROVIDER_TYPE_ALIASES.get(normalized) or PROVIDER_TYPE_ALIASES.get(
        normalized.replace(" ", "")
    )


def provider_type_values_conflict(previous_value, current_value):
    """Only compare two precise credential designations, never broad role labels."""
    previous_type = normalize_provider_type(previous_value)
    current_type = normalize_provider_type(current_value)
    return bool(previous_type and current_type and previous_type != current_type)
