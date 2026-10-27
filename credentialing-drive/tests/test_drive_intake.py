import time
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.connections import connection_status, get_webhook_url
from app.routes import (
    activate_intake_folder,
    list_drive_folders,
    renew_drive_watch,
    summarize_drive_intake_events,
)


class DriveIntakeTests(unittest.TestCase):
    def test_intake_status_counts_only_active_document_events(self):
        events = [
            {"status": "detected"},
            {"status": "queued"},
            {"status": "processing"},
            {"status": "processing"},
            {"status": "retrying"},
            {"status": "processed"},
            {"status": "imported"},
            {"status": "skipped"},
            {"status": "enqueue_failed"},
        ]

        result = summarize_drive_intake_events(events)

        self.assertEqual(result["active_document_count"], 5)
        self.assertTrue(result["is_processing"])
        self.assertEqual(result["detected_document_count"], 1)
        self.assertEqual(result["queued_document_count"], 1)
        self.assertEqual(result["processing_document_count"], 2)
        self.assertEqual(result["retrying_document_count"], 1)

    def test_missing_webhook_url_explains_local_monitoring_setup(self):
        with patch.dict("app.connections.os.environ", {}, clear=True):
            with self.assertRaises(HTTPException) as error:
                get_webhook_url()

        self.assertEqual(error.exception.status_code, 503)
        self.assertIn("publicly reachable HTTPS tunnel", error.exception.detail)

    def _folder_service(self):
        service = Mock()
        service.files().get().execute.return_value = {
            "id": "folder-123",
            "name": "Credentialing documents",
            "mimeType": "application/vnd.google-apps.folder",
            "trashed": False,
        }
        return service

    def test_folder_listing_follows_drive_pagination(self):
        service = Mock()
        service.files().list().execute.side_effect = [
            {"files": [{"id": "one", "name": "A"}], "nextPageToken": "page-2"},
            {"files": [{"id": "two", "name": "B"}]},
        ]
        with patch("app.routes.get_drive_service", return_value=service):
            result = list_drive_folders("client-1")

        self.assertEqual([folder["id"] for folder in result["folders"]], ["one", "two"])

    def test_folder_is_saved_only_after_drive_confirms_monitoring(self):
        connection = {
            "credentials": {"token": "token"},
            "folder_id": "old-folder",
            "folder_name": "Old folder",
            "channel_id": "old-channel",
            "resource_id": "old-resource",
        }
        service = self._folder_service()
        service.changes().getStartPageToken().execute.return_value = {"startPageToken": "page-token"}
        service.changes().watch().execute.return_value = {
            "resourceId": "new-resource",
            "expiration": str(int(time.time() * 1000) + 60_000),
        }

        with (
            patch("app.routes.get_connection", return_value=connection),
            patch("app.routes.get_drive_service", return_value=service),
            patch("app.routes.get_webhook_url", return_value="https://app.example.test/webhooks/google-drive"),
            patch("app.routes.update_connection") as update_connection,
            patch("app.routes.save_connection_lookup") as save_lookup,
            patch("app.routes.delete_connection_lookup") as delete_lookup,
        ):
            result = activate_intake_folder("client-1", "folder-123")

        self.assertTrue(result["intake_ready"])
        self.assertEqual(result["folder"]["id"], "folder-123")
        saved = update_connection.call_args.args[0]
        self.assertEqual(saved["folder_id"], "folder-123")
        self.assertEqual(saved["folder_name"], "Credentialing documents")
        self.assertEqual(saved["resource_id"], "new-resource")
        self.assertEqual(saved["watch_owner"], "cloud")
        save_lookup.assert_called_once()
        delete_lookup.assert_called_once_with("channel_id", "old-channel")
        service.channels().stop.assert_called_once_with(
            body={"id": "old-channel", "resourceId": "old-resource"}
        )

    def test_failed_watch_leaves_the_existing_intake_unchanged(self):
        service = self._folder_service()
        service.changes().getStartPageToken().execute.return_value = {"startPageToken": "page-token"}
        service.changes().watch().execute.side_effect = RuntimeError("Drive is unavailable")

        with (
            patch("app.routes.get_connection", return_value={"credentials": {"token": "token"}}),
            patch("app.routes.get_drive_service", return_value=service),
            patch("app.routes.get_webhook_url", return_value="https://app.example.test/webhooks/google-drive"),
            patch("app.routes.update_connection") as update_connection,
            patch("app.routes.logger"),
        ):
            with self.assertRaises(HTTPException) as error:
                activate_intake_folder("client-1", "folder-123")

        self.assertEqual(error.exception.status_code, 502)
        update_connection.assert_not_called()

    def test_scheduler_skips_a_locally_owned_watch(self):
        local_connection = {"folder_id": "folder-123", "watch_owner": "local"}
        cloud_connection = {"folder_id": "folder-456", "watch_owner": "cloud"}
        request = Mock()

        with (
            patch("app.routes.require_scheduler_identity"),
            patch("app.routes.list_entity_connections", return_value=[
                ("local-client", local_connection),
                ("cloud-client", cloud_connection),
            ]),
            patch("app.routes.start_drive_watch_for_entity") as start_watch,
        ):
            result = renew_drive_watch(request)

        self.assertEqual(result, {"renewed_entity_ids": ["cloud-client"]})
        start_watch.assert_called_once_with("cloud-client")

    def test_connection_status_requires_an_unexpired_complete_watch(self):
        valid_connection = {
            "credentials": {"token": "token"},
            "folder_id": "folder-123",
            "folder_name": "Credentialing documents",
            "page_token": "page-token",
            "channel_id": "channel-123",
            "channel_token": "channel-token",
            "resource_id": "resource-123",
            "channel_expiration": str(int(time.time() * 1000) + 60_000),
        }
        reference = Mock()
        reference.get.return_value.exists = True
        reference.get.return_value.to_dict.return_value = valid_connection
        with patch("app.connections.get_connection_ref", return_value=reference):
            self.assertTrue(connection_status("client-1")["intake_ready"])

        valid_connection["channel_expiration"] = str(int(time.time() * 1000) - 1)
        with patch("app.connections.get_connection_ref", return_value=reference):
            self.assertFalse(connection_status("client-1")["intake_ready"])


if __name__ == "__main__":
    unittest.main()
