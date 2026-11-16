import unittest

from fastapi import HTTPException

from app.native_uploads import native_upload_event, validate_native_upload
from app.task_queue import task_id_for_document


class NativeUploadTests(unittest.TestCase):
    def test_infers_a_supported_mime_type_from_the_file_name(self):
        file_name, mime_type, size_bytes = validate_native_upload("credentials.pdf", "", 1024)
        self.assertEqual(file_name, "credentials.pdf")
        self.assertEqual(mime_type, "application/pdf")
        self.assertEqual(size_bytes, 1024)

    def test_rejects_unsupported_native_uploads(self):
        with self.assertRaises(HTTPException) as error:
            validate_native_upload("archive.zip", "application/zip", 1024)
        self.assertEqual(error.exception.status_code, 415)

    def test_upload_event_has_an_ephemeral_native_source(self):
        event = native_upload_event("abc123", "credentials.pdf", "application/pdf", 1024, "client-a")
        self.assertEqual(event["id"], "upload-abc123")
        self.assertEqual(event["source"], "native_upload")
        self.assertEqual(event["status"], "awaiting_upload")
        self.assertTrue(event["object_name"].startswith("native-uploads/client-a/abc123/"))

    def test_queue_id_is_isolated_by_source(self):
        self.assertNotEqual(
            task_id_for_document("client-a", "same-id", "google_drive"),
            task_id_for_document("client-a", "same-id", "native_upload"),
        )


if __name__ == "__main__":
    unittest.main()
