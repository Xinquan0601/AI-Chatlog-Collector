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
from chatvault.db import (
    PATH_MIGRATION_KEY,
    connection,
    get_settings,
    init_db,
    list_agents,
    migrate_agent_roots,
)
from chatvault.exporting import create_export, estimate_export
from chatvault.ingestion import import_batch, refresh_session_aggregates
from chatvault.parsers import (
    parse_claude_jsonl,
    parse_codex_jsonl,
    parse_dsh_jsonl,
    parse_generic_document,
    parse_generic_jsonl,
    parse_vscode_copilot_jsonl,
)
from chatvault.paths import (
    describe_root,
    expand_user_path,
    normalize_roots,
    to_user_template,
    username,
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


class CopilotParserTests(unittest.TestCase):
    def test_snapshot_patch_replay_and_four_types(self):
        start = 1790000000000
        snapshot = {
            "version": 3,
            "sessionId": "cop-1",
            "creationDate": start,
            "workingDirectory": "D:/demo/app",
            "requests": [],
        }
        lines = [
            json.dumps({"kind": 0, "v": snapshot}),
            json.dumps({
                "kind": 2,
                "k": ["requests"],
                "v": [{
                    "requestId": "r1",
                    "timestamp": start + 1000,
                    "modelId": "gpt-5",
                    "promptTokens": 120,
                    "completionTokens": 30,
                    "message": {"text": "帮我看看 main.py"},
                    "response": [],
                    "responseTimestamp": start + 2000,
                }],
            }),
            json.dumps({
                "kind": 2,
                "k": ["requests", 0, "response"],
                "v": [
                    {"kind": "text", "value": "我先读取文件。"},
                    {
                        "kind": "toolInvocationSerialized",
                        "toolId": "copilot_readFile",
                        "toolCallId": "call-1",
                        "invocationMessage": {"value": "读取 main.py"},
                        "resultDetails": {"value": "print('hi')"},
                        "isComplete": True,
                        "startedAt": start + 2100,
                        "completedAt": start + 2200,
                    },
                ],
            }),
            json.dumps({"kind": 1, "k": ["customTitle"], "v": "Copilot 会话"}),
        ]
        result = parse_vscode_copilot_jsonl(lines, "cop-1.jsonl", "2026-11-03T00:00:00Z", "cop-1")
        self.assertEqual(len(result["sessions"]), 1)
        session = result["sessions"][0]
        self.assertEqual(session["external_id"], "cop-1")
        self.assertEqual(session["title"], "Copilot 会话")
        self.assertEqual(session["project"], "app")
        self.assertEqual(session["model"], "gpt-5")
        self.assertEqual(session["tokens_input"], 120)
        self.assertEqual(session["tokens_output"], 30)
        counts = {}
        for event in result["events"]:
            counts[event["type"]] = counts.get(event["type"], 0) + 1
        self.assertEqual(counts, {
            "user_message": 1,
            "assistant_message": 1,
            "tool_call": 1,
            "tool_result": 1,
        })
        tool_call = next(event for event in result["events"] if event["type"] == "tool_call")
        tool_result = next(event for event in result["events"] if event["type"] == "tool_result")
        self.assertEqual(tool_call["tool_call_id"], "call-1")
        self.assertEqual(tool_result["tool_call_id"], "call-1")
        self.assertIn("读取 main.py", tool_call["tool_input_json"])
        self.assertIn("print", tool_result["content"])
        self.assertEqual(result["errors"], [])

    def test_empty_and_malformed_logs(self):
        result = parse_vscode_copilot_jsonl([json.dumps({"kind": 0, "v": {"requests": []}})], "empty.jsonl", None, "empty")
        self.assertEqual(result["events"], [])
        self.assertEqual(result["sessions"], [])
        broken = parse_vscode_copilot_jsonl(["{not json"], "broken.jsonl", None, "broken")
        self.assertTrue(broken["errors"])
        self.assertEqual(broken["sessions"], [])


class PathTemplateTests(unittest.TestCase):
    def test_windows_and_posix_homes_become_user_template(self):
        home = Path("C:/Users/xinquan")
        self.assertEqual(
            to_user_template("C:/Users/Administrator/.claude/projects", home=home),
            "~/.claude/projects",
        )
        self.assertEqual(
            to_user_template("C:" + chr(92) + "Users" + chr(92) + "Administrator" + chr(92) + "Desktop" + chr(92) + "prj", home=home),
            "~/Desktop/prj",
        )
        self.assertEqual(to_user_template("C:/Users/xinquan", home=home), "~")
        self.assertEqual(
            to_user_template("/home/administrator/.codex/sessions", home=Path("/home/xinquan")),
            "~/.codex/sessions",
        )
        self.assertEqual(
            to_user_template("/Users/Administrator/Library/Application Support", home=Path("/Users/xinquan")),
            "~/Library/Application Support",
        )

    def test_non_user_paths_and_reserved_dirs_are_preserved(self):
        home = Path("C:/Users/xinquan")
        self.assertEqual(to_user_template("C:/Users/Public/Documents", home=home), "C:/Users/Public/Documents")
        self.assertEqual(to_user_template("D:/data/agenttrail", home=home), "D:/data/agenttrail")
        self.assertEqual(to_user_template("~/.zcode/sessions", home=home), "~/.zcode/sessions")
        self.assertEqual(to_user_template("", home=home), "")

    def test_environment_variables_expand_before_templating(self):
        import os
        old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = "C:/Users/xinquan/AppData/Roaming"
        try:
            self.assertEqual(
                to_user_template("%APPDATA%/Code/User/workspaceStorage", home=Path("C:/Users/xinquan")),
                "~/AppData/Roaming/Code/User/workspaceStorage",
            )
        finally:
            if old is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = old

    def test_normalize_roots_deduplicates_and_expands(self):
        roots = normalize_roots([
            "C:/Users/Administrator/.codex/sessions",
            "~/.codex/sessions",
            "~/.codex/sessions/",
            "",
            "  ",
        ], home=Path("C:/Users/xinquan"))
        self.assertEqual(roots, ["~/.codex/sessions"])
        resolved = expand_user_path("~/.codex/sessions", home=Path("C:/Users/xinquan"))
        self.assertEqual(Path(resolved), Path("C:/Users/xinquan/.codex/sessions"))
        info = describe_root("C:/Users/Administrator/.claude/projects", home=Path("C:/Users/xinquan"))
        self.assertEqual(info["template"], "~/.claude/projects")
        self.assertEqual(Path(info["resolved"]), Path("C:/Users/xinquan/.claude/projects"))

    def test_username_is_available(self):
        self.assertTrue(username())


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.db"
        init_db(self.db_path)

    def test_legacy_absolute_roots_are_migrated(self):
        with connection(self.db_path) as conn:
            row = conn.execute("SELECT config_json FROM agents WHERE key = 'codex'").fetchone()
            config = json.loads(row["config_json"])
            config["roots"] = ["C:/Users/Administrator/.codex/sessions"]
            conn.execute(
                "UPDATE agents SET config_json = ? WHERE key = 'codex'",
                (json.dumps(config),),
            )
            conn.execute("DELETE FROM settings WHERE key = ?", (PATH_MIGRATION_KEY,))
            conn.commit()
            result = migrate_agent_roots(conn)
            conn.commit()
            self.assertGreaterEqual(result["agents"], 1)
            migrated = conn.execute("SELECT config_json FROM agents WHERE key = 'codex'").fetchone()
            roots = json.loads(migrated["config_json"])["roots"]
            self.assertIn("~/.codex/sessions", roots)
            self.assertIn("~/.codex/archived_sessions", roots)
            self.assertNotIn("C:/Users/Administrator/.codex/sessions", roots)
            stored = conn.execute("SELECT 1 FROM settings WHERE key = ?", (PATH_MIGRATION_KEY,)).fetchone()
            self.assertIsNotNone(stored)

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

    def test_export_estimate_matches_role_filter(self):
        batch = {
            "source_path": "demo/estimate.jsonl",
            "sessions": [{"external_id": "est", "title": "统计会话", "project": "demo"}],
            "events": [
                {"session_external_id": "est", "type": "user_message", "source_event_id": "u1", "sequence": 1, "timestamp": "2026-11-03T01:00:00Z", "content": "导出我的消息"},
                {"session_external_id": "est", "type": "assistant_message", "source_event_id": "a1", "sequence": 2, "timestamp": "2026-11-03T01:01:00Z", "content": "好的"},
                {"session_external_id": "est", "type": "tool_call", "source_event_id": "c1", "sequence": 3, "timestamp": "2026-11-03T01:02:00Z", "content": "", "tool_name": "read_file", "tool_call_id": "call-1", "tool_input_json": json_dumps({"path": "a.txt"})},
                {"session_external_id": "est", "type": "tool_result", "source_event_id": "r1", "sequence": 4, "timestamp": "2026-11-03T01:03:00Z", "content": "ok", "tool_call_id": "call-1"},
            ],
            "errors": [],
        }
        with connection(self.db_path) as conn:
            import_batch(conn, "codex", batch)
            conn.commit()
            everything = estimate_export(conn)
            self.assertEqual(everything["total"], 4)
            self.assertEqual(everything["sessions"], 1)
            self.assertEqual(sorted(everything["by_type"].values()), [1, 1, 1, 1])
            self.assertGreater(everything["bytes"], 0)

            picked = estimate_export(conn, event_types=["user_message", "tool_call"])
            self.assertEqual(picked["total"], 2)
            self.assertEqual(picked["by_type"], {"user_message": 1, "assistant_message": 0, "tool_call": 1, "tool_result": 0})

            empty = estimate_export(conn, start="2026-12-01T00:00:00Z")
            self.assertEqual(empty["total"], 0)
            self.assertEqual(empty["sessions"], 0)

            exported = create_export(conn, format="csv", event_types=["user_message", "tool_call"], agent="codex")
            try:
                lines = [line for line in Path(exported["path"]).read_text(encoding="utf-8-sig").splitlines() if line]
                self.assertEqual(len(lines), 3)
                self.assertIn("user_message", lines[1] + lines[2])
                self.assertNotIn("assistant_message", lines[1] + lines[2])
            finally:
                Path(exported["path"]).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
