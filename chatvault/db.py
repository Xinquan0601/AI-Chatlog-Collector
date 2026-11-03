from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .constants import (
    AGENT_DEFINITIONS,
    DB_PATH,
    DEFAULT_AGENT_CONFIG,
    DEFAULT_SETTINGS,
)
from .utils import default_roots_for, json_dumps, json_loads, utc_now


SCHEMA = r"""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    vendor TEXT NOT NULL,
    description TEXT NOT NULL,
    color TEXT NOT NULL,
    icon TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    config_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'idle',
    status_message TEXT,
    last_scan_at TEXT,
    last_success_at TEXT,
    last_error_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_key TEXT NOT NULL REFERENCES agents(key) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    project TEXT,
    model TEXT,
    cwd TEXT,
    started_at TEXT,
    ended_at TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    user_message_count INTEGER NOT NULL DEFAULT 0,
    assistant_message_count INTEGER NOT NULL DEFAULT 0,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    tool_result_count INTEGER NOT NULL DEFAULT 0,
    event_count INTEGER NOT NULL DEFAULT 0,
    tokens_input INTEGER NOT NULL DEFAULT 0,
    tokens_output INTEGER NOT NULL DEFAULT 0,
    source_path TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(agent_key, external_id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_key TEXT NOT NULL REFERENCES agents(key) ON DELETE CASCADE,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK(type IN ('user_message','assistant_message','tool_call','tool_result')),
    source_event_id TEXT,
    sequence INTEGER NOT NULL DEFAULT 0,
    timestamp TEXT,
    content TEXT,
    tool_name TEXT,
    tool_call_id TEXT,
    tool_status TEXT,
    tool_input_json TEXT,
    model TEXT,
    tokens_input INTEGER,
    tokens_output INTEGER,
    metadata_json TEXT,
    raw_json TEXT,
    fingerprint TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_agent_time ON sessions(agent_key, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_time ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, timestamp, sequence, id);
CREATE INDEX IF NOT EXISTS idx_events_agent_time ON events(agent_key, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(type, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_tool ON events(tool_name);
CREATE INDEX IF NOT EXISTS idx_events_call ON events(tool_call_id);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_key TEXT NOT NULL REFERENCES agents(key) ON DELETE CASCADE,
    path TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'jsonl',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    mtime_ns INTEGER NOT NULL DEFAULT 0,
    cursor_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT,
    last_synced_at TEXT,
    events_imported INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(agent_key, path)
);
CREATE INDEX IF NOT EXISTS idx_sources_agent ON sources(agent_key, status);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_key TEXT NOT NULL REFERENCES agents(key) ON DELETE CASCADE,
    trigger TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    progress INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    stats_json TEXT,
    error TEXT,
    queued_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_agent ON jobs(agent_key, id DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, id DESC);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer' CHECK(role IN ('admin','viewer')),
    active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    ip TEXT,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS idx_web_sessions_user ON web_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_web_sessions_expiry ON web_sessions(expires_at);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    action TEXT NOT NULL,
    target TEXT,
    detail_json TEXT,
    ip TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);

CREATE TABLE IF NOT EXISTS system_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL DEFAULT 'info',
    module TEXT NOT NULL,
    message TEXT NOT NULL,
    detail_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_created ON system_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_level ON system_logs(level, created_at DESC);

CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ready',
    note TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backups_created ON backups(created_at DESC);
"""


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


@contextmanager
def connection(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | Path | None = None) -> None:
    with connection(db_path) as conn:
        conn.executescript(SCHEMA)
        now = utc_now()
        for definition in AGENT_DEFINITIONS:
            existing = conn.execute("SELECT key FROM agents WHERE key = ?", (definition["key"],)).fetchone()
            if existing:
                continue
            config = dict(DEFAULT_AGENT_CONFIG)
            config["roots"] = default_roots_for(definition)
            config["patterns"] = list(definition.get("patterns") or ["*.jsonl"])
            conn.execute(
                """INSERT INTO agents
                (key, name, vendor, description, color, icon, enabled, config_json,
                 status, status_message, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'idle', '等待首次同步', ?, ?)""",
                (
                    definition["key"], definition["name"], definition["vendor"],
                    definition["description"], definition["color"], definition["icon"],
                    json_dumps(config), now, now,
                ),
            )
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)",
                (key, json_dumps(value), now),
            )


def get_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    result = dict(DEFAULT_SETTINGS)
    for row in conn.execute("SELECT key, value_json FROM settings"):
        result[row["key"]] = json_loads(row["value_json"], DEFAULT_SETTINGS.get(row["key"]))
    return result


def get_setting(conn: sqlite3.Connection, key: str, fallback: Any = None) -> Any:
    row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return DEFAULT_SETTINGS.get(key, fallback)
    return json_loads(row["value_json"], fallback)


def set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        """INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at""",
        (key, json_dumps(value), utc_now()),
    )


def update_settings(conn: sqlite3.Connection, values: dict[str, Any]) -> dict[str, Any]:
    for key, value in values.items():
        if key in DEFAULT_SETTINGS:
            set_setting(conn, key, value)
    return get_settings(conn)


def log_system(
    conn: sqlite3.Connection,
    level: str,
    module: str,
    message: str,
    detail: Any = None,
) -> None:
    conn.execute(
        "INSERT INTO system_logs(level, module, message, detail_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (level, module, message, json_dumps(detail) if detail is not None else None, utc_now()),
    )
    conn.commit()


def log_audit(
    conn: sqlite3.Connection,
    username: str | None,
    action: str,
    target: str | None = None,
    detail: Any = None,
    ip: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_logs(username, action, target, detail_json, ip, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (username, action, target, json_dumps(detail) if detail is not None else None, ip, utc_now()),
    )


def agent_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    config = json_loads(row["config_json"], {})
    return {
        "key": row["key"],
        "name": row["name"],
        "vendor": row["vendor"],
        "description": row["description"],
        "color": row["color"],
        "icon": row["icon"],
        "enabled": bool(row["enabled"]),
        "config": config,
        "roots": config.get("roots", []),
        "patterns": config.get("patterns", []),
        "auto_sync": bool(config.get("auto_sync", True)),
        "interval_minutes": int(config.get("interval_minutes", 30)),
        "max_attempts": int(config.get("max_attempts", 3)),
        "status": row["status"],
        "status_message": row["status_message"],
        "last_scan_at": row["last_scan_at"],
        "last_success_at": row["last_success_at"],
        "last_error_at": row["last_error_at"],
    }


def get_agent(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM agents WHERE key = ?", (key,)).fetchone()
    return agent_row_to_dict(row) if row else None


def list_agents(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM agents ORDER BY rowid").fetchall()
    return [agent_row_to_dict(row) for row in rows]


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    result = {}
    for table in ("sessions", "events", "sources", "jobs", "users", "backups"):
        result[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return result
