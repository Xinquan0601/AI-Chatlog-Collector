from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .constants import PBKDF2_ITERATIONS, SESSION_TTL_SECONDS
from .db import log_audit
from .utils import utc_now


_login_attempts: dict[str, list[float]] = {}
_login_lock = threading.Lock()
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_ATTEMPTS = 8


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    )
    return digest.hex(), salt


def verify_password(password: str, expected_hash: str, salt: str) -> bool:
    try:
        digest, _ = hash_password(password, salt)
        return hmac.compare_digest(digest, expected_hash)
    except (TypeError, ValueError):
        return False


def check_login_rate(ip: str) -> tuple[bool, int]:
    now = time.time()
    with _login_lock:
        attempts = [value for value in _login_attempts.get(ip, []) if now - value < LOGIN_WINDOW_SECONDS]
        _login_attempts[ip] = attempts
        if len(attempts) >= LOGIN_MAX_ATTEMPTS:
            retry_after = max(1, int(LOGIN_WINDOW_SECONDS - (now - attempts[0])))
            return False, retry_after
        return True, 0


def record_login_failure(ip: str) -> None:
    with _login_lock:
        _login_attempts.setdefault(ip, []).append(time.time())


def clear_login_failures(ip: str) -> None:
    with _login_lock:
        _login_attempts.pop(ip, None)


def ensure_default_admin(conn: sqlite3.Connection) -> tuple[bool, str | None]:
    row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    if row and row[0] > 0:
        return False, None
    username = os.environ.get("AGENTTRAIL_ADMIN_USER", "admin")
    password = os.environ.get("AGENTTRAIL_ADMIN_PASSWORD", "admin123")
    password_hash, salt = hash_password(password)
    now = utc_now()
    conn.execute(
        """INSERT INTO users(username, password_hash, salt, role, active, must_change_password, created_at, updated_at)
           VALUES (?, ?, ?, 'admin', 1, 0, ?, ?)""",
        (username, password_hash, salt, now, now),
    )
    conn.commit()
    return True, password


def public_user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "active": bool(row["active"]),
        "must_change_password": bool(row["must_change_password"]),
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
    }


def authenticate(conn: sqlite3.Connection, username: str, password: str, ip: str) -> tuple[dict[str, Any] | None, str | None]:
    row = conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)).fetchone()
    if not row or not row["active"]:
        record_login_failure(ip)
        return None, "用户名或密码错误"
    if not verify_password(password, row["password_hash"], row["salt"]):
        record_login_failure(ip)
        return None, "用户名或密码错误"
    clear_login_failures(ip)
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (utc_now(), row["id"]))
    conn.commit()
    return public_user(row), None


