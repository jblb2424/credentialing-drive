import unittest

from app.routes import router as dashboard_router
from worker import app as worker_app


class WorkerIsolationTests(unittest.TestCase):
    def _paths(self, app):
        return {route.path for route in app.routes if hasattr(route, "path")}

    def test_dashboard_does_not_expose_the_document_worker_route(self):
        self.assertNotIn("/tasks/process-drive-file", self._paths(dashboard_router))

    def test_worker_exposes_only_task_and_health_routes(self):
        paths = self._paths(worker_app)
        self.assertIn("/tasks/process-drive-file", paths)
        self.assertIn("/healthz", paths)
        self.assertNotIn("/webhooks/google-drive", paths)
        self.assertNotIn("/providers", paths)


if __name__ == "__main__":
    unittest.main()
