SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

CONNECTION_ID = "default"
CONNECTION_COLLECTION = "drive_connections"
EVENT_COLLECTION = "drive_change_events"
PROVIDER_COLLECTION = "providers"
PROVIDER_IDENTITY_COLLECTION = "provider_identities"
BIGQUERY_REPORTING_DATASET = "credentialing_reporting"
BIGQUERY_REPORTING_TABLE = "provider_reporting_events"
PDF_MIME_TYPE = "application/pdf"
JPEG_MIME_TYPE = "image/jpeg"
PNG_MIME_TYPE = "image/png"
GIF_MIME_TYPE = "image/gif"
TIFF_MIME_TYPE = "image/tiff"
BMP_MIME_TYPE = "image/bmp"
WEBP_MIME_TYPE = "image/webp"
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
GOOGLE_DOCUMENT_MIME_TYPE = "application/vnd.google-apps.document"
TEXT_MIME_TYPES = {"text/plain", "text/rtf", "text/html", "text/markdown"}
CSV_MIME_TYPE = "text/csv"
XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
GOOGLE_SHEETS_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
SPREADSHEET_MIME_TYPES = {CSV_MIME_TYPE, XLSX_MIME_TYPE, GOOGLE_SHEETS_MIME_TYPE}
OCR_DOCUMENT_MIME_TYPES = {
    PDF_MIME_TYPE, JPEG_MIME_TYPE, PNG_MIME_TYPE, GIF_MIME_TYPE,
    TIFF_MIME_TYPE, BMP_MIME_TYPE, WEBP_MIME_TYPE,
}
TEXT_DOCUMENT_MIME_TYPES = TEXT_MIME_TYPES | {DOCX_MIME_TYPE, GOOGLE_DOCUMENT_MIME_TYPE}
DOCUMENT_MIME_TYPES = OCR_DOCUMENT_MIME_TYPES | TEXT_DOCUMENT_MIME_TYPES
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_SPREADSHEET_BYTES = 10 * 1024 * 1024
MAX_GEMINI_INPUT_CHARS = 100_000
DOCUMENT_CATEGORIES = (
    "state_license", "board_certificate_or_eligibility_letter",
    "education_training_certificates", "dea_registration", "ecfmg_certificate",
    "controlled_substance_certificate", "cv_or_resume", "malpractice_certificate",
    "malpractice_claim_information", "hospital_privileges_letter", "drivers_license",
    "social_security_card", "collaborating_or_supervising_physician_agreement",
    "peer_references", "w_9", "irs_letter", "articles_of_organization",
    "bank_letter_or_voided_check", "other",
)
