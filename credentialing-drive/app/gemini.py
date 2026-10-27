import json
import logging
import os

from fastapi import HTTPException
from google import genai

from app.config import DOCUMENT_CATEGORIES, MAX_GEMINI_INPUT_CHARS
from app.connections import get_project_id
from app.prompts import prompt_template, render_prompt

logger = logging.getLogger(__name__)


def generate_gemini_json(contents):
    client = genai.Client(
        vertexai=True,
        project=get_project_id(),
        location=os.environ.get("VERTEX_AI_LOCATION", "global"),
    )
    response = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
        contents=contents,
        config={"response_mime_type": "application/json", "temperature": 0},
    )
    return response.text


def reconcile_provider_records_with_gemini(candidates):
    """Run a fresh, purpose-specific model pass over existing provider records."""
    model = os.environ.get(
        "GEMINI_RECONCILIATION_MODEL", os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
    )
    client = genai.Client(
        vertexai=True,
        project=get_project_id(),
        location=os.environ.get("VERTEX_AI_LOCATION", "global"),
    )
    prompt = prompt_template("validation", "provider_record_reconciliation")
    response = client.models.generate_content(
        model=model,
        contents=prompt + json.dumps(candidates, default=str),
        config={"response_mime_type": "application/json", "temperature": 0},
    )
    return parse_gemini_extraction(response.text), model


def interpret_text_with_gemini(ocr_text):
    if not ocr_text:
        return {"document_type": "unknown", "summary": "No text extracted"}

    prompt = render_prompt(
        "ingestion",
        "document_extraction",
        date_normalization_policy=prompt_template("ingestion", "date_normalization_policy"),
        facts_and_verification_sources_policy=prompt_template(
            "ingestion", "facts_and_verification_sources_policy"
        ),
    )
    return generate_gemini_json(prompt + ocr_text[:MAX_GEMINI_INPUT_CHARS])


def classify_document_category(metadata, content):
    """Classify each uploaded file once so all resulting revisions share its category."""
    prompt = render_prompt(
        "ingestion",
        "document_category_classification",
        document_categories=", ".join(DOCUMENT_CATEGORIES),
        file_name=repr(metadata.get("name")),
    )
    try:
        category = parse_gemini_extraction(
            generate_gemini_json(prompt + content[:MAX_GEMINI_INPUT_CHARS])
        ).get("document_category")
        return category if category in DOCUMENT_CATEGORIES else "other"
    except Exception:
        logger.exception(
            "Document category classification failed for Drive file_id=%s", metadata.get("id")
        )
        return "other"


def interpret_spreadsheet_with_gemini(rows):
    if not rows:
        return []

    prompt = render_prompt(
        "ingestion",
        "spreadsheet_extraction",
        date_normalization_policy=prompt_template("ingestion", "date_normalization_policy"),
        facts_and_verification_sources_policy=prompt_template(
            "ingestion", "facts_and_verification_sources_policy"
        ),
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
        interpretation = generate_gemini_json(prompt + json.dumps(batch, default=str))
        providers.extend(parse_gemini_spreadsheet_extraction(interpretation, batch))
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
