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
from chatvault.importing import preview, run_import
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

    def test_export_multi_agent_and_inclusive_bounds(self):
        codex_batch = {
            "source_path": "demo/codex.jsonl",
            "sessions": [{"external_id": "cx-1", "title": "Codex 会话", "project": "demo"}],
            "events": [
                {"session_external_id": "cx-1", "type": "user_message", "source_event_id": "u1", "sequence": 1, "timestamp": "2026-11-03T01:00:00Z", "content": "codex 起点"},
            ],
            "errors": [],
        }
        zcode_batch = {
            "source_path": "demo/zcode.jsonl",
            "sessions": [{"external_id": "zc-1", "title": "ZCode 会话", "project": "demo"}],
            "events": [
                {"session_external_id": "zc-1", "type": "user_message", "source_event_id": "u1", "sequence": 1, "timestamp": "2026-11-03T01:30:00Z", "content": "zcode 提问"},
                {"session_external_id": "zc-1", "type": "assistant_message", "source_event_id": "a1", "sequence": 2, "timestamp": "2026-11-03T02:00:00Z", "content": "zcode 结束点"},
            ],
            "errors": [],
        }
        with connection(self.db_path) as conn:
            import_batch(conn, "codex", codex_batch)
            import_batch(conn, "zcode", zcode_batch)
            conn.commit()

            single = estimate_export(conn, agents=["codex"])
            self.assertEqual(single["total"], 1)
            self.assertEqual(single["agents"], ["codex"])

            combined = estimate_export(conn, agents=["codex", "zcode"])
            self.assertEqual(combined["total"], 3)
            self.assertEqual(sorted(combined["by_type"].values()), [0, 0, 1, 2])

            everything = estimate_export(conn)
            self.assertEqual(everything["total"], 3)
            self.assertEqual(everything["agents"], [])
            self.assertEqual({item["agent_key"] for item in everything["by_agent"]}, {"codex", "zcode"})
            self.assertEqual(sum(item["events"] for item in everything["by_agent"]), 3)

            # Both the start and the end instant are part of the closed interval.
            closed = estimate_export(conn, start="2026-11-03T01:00:00Z", end="2026-11-03T02:00:00Z")
            self.assertEqual(closed["total"], 3)
            just_before = estimate_export(conn, start="2026-11-03T01:00:00Z", end="2026-11-03T01:59:59.999Z")
            self.assertEqual(just_before["total"], 2)

            exported = create_export(conn, format="csv", agents=["codex", "zcode"])
            try:
                lines = [line for line in Path(exported["path"]).read_text(encoding="utf-8-sig").splitlines() if line]
                self.assertEqual(len(lines), 4)
                self.assertIn("codex", lines[1])
                self.assertIn("zcode", lines[2] + lines[3])
                self.assertEqual(exported["manifest"]["filters"]["agents"], ["codex", "zcode"])
            finally:
                Path(exported["path"]).unlink(missing_ok=True)


LEGACY_EXPORT_SCHEMA = """
CREATE TABLE export_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE sessions (
    session_pk INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    tool TEXT,
    session_id TEXT NOT NULL,
    parent_session_id TEXT,
    root_session_id TEXT,
    is_subagent INTEGER NOT NULL DEFAULT 0,
    project_path TEXT,
    project_path_raw TEXT,
    title TEXT,
    display_title TEXT,
    model TEXT,
    start_ms INTEGER,
    end_ms INTEGER,
    archived_ms INTEGER,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    msg_count INTEGER DEFAULT 0,
    src_msg_count INTEGER DEFAULT 0,
    user_msg_count INTEGER DEFAULT 0,
    meta_json TEXT
);
CREATE TABLE messages (
    row_id INTEGER PRIMARY KEY,
    session_pk INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    is_reasoning INTEGER NOT NULL DEFAULT 0,
    timestamp_ms INTEGER,
    model TEXT,
    text TEXT,
    tool TEXT,
    tool_status TEXT,
    tool_input TEXT,
    tool_output TEXT,
    msg_uid TEXT
);
"""


class LegacyImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.export_path = Path(self.temp.name) / "chatlog-export.db"
        self.db_path = Path(self.temp.name) / "agenttrail.db"
        self._make_export()
        init_db(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def _make_export(self):
        bs = chr(92)
        project_raw = "C:" + bs + "Users" + bs + "Administrator" + bs + "Desktop" + bs + "proj-a"
        conn = sqlite3.connect(self.export_path)
        try:
            conn.executescript(LEGACY_EXPORT_SCHEMA)
            conn.executemany(
                "INSERT INTO export_meta(key, value) VALUES (?, ?)",
                [("exported_at", "2026-11-03T12:00:00Z"), ("tool_version", "2.4.0"), ("filters_json", '{"include_reasoning": false}')],
            )
            conn.executemany(
                "INSERT INTO sessions(session_pk, source, tool, session_id, project_path, project_path_raw,"
                " title, display_title, model, start_ms, end_ms, input_tokens, output_tokens, msg_count,"
                " user_msg_count, is_subagent, meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (1, "codex", "codex", "sess-codex", "C:/Users/Administrator/Desktop/proj-a", project_raw,
                     None, None, "gpt-5", 1786000000000, 1786000060000, 1200, 340, 5, 1, 0, "{}"),
                    (2, "opencode-local", "opencode", "ses_abc", "C:/Users/Administrator/Desktop/proj-b",
                     "C:/Users/Administrator/Desktop/proj-b", None, "继续排查", "gpt-5", 1786000100000,
                     1786000120000, 10, 5, 2, 1, 0, None),
                    (3, "cursor", "cursor", "cursor-1", None, None, None, "未知来源", "x", 1786000200000,
                     1786000210000, 0, 0, 1, 1, 0, None),
                ],
            )
            conn.executemany(
                "INSERT INTO messages(row_id, session_pk, seq, role, is_reasoning, timestamp_ms, model,"
                " text, tool, tool_status, tool_input, tool_output, msg_uid)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (1, 1, 1, "user", 0, 1786000000000, None, "帮我看看这个 bug", None, None, None, None, "u1"),
                    (2, 1, 2, "assistant", 1, 1786000010000, "gpt-5", "思考中", None, None, None, None, "r1"),
                    (3, 1, 3, "assistant", 0, 1786000020000, "gpt-5", "好的，我先列一下目录", None, None, None, None, "a1"),
                    (4, 1, 4, "tool_call", 0, 1786000030000, None, None, "exec_command", None, '{"cmd": "ls"}', None, "c1"),
                    (5, 1, 5, "tool_result", 0, 1786000040000, None, None, None, "ok", None, "a.txt", "t1"),
                    (6, 2, 1, "tool_result", 0, 1786000100000, None, None, None, "ok", None, "orphan output", "t2"),
                    (7, 2, 2, "user", 0, 1786000110000, None, "继续", None, None, None, None, "u2"),
                    (8, 3, 1, "user", 0, 1786000200000, None, "未知来源的消息", None, None, None, None, "u3"),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def test_preview_maps_old_sources(self):
        info = preview(self.export_path)
        self.assertEqual(info["totals"]["sessions"], 3)
        self.assertEqual(info["totals"]["messages"], 8)
        self.assertEqual(info["totals"]["reasoning"], 1)
        self.assertEqual(info["totals"]["tool_calls"], 1)
        self.assertEqual(info["totals"]["tool_results"], 2)
        by_source = {item["source"]: item for item in info["sources"]}
        self.assertEqual(by_source["codex"]["agent_key"], "codex")
        self.assertEqual(by_source["opencode-local"]["agent_key"], "opencode")
        self.assertFalse(by_source["cursor"]["supported"])
        self.assertTrue(info["warnings"])
        self.assertEqual(info["tool_version"], "2.4.0")

    def test_import_session_events_and_tool_pairing(self):
        result = run_import(self.export_path, db_path=self.db_path)
        self.assertEqual(result["sessions_imported"], 2)
        self.assertEqual(result["sessions_skipped"], 1)
        self.assertEqual(result["events_inserted"], 6)
        self.assertEqual(result["skipped_reasoning"], 1)
        self.assertEqual(result["duplicates"], 0)
        self.assertFalse(result["errors"])
        self.assertTrue(any("cursor" in item for item in result["warnings"]))

        with connection(self.db_path) as conn:
            row = conn.execute("SELECT * FROM sessions WHERE external_id = ?", ("sess-codex",)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["title"], "帮我看看这个 bug")
            self.assertEqual(row["project"], "proj-a")
            self.assertEqual(row["cwd"], "C:" + chr(92) + "Users" + chr(92) + "Administrator" + chr(92) + "Desktop" + chr(92) + "proj-a")
            self.assertEqual(row["tokens_input"], 1200)
            meta = json.loads(row["metadata_json"])
            self.assertEqual(meta["imported_from"], "chatlog")
            self.assertEqual(meta["legacy_source"], "codex")
            self.assertFalse(meta["is_subagent"])

            types = [item["type"] for item in conn.execute(
                "SELECT type FROM events WHERE session_id = ? ORDER BY sequence", (row["id"],)
            )]
            self.assertEqual(types, ["user_message", "assistant_message", "tool_call", "tool_result"])

            call = conn.execute(
                "SELECT * FROM events WHERE session_id = ? AND type = 'tool_call'", (row["id"],)
            ).fetchone()
            outcome = conn.execute(
                "SELECT * FROM events WHERE session_id = ? AND type = 'tool_result'", (row["id"],)
            ).fetchone()
            self.assertEqual(call["tool_name"], "exec_command")
            self.assertEqual(json.loads(call["tool_input_json"]), {"cmd": "ls"})
            self.assertEqual(outcome["content"], "a.txt")
            self.assertEqual(outcome["tool_call_id"], call["tool_call_id"])
            self.assertEqual(outcome["tool_status"], "success")

            other = conn.execute("SELECT * FROM sessions WHERE agent_key = 'opencode'").fetchone()
            self.assertEqual(other["external_id"], "opencode-local:ses_abc")
            orphan = conn.execute(
                "SELECT * FROM events WHERE session_id = ? AND type = 'tool_result'", (other["id"],)
            ).fetchone()
            self.assertIsNone(orphan["tool_call_id"])

    def test_import_is_idempotent(self):
        first = run_import(self.export_path, db_path=self.db_path)
        second = run_import(self.export_path, db_path=self.db_path)
        self.assertEqual(second["events_inserted"], 0)
        self.assertEqual(second["duplicates"], first["events_inserted"])
        with connection(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        self.assertEqual(count, first["events_inserted"])
        self.assertEqual(sessions, 2)

    def test_reasoning_can_be_included(self):
        result = run_import(self.export_path, include_reasoning=True, db_path=self.db_path)
        self.assertEqual(result["skipped_reasoning"], 0)
        with connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE content = ? AND type = 'assistant_message'", ("思考中",)
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(json.loads(row["metadata_json"])["reasoning"], True)


if __name__ == "__main__":
    unittest.main()
