import json
import logging
import os

from fastapi import HTTPException
from google import genai

from app.config import DOCUMENT_CATEGORIES, MAX_GEMINI_INPUT_CHARS
from app.connections import get_project_id

logger = logging.getLogger(__name__)



def interpret_text_with_gemini(ocr_text):
    if not ocr_text:
        return {"document_type": "unknown", "summary": "No text extracted"}

    prompt = """You interpret healthcare credentialing documents. Return JSON only with these keys:
document_type, entity_name, group_name, provider, locations, payers, licenses, expiration_dates, and summary.
`provider` must be an object with name, npi, and credentials. `locations`, `payers`, and `licenses` must be arrays.
Use null or empty arrays when a value is not present. Do not infer values that are not supported by the text.

OCR text:
"""
    client = genai.Client(
        vertexai=True,
        project=get_project_id(),
        location=os.environ.get("VERTEX_AI_LOCATION", "global"),
    )
    response = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=prompt + ocr_text[:MAX_GEMINI_INPUT_CHARS],
        config={"response_mime_type": "application/json", "temperature": 0},
    )
    return response.text


def classify_document_category(metadata, content):
    """Classify each uploaded file once so all resulting revisions share its category."""
    prompt = f"""Classify this healthcare credentialing upload into exactly one category.
Return JSON only in this shape: {{"document_category": "..."}}.
Use only one of these values:
{", ".join(DOCUMENT_CATEGORIES)}.
Choose other when the file does not clearly fit one category. Do not infer a category from
missing information. The file name is {metadata.get("name")!r}.

Document content:
"""
    try:
        client = genai.Client(
            vertexai=True,
            project=get_project_id(),
            location=os.environ.get("VERTEX_AI_LOCATION", "global"),
        )
        response = client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=prompt + content[:MAX_GEMINI_INPUT_CHARS],
            config={"response_mime_type": "application/json", "temperature": 0},
        )
        category = parse_gemini_extraction(response.text).get("document_category")
        return category if category in DOCUMENT_CATEGORIES else "other"
    except Exception:
        logger.exception(
            "Document category classification failed for Drive file_id=%s", metadata.get("id")
        )
        return "other"


def interpret_spreadsheet_with_gemini(rows):
    if not rows:
        return []

    prompt = """You interpret healthcare credentialing onboarding spreadsheets with
unknown column names and layouts. Return JSON only in this shape:
{"providers": [{"source_row_numbers": [2], "document_type": "...",
"entity_name": null, "group_name": null,
"provider": {"name": null, "npi": null, "credentials": null},
"locations": [], "payers": [], "licenses": [], "expiration_dates": [],
"summary": null}]}

Normalize every provider represented in the spreadsheet into this canonical shape.
Use the original row numbers that support each provider in source_row_numbers. A provider
may use multiple rows when the layout requires it. Do not infer values that are not in the
spreadsheet, do not include a provider without a name or NPI, and use null or [] for unknown
values. Preserve all meaningful payer, location, license, and expiration information.

Spreadsheet rows (each object contains an original row_number and raw, client-supplied columns):
"""
    client = genai.Client(
        vertexai=True,
        project=get_project_id(),
        location=os.environ.get("VERTEX_AI_LOCATION", "global"),
    )
    max_batch_chars = MAX_GEMINI_INPUT_CHARS - len(prompt)
    batches = []
    batch = []
    batch_chars = 2
    for row in rows:
        row_json = json.dumps(row, default=str)
        if len(row_json) > max_batch_chars:
            raise HTTPException(status_code=413, detail="Spreadsheet row exceeds the Gemini input limit")
        if batch and batch_chars + len(row_json) + 1 > max_batch_chars:
            batches.append(batch)
            batch = []
            batch_chars = 2
        batch.append(row)
        batch_chars += len(row_json) + 1
    if batch:
        batches.append(batch)

    providers = []
    for batch in batches:
        response = client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=prompt + json.dumps(batch, default=str),
            config={"response_mime_type": "application/json", "temperature": 0},
        )
        providers.extend(parse_gemini_spreadsheet_extraction(response.text, batch))
    return providers


def parse_gemini_extraction(interpretation):
    if isinstance(interpretation, dict):
        return interpretation
    try:
        parsed = json.loads(interpretation)
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="Gemini returned invalid structured output")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="Gemini returned an unexpected response")
    return parsed


def parse_gemini_spreadsheet_extraction(interpretation, source_rows):
    try:
        parsed = json.loads(interpretation) if isinstance(interpretation, str) else interpretation
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="Gemini returned invalid spreadsheet output")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("providers"), list):
        raise HTTPException(status_code=502, detail="Gemini returned an unexpected spreadsheet output")

    valid_row_numbers = {row["row_number"] for row in source_rows}
    providers = []
    for provider in parsed["providers"]:
        if not isinstance(provider, dict):
            raise HTTPException(status_code=502, detail="Gemini returned an invalid provider")
        row_numbers = provider.get("source_row_numbers")
        if not isinstance(row_numbers, list):
            raise HTTPException(status_code=502, detail="Gemini omitted spreadsheet row provenance")
        try:
            row_numbers = sorted({int(row_number) for row_number in row_numbers})
        except (TypeError, ValueError):
            raise HTTPException(status_code=502, detail="Gemini returned invalid spreadsheet row provenance")
        if not row_numbers or not set(row_numbers).issubset(valid_row_numbers):
            raise HTTPException(status_code=502, detail="Gemini returned unknown spreadsheet row provenance")
        provider_profile = provider.get("provider") or {}
        if not isinstance(provider_profile, dict) or not (
            provider_profile.get("name") or provider_profile.get("npi")
        ):
            raise HTTPException(status_code=502, detail="Gemini returned an unidentified provider")
        provider["source_row_numbers"] = row_numbers
        providers.append(provider)
    return providers
