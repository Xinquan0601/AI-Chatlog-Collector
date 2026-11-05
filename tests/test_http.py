from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import http.cookiejar
import zipfile
from pathlib import Path
from urllib.parse import quote

from chatvault.constants import AGENT_DEFINITIONS, API_REVISION
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
        cls.export_path = Path(cls.temp.name) / "chatlog-export.db"
        export = sqlite3.connect(cls.export_path)
        try:
            export.executescript(
                """
                CREATE TABLE export_meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE sessions (
                    session_pk INTEGER PRIMARY KEY, source TEXT, tool TEXT, session_id TEXT,
                    parent_session_id TEXT, root_session_id TEXT, is_subagent INTEGER DEFAULT 0,
                    project_path TEXT, project_path_raw TEXT, title TEXT, display_title TEXT,
                    model TEXT, start_ms INTEGER, end_ms INTEGER, archived_ms INTEGER,
                    input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
                    reasoning_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0,
                    cache_write_tokens INTEGER DEFAULT 0, msg_count INTEGER DEFAULT 0,
                    src_msg_count INTEGER DEFAULT 0, user_msg_count INTEGER DEFAULT 0, meta_json TEXT
                );
                CREATE TABLE messages (
                    row_id INTEGER PRIMARY KEY, session_pk INTEGER, seq INTEGER, role TEXT,
                    is_reasoning INTEGER DEFAULT 0, timestamp_ms INTEGER, model TEXT, text TEXT,
                    tool TEXT, tool_status TEXT, tool_input TEXT, tool_output TEXT, msg_uid TEXT
                );
                """
            )
            export.execute("INSERT INTO export_meta(key, value) VALUES ('tool_version', '2.4.0')")
            export.execute(
                "INSERT INTO sessions(session_pk, source, tool, session_id, title, start_ms, end_ms, msg_count)"
                " VALUES (1, 'codex', 'codex', 'http-import', '导入的会话', 1786000000000, 1786000060000, 2)"
            )
            export.executemany(
                "INSERT INTO messages(row_id, session_pk, seq, role, text, timestamp_ms)"
                " VALUES (?, 1, ?, ?, ?, ?)",
                [(1, 1, "user", "旧库里的话", 1786000000000), (2, 2, "assistant", "旧库里的回复", 1786000010000)],
            )
            export.commit()
        finally:
            export.close()
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

    def test_00_health_and_static_cache(self):
        opener = self.opener()
        status, payload = self.call(opener, "/api/health")
        self.assertEqual(status, 200)
        data = payload["data"]
        self.assertEqual(data["api_revision"], API_REVISION)
        self.assertIsInstance(data["stale"], bool)
        with opener.open(urllib.request.Request(self.base + "/assets/app.js"), timeout=15) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get("Cache-Control"), "no-cache")

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
        self.assertEqual(len(payload["data"]["agents"]), len(AGENT_DEFINITIONS))

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

    def test_03_export_estimate(self):
        admin = self.opener()
        status, _ = self.login(admin, "admin", "test-admin-123")
        self.assertEqual(status, 200)
        status, payload = self.call(admin, "/api/export/estimate")
        self.assertEqual(status, 200)
        data = payload["data"]
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["sessions"], 1)
        self.assertEqual(data["by_type"]["user_message"], 1)
        self.assertEqual(data["by_type"]["assistant_message"], 1)
        self.assertEqual(data["by_type"]["tool_call"], 0)
        self.assertFalse(data["exceeded"])
        self.assertEqual(data["limit"], 0)
        self.assertTrue(data["unlimited"])
        self.assertGreater(data["bytes"], 0)

        status, payload = self.call(admin, "/api/export/estimate?types=assistant_message")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["total"], 1)
        self.assertEqual(payload["data"]["by_type"]["user_message"], 0)

        status, payload = self.call(admin, "/api/export/estimate?from=2026-12-01T00:00:00Z")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["total"], 0)

        status, payload = self.call(admin, "/api/export/estimate?agents=codex")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["total"], 2)
        self.assertEqual(payload["data"]["agents"], ["codex"])

        status, payload = self.call(admin, "/api/export/estimate?agents=codex,zcode")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["total"], 2)
        self.assertEqual(payload["data"]["agents"], ["codex", "zcode"])
        self.assertEqual(payload["data"]["by_agent"][0]["agent_key"], "codex")

        # The old single-agent parameter keeps working for the export dialog.
        status, payload = self.call(admin, "/api/export/estimate?agent=codex")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["total"], 2)
        self.assertEqual(payload["data"]["agents"], ["codex"])

        status, payload = self.call(admin, "/api/export/estimate?agents=zcode")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["total"], 0)

        # Closed interval: both the first and the last instants are included.
        status, payload = self.call(
            admin,
            "/api/export/estimate?from=2026-11-03T01:00:00Z&to=2026-11-03T01:01:00Z",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["total"], 2)

        anonymous = self.opener()
        status, payload = self.call(anonymous, "/api/export/estimate")
        self.assertEqual(status, 401)

    def test_04_legacy_import_api(self):
        viewer = self.opener()
        self.login(viewer, "viewer", "viewer-123")
        status, _ = self.call(viewer, "/api/import/candidates")
        self.assertEqual(status, 403)

        admin = self.opener()
        status, _ = self.login(admin, "admin", "test-admin-123")
        self.assertEqual(status, 200)
        status, payload = self.call(admin, "/api/import/candidates")
        self.assertEqual(status, 200)
        self.assertTrue(any(item["path"] == str(self.export_path) for item in payload["data"]["items"]))

        status, payload = self.call(admin, "/api/import/preview?path=" + str(self.export_path).replace("+", "%2B"))
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["totals"]["sessions"], 1)
        self.assertEqual(payload["data"]["sources"][0]["agent_key"], "codex")

        status, payload = self.call(admin, "/api/import/preview")
        self.assertEqual(status, 400)
        status, payload = self.call(admin, "/api/import/preview?path=" + str(Path(self.temp.name) / "missing.db"))
        self.assertEqual(status, 400)

        status, payload = self.call(admin, "/api/import/runs", "POST", {"path": str(Path(self.temp.name) / "missing.db")}, csrf=False)
        self.assertEqual(status, 403)
        status, payload = self.call(admin, "/api/import/runs", "POST", {"path": str(Path(self.temp.name) / "missing.db")})
        self.assertEqual(status, 400)

        status, payload = self.call(admin, "/api/import/runs", "POST", {"path": str(self.export_path)})
        self.assertEqual(status, 202)
        run_id = payload["data"]["run_id"]
        deadline = time.time() + 15
        detail = None
        while time.time() < deadline:
            status, payload = self.call(admin, "/api/import/runs/" + str(run_id))
            self.assertEqual(status, 200)
            detail = payload["data"]["item"]
            if detail["status"] not in ("queued", "running"):
                break
            time.sleep(0.2)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["status"], "success")
        self.assertEqual(detail["imported_sessions"], 1)
        self.assertEqual(detail["imported_events"], 2)

        status, payload = self.call(admin, "/api/import/runs?limit=5")
        self.assertEqual(status, 200)
        self.assertTrue(any(item["id"] == run_id for item in payload["data"]["items"]))

        status, payload = self.call(admin, "/api/import/runs/999999/cancel", "POST", {})
        self.assertEqual(status, 409)


    def test_05_archive_import_api(self):
        admin = self.opener()
        status, _ = self.login(admin, "admin", "test-admin-123")
        self.assertEqual(status, 200)
        with connection(self.db_path) as conn:
            before_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            shared = conn.execute(
                "SELECT fingerprint FROM events WHERE source_event_id = 'u1'"
            ).fetchone()
        self.assertIsNotNone(shared)

        bundle_path = Path(self.temp.name) / "machine-a.zip"
        columns = ["agent", "session_id", "event_type", "timestamp", "content", "fingerprint"]
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns)
        writer.writeheader()
        writer.writerow({
            "agent": "codex", "session_id": "http-session", "event_type": "user_message",
            "timestamp": "2026-11-03T01:00:00.000Z", "content": "hello",
            "fingerprint": shared["fingerprint"],
        })
        writer.writerow({
            "agent": "codex", "session_id": "remote-session", "event_type": "user_message",
            "timestamp": "2026-11-03T04:00:00.000Z", "content": "来自 A 的新记录",
            "fingerprint": "remote-fp-1",
        })
        with zipfile.ZipFile(bundle_path, "w") as archive:
            archive.writestr("events.csv", buffer.getvalue())
            archive.writestr(
                "sessions.csv",
                "agent_key,external_id,title,project,started_at\n"
                "codex,http-session,HTTP 测试会话,tests,2026-11-03T01:00:00.000Z\n"
                "codex,remote-session,远端会话,remote,2026-11-03T04:00:00.000Z\n",
            )
            archive.writestr(
                "manifest.json",
                json.dumps({
                    "export_version": "1.0",
                    "generated_at": "2026-11-03T05:00:00Z",
                    "counts": {"events": 2, "sessions": 2},
                }, ensure_ascii=False),
            )

        status, payload = self.call(admin, "/api/import/candidates")
        self.assertEqual(status, 200)
        self.assertTrue(any(item.get("kind") == "archive" for item in payload["data"]["items"]))

        status, payload = self.call(admin, "/api/import/preview?path=" + quote(str(bundle_path)))
        self.assertEqual(status, 200)
        data = payload["data"]
        self.assertEqual(data["kind"], "archive")
        self.assertEqual(data["format"], "bundle")
        self.assertEqual(data["totals"]["events"], 2)
        self.assertEqual(data["totals"]["new_events"], 1)
        self.assertEqual(data["totals"]["duplicate_events"], 1)
        self.assertEqual(data["totals"]["merged_sessions"], 1)

        status, payload = self.call(admin, "/api/import/runs", "POST", {"path": str(bundle_path)})
        self.assertEqual(status, 202)
        run_id = payload["data"]["run_id"]
        deadline = time.time() + 15
        detail = None
        while time.time() < deadline:
            status, payload = self.call(admin, "/api/import/runs/" + str(run_id))
            self.assertEqual(status, 200)
            detail = payload["data"]["item"]
            if detail["status"] not in ("queued", "running"):
                break
            time.sleep(0.2)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["status"], "success")
        self.assertEqual(detail["kind"], "archive")
        self.assertEqual(detail["imported_events"], 1)
        self.assertEqual(detail["duplicate_events"], 1)
        self.assertEqual(detail["imported_sessions"], 1)
        with connection(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], before_events + 1)
            session = conn.execute(
                "SELECT title, project FROM sessions WHERE external_id = 'remote-session'"
            ).fetchone()
            self.assertIsNotNone(session)
            self.assertEqual(session["title"], "远端会话")
            self.assertEqual(session["project"], "remote")


if __name__ == "__main__":
    unittest.main()
