from __future__ import annotations

import os
import sys
from pathlib import Path

from . import __version__

APP_NAME = "迹录"
APP_NAME_EN = "AgentTrail"
VERSION = __version__
# Bumped whenever the HTTP contract changes. The web client ships the same
# number, so a half-updated deployment (new page, old process) is reported as
# a version mismatch instead of failing with puzzling 404 / 501 responses.
API_REVISION = 1

ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"
DATA_DIR = Path(os.environ.get("AGENTTRAIL_DATA_DIR", ROOT_DIR / "data")).resolve()
DB_PATH = Path(os.environ.get("AGENTTRAIL_DB", DATA_DIR / "agenttrail.db")).resolve()
BACKUP_DIR = DATA_DIR / "backups"
LOG_DIR = DATA_DIR / "logs"
FIXTURE_DIR = ROOT_DIR / "demo_sources"

HOST = os.environ.get("AGENTTRAIL_HOST", "127.0.0.1")
PORT = int(os.environ.get("AGENTTRAIL_PORT", "8787"))
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
SESSION_COOKIE = "agenttrail_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600
PBKDF2_ITERATIONS = 310_000
MAX_BODY_BYTES = 2 * 1024 * 1024

EVENT_TYPES = {
    "user_message": {"label": "用户消息", "short": "USER", "color": "#2f63c8"},
    "assistant_message": {"label": "助手消息", "short": "ASSISTANT", "color": "#0b7a63"},
    "tool_call": {"label": "工具调用", "short": "TOOL CALL", "color": "#a8720f"},
    "tool_result": {"label": "工具结果", "short": "TOOL RESULT", "color": "#6b4bd6"},
}


def vscode_user_dir() -> str:
    """Return the VS Code user data directory as a portable template."""
    if os.name == "nt":
        return "%APPDATA%/Code/User"
    if sys.platform == "darwin":
        return "~/Library/Application Support/Code/User"
    return "~/.config/Code/User"


def vscode_copilot_roots() -> list[str]:
    base = vscode_user_dir()
    return [
        base + "/workspaceStorage",
        base + "/globalStorage/emptyWindowChatSessions",
    ]


AGENT_DEFINITIONS = [
    {
        "key": "codex",
        "name": "Codex",
        "vendor": "OpenAI",
        "description": "读取 Codex Desktop / CLI 的 rollout JSONL，提取对话、函数调用与输出。",
        "color": "#0e7c66",
        "icon": "terminal",
        "adapter": "codex",
        "default_roots": ["~/.codex/sessions", "~/.codex/archived_sessions"],
        "patterns": ["*.jsonl"],
        "format": "JSONL · rollout",
    },
    {
        "key": "claudecode",
        "name": "Claude Code",
        "vendor": "Anthropic",
        "description": "读取 ~/.claude/projects 下的会话 JSONL，保留 tool_use / tool_result 关联。",
        "color": "#b4622b",
        "icon": "spark",
        "adapter": "claude",
        "default_roots": ["~/.claude/projects"],
        "patterns": ["*.jsonl"],
        "format": "JSONL · projects",
    },
    {
        "key": "opencode",
        "name": "OpenCode",
        "vendor": "OpenCode",
        "description": "以只读方式接入 opencode.db，按消息更新时间做增量抽取。",
        "color": "#4f46e5",
        "icon": "layers",
        "adapter": "opencode",
        "default_roots": [
            "~/.local/share/opencode/opencode.db",
            "~/.local/share/opencode-linux/opencode.db",
        ],
        "patterns": ["opencode.db", "*.db"],
        "format": "SQLite · read-only",
    },
    {
        "key": "workbuddy",
        "name": "WorkBuddy",
        "vendor": "Tencent",
        "description": "读取 .workbuddy/projects 下的项目会话 JSONL，兼容 CodeBuddy 消息格式。",
        "color": "#2f63c8",
        "icon": "briefcase",
        "adapter": "claude",
        "default_roots": ["~/.workbuddy/projects"],
        "patterns": ["*.jsonl"],
        "format": "JSONL · projects",
    },
    {
        "key": "deepseekharness",
        "name": "DeepSeek Harness",
        "vendor": "DeepSeek",
        "description": "解析 DSH v4 会话事件流，支持明文与 zstd 压缩的 JSONL。",
        "color": "#6d8b1d",
        "icon": "orbit",
        "adapter": "dsh",
        "default_roots": ["~/.dsh/sessions"],
        "patterns": ["*.jsonl", "*.jsonl.zstd", "*.zst"],
        "format": "JSONL · zstd",
    },
    {
        "key": "vscodecopilot",
        "name": "VS Code Copilot",
        "vendor": "Microsoft / GitHub",
        "description": "读取 VS Code 工作区聊天会话（workspaceStorage 与 emptyWindowChatSessions），回放 JSONL 快照与增量记录。",
        "color": "#0078d4",
        "icon": "code",
        "adapter": "copilot",
        "default_roots": vscode_copilot_roots(),
        "patterns": ["*.jsonl"],
        "format": "JSONL · chat session",
    },
    {
        "key": "zcode",
        "name": "ZCode",
        "vendor": "ZCode",
        "description": "以只读方式接入 ZCode 的 db.sqlite，并兼容通用 JSON / JSONL 会话目录。",
        "color": "#a03a76",
        "icon": "code",
        "adapter": "zcode",
        "default_roots": ["~/.zcode/cli/db", "~/.zcode/sessions", "~/.zcode/projects"],
        "patterns": ["*.jsonl", "*.json"],
        "format": "SQLite · JSON / JSONL",
    },
]

AGENT_MAP = {item["key"]: item for item in AGENT_DEFINITIONS}

DEFAULT_SETTINGS = {
    "site_name": "迹录 AgentTrail",
    "auto_sync_enabled": True,
    "sync_interval_minutes": 30,
    "auto_retry_attempts": 3,
    "auto_backup_enabled": True,
    "backup_interval_hours": 24,
    "backup_retention": 7,
    "default_page_size": 20,
    "timezone": "Asia/Shanghai",
    "keep_forever": True,
    # 0 = unlimited: an export is the analysis machine's merge source, so it
    # must be able to carry the complete archive.
    "export_max_rows": 0,
    "demo_seeded": False,
}

DEFAULT_AGENT_CONFIG = {
    "roots": [],
    "patterns": [],
    "auto_sync": True,
    "interval_minutes": 30,
    "max_attempts": 3,
    "follow_subagents": True,
}

ALLOWED_SETTINGS = {
    "site_name": str,
    "auto_sync_enabled": bool,
    "sync_interval_minutes": int,
    "auto_retry_attempts": int,
    "auto_backup_enabled": bool,
    "backup_interval_hours": int,
    "backup_retention": int,
    "default_page_size": int,
    "timezone": str,
    "keep_forever": bool,
    "export_max_rows": int,
    "demo_seeded": bool,
}

JOB_STATUSES = ["queued", "running", "success", "partial", "failed", "cancelled"]
