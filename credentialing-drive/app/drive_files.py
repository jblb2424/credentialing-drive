import csv
import io
from datetime import date, datetime

from fastapi import HTTPException
from googleapiclient.http import MediaIoBaseDownload
from openpyxl import load_workbook

from app.config import (
    CSV_MIME_TYPE, DOCUMENT_MIME_TYPES, GOOGLE_DOCUMENT_MIME_TYPE,
    GOOGLE_SHEETS_MIME_TYPE, MAX_DOCUMENT_BYTES, MAX_SPREADSHEET_BYTES,
    SPREADSHEET_MIME_TYPES,
)



def download_drive_document(service, file_id):
    metadata = (
        service.files()
        .get(fileId=file_id, fields="id,name,mimeType,size,trashed")
        .execute()
    )
    if metadata.get("trashed"):
        raise HTTPException(status_code=404, detail="Drive file is trashed")
    if metadata.get("mimeType") not in DOCUMENT_MIME_TYPES:
        raise HTTPException(status_code=415, detail="Unsupported document format")

    file_size = int(metadata.get("size", 0))
    if file_size > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="Document exceeds the 20 MB processing limit")

    if metadata["mimeType"] == GOOGLE_DOCUMENT_MIME_TYPE:
        request = service.files().export_media(fileId=file_id, mimeType="text/plain")
    else:
        request = service.files().get_media(fileId=file_id)

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    document_bytes = buffer.getvalue()
    if len(document_bytes) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="Document exceeds the 20 MB processing limit")
    return metadata, document_bytes


def download_drive_spreadsheet(service, file_id):
    metadata = (
        service.files()
        .get(fileId=file_id, fields="id,name,mimeType,size,trashed")
        .execute()
    )
    if metadata.get("trashed"):
        raise HTTPException(status_code=404, detail="Drive file is trashed")
    if metadata.get("mimeType") not in SPREADSHEET_MIME_TYPES:
        raise HTTPException(status_code=415, detail="Unsupported spreadsheet format")
    if int(metadata.get("size", 0)) > MAX_SPREADSHEET_BYTES:
        raise HTTPException(
            status_code=413, detail="Spreadsheet exceeds the 10 MB processing limit"
        )

    if metadata["mimeType"] == GOOGLE_SHEETS_MIME_TYPE:
        request = service.files().export_media(fileId=file_id, mimeType=CSV_MIME_TYPE)
    else:
        request = service.files().get_media(fileId=file_id)

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    spreadsheet_bytes = buffer.getvalue()
    if len(spreadsheet_bytes) > MAX_SPREADSHEET_BYTES:
        raise HTTPException(
            status_code=413, detail="Spreadsheet exceeds the 10 MB processing limit"
        )
    return metadata, spreadsheet_bytes


def normalize_cell(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value.strip() if isinstance(value, str) else value


def parse_rows(headers, rows):
    raw_headers = []
    used_headers = set()
    for index, header in enumerate(headers, start=1):
        base_header = str(header or "").strip() or f"Column {index}"
        unique_header = base_header
        while unique_header in used_headers:
            unique_header = f"{base_header} ({index})"
        raw_headers.append(unique_header)
        used_headers.add(unique_header)

    parsed_rows = []
    for row_number, values in rows:
        fields = {
            header or f"Column {index}": normalize_cell(value)
            for index, (header, value) in enumerate(zip(raw_headers, values), start=1)
            if value not in (None, "")
        }
        if fields:
            parsed_rows.append({"row_number": row_number, "fields": fields})
    return parsed_rows


def parse_spreadsheet(spreadsheet_bytes, mime_type):
    if mime_type in {CSV_MIME_TYPE, GOOGLE_SHEETS_MIME_TYPE}:
        csv_rows = list(csv.reader(io.StringIO(spreadsheet_bytes.decode("utf-8-sig"))))
        if not csv_rows:
            return []
        return parse_rows(csv_rows[0], enumerate(csv_rows[1:], start=2))

    workbook = load_workbook(io.BytesIO(spreadsheet_bytes), read_only=True, data_only=True)
    worksheet = workbook.active
    rows = worksheet.iter_rows(values_only=True)
    headers = next(rows, None)
    if headers is None:
        return []
    return parse_rows(headers, enumerate(rows, start=2))


def split_list(value):
    if not value:
        return []
    return [item.strip() for item in re.split(r"[;,|]", str(value)) if item.strip()]
