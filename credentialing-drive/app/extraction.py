import io
import os
import re
import zipfile
from html import unescape
from xml.etree import ElementTree

from fastapi import HTTPException
from google.api_core.client_options import ClientOptions
from google.cloud import documentai

from app.config import DOCX_MIME_TYPE, OCR_DOCUMENT_MIME_TYPES
from app.connections import get_project_id



def extract_text_with_document_ai(document_bytes, mime_type):
    project_id = get_project_id()
    location = os.environ.get("DOCUMENT_AI_LOCATION", "us")
    processor_id = os.environ.get("DOCUMENT_AI_PROCESSOR_ID")
    if not processor_id:
        raise HTTPException(status_code=500, detail="DOCUMENT_AI_PROCESSOR_ID is not configured")

    client = documentai.DocumentProcessorServiceClient(
        client_options=ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
    )
    request = documentai.ProcessRequest(
        name=client.processor_path(project_id, location, processor_id),
        raw_document=documentai.RawDocument(content=document_bytes, mime_type=mime_type),
    )
    document = client.process_document(request=request).document
    return document.text, len(document.pages)


def extract_text_from_docx(document_bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(document_bytes)) as archive:
            document_xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        raise HTTPException(status_code=422, detail="Invalid DOCX document")

    root = ElementTree.fromstring(document_xml)
    paragraphs = []
    for paragraph in root.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
        text = "".join(
            node.text or ""
            for node in paragraph.findall(
                ".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
            )
        ).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def extract_text_from_text_document(document_bytes, mime_type):
    if mime_type == DOCX_MIME_TYPE:
        return extract_text_from_docx(document_bytes)

    text = document_bytes.decode("utf-8", errors="replace")
    if mime_type == "text/html":
        text = unescape(re.sub(r"<[^>]+>", " ", text))
    elif mime_type == "text/rtf":
        text = text.replace("\\par", "\n")
        text = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", text)
        text = text.replace("{", " ").replace("}", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def extract_document_text(document_bytes, mime_type):
    if mime_type in OCR_DOCUMENT_MIME_TYPES:
        return extract_text_with_document_ai(document_bytes, mime_type)
    return extract_text_from_text_document(document_bytes, mime_type), 1
