from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from chatvault.analytics import analytics, list_session_events, list_sessions, overview, type_counts
from chatvault.auth import (
    authenticate,
    create_user,
    create_web_session,
    ensure_default_admin,
    resolve_session,
)
from chatvault.db import connection, get_settings, init_db, list_agents
from chatvault.exporting import create_export
from chatvault.ingestion import import_batch, refresh_session_aggregates
from chatvault.parsers import (
    parse_claude_jsonl,
    parse_codex_jsonl,
    parse_dsh_jsonl,
    parse_generic_document,
    parse_generic_jsonl,
)
from chatvault.utils import json_dumps


class ParserTests(unittest.TestCase):
    def test_codex_rollout_four_types(self):
        lines = [
            json.dumps({"type": "session_meta", "timestamp": "2026-10-30T12:00:00Z", "payload": {"session_id": "cx-1", "cwd": "D:/demo", "originator": "Codex Desktop"}}),
            json.dumps({"type": "response_item", "timestamp": "2026-10-30T12:00:01Z", "payload": {"type": "message", "id": "m1", "role": "user", "content": [{"type": "input_text", "text": "请分析项目结构"}]}}),
            json.dumps({"type": "response_item", "timestamp": "2026-10-30T12:00:02Z", "payload": {"type": "function_call", "name": "exec_command", "call_id": "c1", "arguments": "{\"cmd\":\"git status\"}"}}),
            json.dumps({"type": "response_item", "timestamp": "2026-10-30T12:00:03Z", "payload": {"type": "function_call_output", "call_id": "c1", "output": "## main"}}),
            json.dumps({"type": "response_item", "timestamp": "2026-10-30T12:00:04Z", "payload": {"type": "message", "id": "m2", "role": "assistant", "content": [{"type": "output_text", "text": "仓库是干净的。"}]}}),
        ]
        result = parse_codex_jsonl(lines, "rollout-2026-10-30T12-00-00-cx-1.jsonl", "2026-10-30T12:00:00Z")
        types = {event["type"] for event in result["events"]}
        self.assertEqual(types, {"user_message", "assistant_message", "tool_call", "tool_result"})
        self.assertEqual(result["sessions"][0]["external_id"], "cx-1")
        self.assertEqual(result["sessions"][0]["title"], "请分析项目结构")
        tool_call = next(event for event in result["events"] if event["type"] == "tool_call")
        tool_result = next(event for event in result["events"] if event["type"] == "tool_result")
        self.assertEqual(tool_call["tool_call_id"], "c1")
        self.assertEqual(tool_result["tool_call_id"], "c1")

    def test_codex_event_msg_fallback_without_duplicate(self):
        lines = [
            json.dumps({"type": "session_meta", "payload": {"session_id": "cx-2"}}),
            json.dumps({"type": "response_item", "timestamp": "2026-10-30T12:00:01Z", "payload": {"type": "message", "id": "m1", "role": "user", "content": [{"type": "input_text", "text": "你好"}]}}),
            json.dumps({"type": "event_msg", "timestamp": "2026-10-30T12:00:01Z", "payload": {"type": "item_completed", "item": {"type": "UserMessage", "id": "item-1", "content": [{"type": "text", "text": "你好"}]}}}),
            json.dumps({"type": "event_msg", "timestamp": "2026-10-30T12:00:02Z", "payload": {"type": "item_completed", "item": {"type": "UserMessage", "id": "item-2", "content": [{"type": "text", "text": "只出现在 item 的消息"}]}}}),
        ]
        result = parse_codex_jsonl(lines, "rollout-cx-2.jsonl", "2026-10-30T12:00:00Z")
        user_events = [event for event in result["events"] if event["type"] == "user_message"]
        self.assertEqual(len(user_events), 2)
        self.assertIn("只出现在 item 的消息", [event["content"] for event in user_events])

    def test_claude_tool_use_and_result(self):
        lines = [
            json.dumps({"type": "user", "uuid": "u1", "sessionId": "cc-1", "timestamp": "2026-10-30T10:00:00Z", "message": {"role": "user", "content": [{"type": "text", "text": "读取文件"}]}}),
            json.dumps({"type": "assistant", "uuid": "a1", "sessionId": "cc-1", "timestamp": "2026-10-30T10:00:01Z", "message": {"role": "assistant", "model": "claude", "usage": {"input_tokens": 10, "output_tokens": 5}, "content": [{"type": "text", "text": "我来读取。"}, {"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"file_path": "main.py"}}]}}),
            json.dumps({"type": "user", "uuid": "u2", "sessionId": "cc-1", "timestamp": "2026-10-30T10:00:02Z", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": [{"type": "text", "text": "print('hi')"}]}]}}),
        ]
        result = parse_claude_jsonl(lines, "cc-1.jsonl", agent_key="claudecode")
        types = [event["type"] for event in result["events"]]
        self.assertEqual(types.count("user_message"), 1)
        self.assertEqual(types.count("assistant_message"), 1)
        self.assertEqual(types.count("tool_call"), 1)
        self.assertEqual(types.count("tool_result"), 1)
        result_event = next(event for event in result["events"] if event["type"] == "tool_result")
        self.assertIn("print", result_event["content"])

    def test_workbuddy_user_query_cleanup(self):
        raw = "<system-reminder data-role=\"user-context\">THIS IS NOT THE ASK</system-reminder>\n<user_query>真正的用户请求</user_query>"
        lines = [json.dumps({"type": "message", "role": "user", "id": "wb-u1", "sessionId": "wb-1", "timestamp": 1790000000000, "content": [{"type": "input_text", "text": raw}]})]
        result = parse_claude_jsonl(lines, "wb-1.jsonl", agent_key="workbuddy")
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["content"], "真正的用户请求")

    def test_workbuddy_top_level_tool_events(self):
        lines = [
            json.dumps({"type": "function_call", "id": "fc-1", "sessionId": "wb-2", "timestamp": 1790000000000, "name": "Bash", "callId": "call-9", "arguments": "{\"command\":\"ls\"}", "providerData": {"model": "demo-model"}}),
            json.dumps({"type": "function_call_result", "id": "fr-1", "sessionId": "wb-2", "timestamp": 1790000001000, "name": "Bash", "callId": "call-9", "status": "completed", "output": {"type": "text", "text": "a.txt"}}),
        ]
        result = parse_claude_jsonl(lines, "wb-2.jsonl", agent_key="workbuddy")
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(result["events"][0]["type"], "tool_call")
        self.assertEqual(result["events"][0]["tool_call_id"], "call-9")
        self.assertEqual(result["events"][0]["model"], "demo-model")
        self.assertEqual(result["events"][1]["type"], "tool_result")
        self.assertEqual(result["events"][1]["content"], "a.txt")

    def test_dsh_event_stream(self):
        lines = [
            json.dumps({"type": "session", "id": "dsh-1", "createdAt": 1790000000000, "cwd": "D:/demo"}),
            json.dumps({"type": "user/message", "seq": 1, "time": 1790000001000, "data": {"id": "u1", "role": "user", "source": {"kind": "user"}, "content": [{"type": "text", "text": "实现功能"}]}}),
            json.dumps({"type": "assistant/message", "seq": 2, "time": 1790000002000, "data": {"usage": {"inputTokens": 12, "outputTokens": 7}, "message": {"id": "a1", "role": "assistant", "content": [{"type": "text", "text": "开始实现。"}, {"type": "tool-call", "id": "call-1", "name": "run_code", "arguments": "{\"code\":\"1+1\"}"}]}}}),
            json.dumps({"type": "tool/result", "seq": 3, "time": 1790000003000, "data": {"toolCallId": "call-1", "message": {"id": "r1", "role": "tool", "content": [{"type": "text", "text": "2"}]}}}),
        ]
        result = parse_dsh_jsonl(lines, "sessions/session-dsh-1/session.v4.jsonl", default_session_id="dsh-1")
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual({event["type"] for event in result["events"]}, {"user_message", "assistant_message", "tool_call", "tool_result"})
        call = next(event for event in result["events"] if event["type"] == "tool_call")
        self.assertEqual(call["tool_name"], "run_code")

    def test_generic_json_and_jsonl_fallback(self):
        document = {
            "sessionId": "z-1",
            "title": "ZCode 会话",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi", "tool_calls": [{"id": "c1", "function": {"name": "lookup", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "done"},
            ],
        }
        result = parse_generic_document(json.dumps(document), "z-1.json", agent_key="zcode")
        self.assertEqual({event["type"] for event in result["events"]}, {"user_message", "assistant_message", "tool_call", "tool_result"})
        # A .json file can contain JSONL.
        lines = "\n".join([
            json.dumps({"role": "user", "content": "第一行"}),
            json.dumps({"role": "assistant", "content": "第二行"}),
        ])
        result2 = parse_generic_document(lines, "z-2.json", agent_key="zcode")
        self.assertEqual(len(result2["events"]), 2)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.db"
        init_db(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_auth_and_roles(self):
        with connection(self.db_path) as conn:
            created, password = ensure_default_admin(conn)
            self.assertTrue(created)
            user, error = authenticate(conn, "admin", password, "127.0.0.1")
            self.assertIsNone(error)
            self.assertEqual(user["role"], "admin")
            token = create_web_session(conn, user["id"], "127.0.0.1", "test")
            session_user = resolve_session(conn, token)
            self.assertEqual(session_user["username"], "admin")
            bad, error = authenticate(conn, "admin", "wrong", "127.0.0.1")
            self.assertIsNone(bad)
            self.assertIsNotNone(error)
            viewer, error = create_user(conn, "viewer", "viewer123", "viewer")
            self.assertIsNotNone(viewer)
            self.assertEqual(viewer["role"], "viewer")

    def test_import_overview_and_export(self):
        now = "2026-11-03T01:00:00.000Z"
        batch = {
            "source_path": "demo/test.jsonl",
            "sessions": [{
                "external_id": "s-1", "title": "测试会话", "project": "demo", "cwd": "D:/demo",
                "model": "demo-model", "started_at": now, "ended_at": "2026-11-03T01:05:00.000Z",
                "source_path": "demo/test.jsonl", "metadata": {"demo": True},
            }],
            "events": [
                {"session_external_id": "s-1", "type": "user_message", "source_event_id": "u1", "sequence": 1, "timestamp": now, "content": "你好"},
                {"session_external_id": "s-1", "type": "assistant_message", "source_event_id": "a1", "sequence": 2, "timestamp": now, "content": "你好，我来处理。", "tokens_input": 10, "tokens_output": 4},
                {"session_external_id": "s-1", "type": "tool_call", "source_event_id": "c1", "sequence": 3, "timestamp": now, "content": "", "tool_name": "read_file", "tool_call_id": "call-1", "tool_status": "completed", "tool_input_json": json_dumps({"path": "a.txt"})},
                {"session_external_id": "s-1", "type": "tool_result", "source_event_id": "r1", "sequence": 4, "timestamp": now, "content": "ok", "tool_call_id": "call-1", "tool_status": "success"},
            ],
            "errors": [],
        }
        with connection(self.db_path) as conn:
            first = import_batch(conn, "codex", batch)
            second = import_batch(conn, "codex", batch)
            conn.commit()
            self.assertEqual(first["events_inserted"], 4)
            self.assertEqual(second["events_inserted"], 0)
            self.assertEqual(second["duplicates"], 4)
            sessions = list_sessions(conn)
            self.assertEqual(sessions["total"], 1)
            self.assertEqual(sessions["items"][0]["event_count"], 4)
            self.assertEqual(sessions["items"][0]["tool_call_count"], 1)
            detail = list_session_events(conn, sessions["items"][0]["id"])
            self.assertEqual(len(detail["items"]), 4)
            self.assertEqual(detail["items"][2]["tool_input"], {"path": "a.txt"})
            stats = analytics(conn, start="2026-11-01T00:00:00Z", end="2026-11-04T00:00:00Z")
            self.assertEqual(stats["summary"]["events"], 4)
            self.assertEqual(stats["summary"]["tool_calls"], 1)
            ov = overview(conn)
            self.assertEqual(ov["totals"]["sessions"], 1)
            self.assertEqual(type_counts(conn)["tool_result"], 1)
            exported = create_export(conn, format="csv", agent="codex")
            try:
                content = Path(exported["path"]).read_bytes()
                self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
                self.assertIn("tool_call_id", content.decode("utf-8-sig"))
            finally:
                Path(exported["path"]).unlink(missing_ok=True)
            exported = create_export(conn, format="bundle", agent="codex")
            try:
                with zipfile.ZipFile(exported["path"]) as archive:
                    names = set(archive.namelist())
                    self.assertIn("events.csv", names)
                    self.assertIn("sessions.csv", names)
                    self.assertIn("manifest.json", names)
                    self.assertIn("data_dictionary.md", names)
            finally:
                Path(exported["path"]).unlink(missing_ok=True)

    def test_export_formula_hardening(self):
        batch = {
            "source_path": "demo/formula.jsonl",
            "sessions": [{"external_id": "formula", "title": "=HYPERLINK(\"http://evil\")", "project": "demo"}],
            "events": [{"session_external_id": "formula", "type": "user_message", "source_event_id": "u1", "sequence": 1, "timestamp": "2026-11-03T01:00:00Z", "content": "=SUM(A1:A2)"}],
            "errors": [],
        }
        with connection(self.db_path) as conn:
            import_batch(conn, "codex", batch)
            exported = create_export(conn, format="csv", agent="codex")
            try:
                text = Path(exported["path"]).read_text(encoding="utf-8-sig")
                self.assertIn("'=SUM(A1:A2)", text)
                self.assertIn("'=HYPERLINK", text)
            finally:
                Path(exported["path"]).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