def create_web_session(
    conn: sqlite3.Connection,
    user_id: int,
    ip: str | None,
    user_agent: str | None,
) -> str:
    raw_token = secrets.token_urlsafe(40)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=SESSION_TTL_SECONDS)
    conn.execute(
        """INSERT INTO web_sessions(token_hash, user_id, created_at, expires_at, last_seen_at, ip, user_agent)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            token_hash, user_id,
            now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            expires.isoformat(timespec="seconds").replace("+00:00", "Z"),
            now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            ip, (user_agent or "")[:500],
        ),
    )
    conn.commit()
    return raw_token


def token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def resolve_session(conn: sqlite3.Connection, raw_token: str | None) -> dict[str, Any] | None:
    if not raw_token:
        return None
    row = conn.execute(
        """SELECT ws.token_hash, ws.expires_at, ws.last_seen_at, u.*
           FROM web_sessions ws JOIN users u ON u.id = ws.user_id
           WHERE ws.token_hash = ? AND u.active = 1""",
        (token_hash(raw_token),),
    ).fetchone()
    if not row:
        return None
    now = datetime.now(timezone.utc)
    try:
        expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if expires <= now:
        conn.execute("DELETE FROM web_sessions WHERE token_hash = ?", (row["token_hash"],))
        conn.commit()
        return None
    # Avoid a write on every static request; refresh the sliding marker every 5 minutes.
    try:
        last_seen = datetime.fromisoformat(row["last_seen_at"].replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        last_seen = now - timedelta(minutes=10)
    if now - last_seen > timedelta(minutes=5):
        conn.execute(
            "UPDATE web_sessions SET last_seen_at = ? WHERE token_hash = ?",
            (now.isoformat(timespec="seconds").replace("+00:00", "Z"), row["token_hash"]),
        )
        conn.commit()
    user = public_user(row)
    user["_raw_token_hash"] = row["token_hash"]
    return user


def destroy_session(conn: sqlite3.Connection, raw_token: str | None) -> None:
    if raw_token:
        conn.execute("DELETE FROM web_sessions WHERE token_hash = ?", (token_hash(raw_token),))
        conn.commit()


def cleanup_sessions(conn: sqlite3.Connection) -> int:
    cutoff = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    cursor = conn.execute("DELETE FROM web_sessions WHERE expires_at <= ?", (cutoff,))
    conn.commit()
    return cursor.rowcount


def change_password(conn: sqlite3.Connection, user_id: int, old_password: str, new_password: str) -> tuple[bool, str | None]:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return False, "用户不存在"
    if not verify_password(old_password, row["password_hash"], row["salt"]):
        return False, "当前密码不正确"
    if len(new_password) < 8:
        return False, "新密码至少需要 8 个字符"
    password_hash, salt = hash_password(new_password)
    conn.execute(
        "UPDATE users SET password_hash = ?, salt = ?, must_change_password = 0, updated_at = ? WHERE id = ?",
        (password_hash, salt, utc_now(), user_id),
    )
    conn.commit()
    return True, None


def create_user(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    role: str = "viewer",
    must_change: bool = True,
) -> tuple[dict[str, Any] | None, str | None]:
    username = username.strip()
    if len(username) < 3 or len(username) > 40:
        return None, "用户名长度需为 3–40 个字符"
    if len(password) < 8:
        return None, "密码至少需要 8 个字符"
    if role not in {"admin", "viewer"}:
        return None, "角色无效"
    exists = conn.execute("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
    if exists:
        return None, "用户名已存在"
    password_hash, salt = hash_password(password)
    now = utc_now()
    try:
        cursor = conn.execute(
            """INSERT INTO users(username, password_hash, salt, role, active, must_change_password, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
            (username, password_hash, salt, role, 1 if must_change else 0, now, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return None, "用户名已存在"
    row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return public_user(row), None


def update_user(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    role: str | None = None,
    active: bool | None = None,
    password: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return None, "用户不存在"
    if role is not None:
        if role not in {"admin", "viewer"}:
            return None, "角色无效"
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    if active is not None:
        conn.execute("UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, user_id))
        if not active:
            conn.execute("DELETE FROM web_sessions WHERE user_id = ?", (user_id,))
    if password is not None:
        if len(password) < 8:
            return None, "密码至少需要 8 个字符"
        password_hash, salt = hash_password(password)
        conn.execute(
            "UPDATE users SET password_hash = ?, salt = ?, must_change_password = 1 WHERE id = ?",
            (password_hash, salt, user_id),
        )
    conn.execute("UPDATE users SET updated_at = ? WHERE id = ?", (utc_now(), user_id))
    conn.commit()
    refreshed = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return public_user(refreshed), None


def list_users(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    return [public_user(row) for row in rows]


def count_admins(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1").fetchone()[0]


def log_login(conn: sqlite3.Connection, username: str, ip: str, success: bool, detail: str | None = None) -> None:
    log_audit(
        conn, username if success else username,
        "auth.login" if success else "auth.login_failed",
        target="web",
        detail={"success": success, "message": detail} if detail else {"success": success},
        ip=ip,
    )
    conn.commit()
