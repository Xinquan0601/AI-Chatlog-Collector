from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import http.cookiejar
from pathlib import Path

from chatvault.db import connection, init_db
from chatvault.ingestion import import_batch
from chatvault.jobs import get_job_manager
from chatvault.server import create_server


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp.name) / "http.db"
        os.environ["AGENTTRAIL_ADMIN_PASSWORD"] = "test-admin-123"
        init_db(cls.db_path)
        with connection(cls.db_path) as conn:
            import_batch(conn, "codex", {
                "source_path": "test.jsonl",
                "sessions": [{"external_id": "http-session", "title": "HTTP 测试会话", "project": "tests"}],
                "events": [
                    {"session_external_id": "http-session", "type": "user_message", "source_event_id": "u1", "sequence": 1, "timestamp": "2026-11-03T01:00:00Z", "content": "hello"},
                    {"session_external_id": "http-session", "type": "assistant_message", "source_event_id": "a1", "sequence": 2, "timestamp": "2026-11-03T01:01:00Z", "content": "world"},
                ],
                "errors": [],
            })
            conn.commit()
        cls.server, cls.info = create_server("127.0.0.1", 0, db_path=cls.db_path, disable_scheduler=True)
        cls.port = cls.info["port"]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        manager = get_job_manager()
        manager.stop()
        cls.temp.cleanup()
        os.environ.pop("AGENTTRAIL_ADMIN_PASSWORD", None)

    def opener(self):
        jar = http.cookiejar.CookieJar()
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(self, opener, path, method="GET", body=None, csrf=True):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if method != "GET":
            if csrf:
                headers["X-AgentTrail-Request"] = "1"
            if data is not None:
                headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with opener.open(request, timeout=15) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read())
            return exc.code, payload

    def login(self, opener, username, password):
        return self.call(opener, "/api/auth/login", "POST", {"username": username, "password": password})

    def test_01_auth_and_csrf(self):
        anonymous = self.opener()
        status, payload = self.call(anonymous, "/api/auth/status")
        self.assertEqual(status, 200)
        self.assertFalse(payload["data"]["authenticated"])
        status, payload = self.call(anonymous, "/api/bootstrap")
        self.assertEqual(status, 401)
        status, payload = self.call(anonymous, "/api/auth/login", "POST", {"username": "admin", "password": "test-admin-123"}, csrf=False)
        self.assertEqual(status, 403)
        status, payload = self.login(anonymous, "admin", "test-admin-123")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["user"]["role"], "admin")
        status, payload = self.call(anonymous, "/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["data"]["agents"]), 6)

    def test_02_data_and_viewer_permissions(self):
        admin = self.opener()
        status, _ = self.login(admin, "admin", "test-admin-123")
        self.assertEqual(status, 200)
        status, payload = self.call(admin, "/api/sessions")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["total"], 1)
        status, payload = self.call(admin, "/api/users", "POST", {"username": "viewer", "password": "viewer-123", "role": "viewer"})
        self.assertEqual(status, 201)
        viewer = self.opener()
        status, _ = self.login(viewer, "viewer", "viewer-123")
        self.assertEqual(status, 200)
        status, payload = self.call(viewer, "/api/sessions")
        self.assertEqual(status, 200)
        status, payload = self.call(viewer, "/api/settings")
        self.assertEqual(status, 403)
        status, payload = self.call(viewer, "/api/agents/codex/sync", "POST", {})
        self.assertEqual(status, 403)
        status, payload = self.call(admin, "/api/overview")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["totals"]["events"], 2)


if __name__ == "__main__":
    unittest.main()
