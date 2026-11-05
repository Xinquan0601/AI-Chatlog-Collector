from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import __version__
from .analytics import (
    analytics,
    get_event,
    get_session,
    global_search,
    list_agents_with_stats,
    list_audit_logs,
    list_jobs,
    list_logs,
    list_session_events,
    list_sessions,
    list_sources,
    overview,
    resolve_time_range,
    type_counts,
)
from .auth import (
    authenticate,
    change_password,
    check_login_rate,
    cleanup_sessions,
    count_admins,
    create_user,
    create_web_session,
    destroy_session,
    ensure_default_admin,
    list_users,
    log_login,
    resolve_session,
    update_user,
)
from .backup import backup_stats, create_backup, delete_backup, get_backup, list_backups
from .constants import (
    AGENT_MAP,
    API_REVISION,
    APP_NAME,
    APP_NAME_EN,
    BACKUP_DIR,
    DB_PATH,
    DEFAULT_SETTINGS,
    EVENT_TYPES,
    HOST,
    MAX_BODY_BYTES,
    MAX_PAGE_SIZE,
    PORT,
    ROOT_DIR,
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    VERSION,
    WEB_DIR,
)
from .db import (
    agent_row_to_dict,
    connection,
    get_agent,
    get_settings,
    init_db,
    list_agents,
    log_audit,
    log_system,
    set_setting,
    table_counts,
    update_settings,
)
from .exporting import ExportTooLarge, create_export, estimate_export
from .ingestion import reset_source
from .importing import (
    ImportSourceError,
    candidate_exports,
    get_import_manager,
    get_run,
    list_runs,
    preview as preview_import,
    recover_stale_runs,
)
from .jobs import get_job_manager, init_job_manager
from .paths import normalize_roots, profile_dir, username
from .utils import json_dumps, json_loads, utc_now


MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".map": "application/json; charset=utf-8",
}

CACHEABLE_SUFFIXES = {".png", ".webp", ".ico", ".woff2"}
NO_CACHE_SUFFIXES = {".html", ".js", ".mjs", ".css", ".svg", ".json", ".map"}

# The web client is read from disk on every request, but Python modules are
# loaded once at startup.  Updating the checkout while the server keeps
# running therefore serves a new front end on top of an old back end, which
# shows up as confusing 404 / 501 responses; this timestamp lets /api/health
# report that state so the UI can ask for a restart.
PROCESS_STARTED_AT = time.time()
_SOURCE_SCAN = {"checked_at": 0.0, "stale": False}


def source_paths() -> list[Path]:
    return [ROOT_DIR / "app.py", *sorted((ROOT_DIR / "chatvault").glob("*.py"))]


def running_code_stale() -> bool:
    """Return True when Python files on disk are newer than this process."""
    now = time.time()
    if now - _SOURCE_SCAN["checked_at"] < 5.0:
        return _SOURCE_SCAN["stale"]
    newest = 0.0
    for path in source_paths():
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    _SOURCE_SCAN["checked_at"] = now
    _SOURCE_SCAN["stale"] = newest > PROCESS_STARTED_AT + 1.0
    return _SOURCE_SCAN["stale"]


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'"
    ),
}


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class AgentTrailServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], handler: type[BaseHTTPRequestHandler], db_path: str | Path | None = None):
        self.db_path = db_path
        super().__init__(server_address, handler)


class RequestHandler(BaseHTTPRequestHandler):
    server_version = f"AgentTrail/{VERSION}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ utils
    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(f"[{datetime.now().strftime('%H:%M:%S')}] {self.address_string()} {format % args}\n")

    @property
    def db_path(self) -> str | Path | None:
        return getattr(self.server, "db_path", None)

    def _json_body(self) -> dict[str, Any]:
        length_header = self.headers.get("Content-Length")
        try:
            length = int(length_header or 0)
        except ValueError:
            raise APIError(400, "bad_request", "Content-Length 无效")
        if length > MAX_BODY_BYTES:
            raise APIError(413, "too_large", "请求体过大")
        if length == 0:
            return {}
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise APIError(415, "unsupported_media_type", "请求体必须是 application/json")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise APIError(400, "bad_request", "JSON 格式无效")
        if not isinstance(data, dict):
            raise APIError(400, "bad_request", "请求体必须是 JSON 对象")
        return data

    def _query(self, name: str, default: str | None = None) -> str | None:
        values = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        items = values.get(name)
        return items[0] if items else default

    def _query_int(self, name: str, default: int, minimum: int = 1, maximum: int = 10**9) -> int:
        raw = self._query(name)
        if raw is None or raw == "":
            return default
        try:
            number = int(raw)
        except ValueError:
            raise APIError(400, "bad_request", f"{name} 必须是整数")
        return max(minimum, min(maximum, number))

    def _cookie_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            cookie = SimpleCookie()
            cookie.load(raw)
            morsel = cookie.get(SESSION_COOKIE)
            return morsel.value if morsel else None
        except Exception:
            return None

    def _set_cookie(self, token: str | None) -> None:
        if token:
            cookie = (
                f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL_SECONDS}"
            )
            if os.environ.get("AGENTTRAIL_SECURE_COOKIE") == "1":
                cookie += "; Secure"
        else:
            cookie = f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
        self._extra_headers.append(("Set-Cookie", cookie))

    def _send_json(self, data: Any, status: int = 200, *, ok: bool = True) -> None:
        payload = {"ok": ok, "data": data} if ok else {"ok": False, "error": data}
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        for key, value in self._extra_headers:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, code: str, message: str) -> None:
        self._send_json({"code": code, "message": message}, status, ok=False)

    def _send_file(self, path: Path, filename: str, content_type: str) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            self._error(404, "not_found", "文件不存在")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header(
            "Content-Disposition",
            f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}",
        )
        self.send_header("Cache-Control", "no-store")
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        for key, value in self._extra_headers:
            self.send_header(key, value)
        self.end_headers()
        try:
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(128 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _audit(self, conn: sqlite3.Connection, action: str, target: str | None = None, detail: Any = None) -> None:
        username = self.user.get("username") if self.user else None
        log_audit(conn, username, action, target, detail, self.client_address[0])
        conn.commit()

    def _require_user(self, conn: sqlite3.Connection, admin: bool = False) -> dict[str, Any]:
        token = self._cookie_token()
        user = resolve_session(conn, token)
        if not user:
            raise APIError(401, "unauthorized", "登录已过期，请重新登录")
        user = {key: value for key, value in user.items() if not key.startswith("_")}
        self.user = user
        if admin and user["role"] != "admin":
            raise APIError(403, "forbidden", "需要管理员权限")
        return user

    # ------------------------------------------------------------------ HTTP verbs
    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        self.user: dict[str, Any] | None = None
        self._extra_headers: list[tuple[str, str]] = []
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        try:
            if path.startswith("/api/"):
                if method != "GET":
                    self._check_request_forgery()
                with connection(self.db_path) as conn:
                    self._handle_api(conn, method, path)
                return
            if method == "GET":
                self._serve_static(path)
            else:
                self._error(405, "method_not_allowed", "Method not allowed")
        except APIError as exc:
            self._error(exc.status, exc.code, exc.message)
        except BrokenPipeError:
            pass
        except Exception as exc:
            # Keep details in the system log, not in the HTTP response.
            try:
                with connection(self.db_path) as conn:
                    log_system(conn, "error", "server", "未处理的服务端异常", {
                        "path": path, "method": method, "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc()[-5000:],
                    })
            except Exception:
                pass
            self.log_message("unhandled error: %s", traceback.format_exc())
            try:
                self._error(500, "internal_error", "服务器内部错误，请查看系统日志")
            except Exception:
                pass

    def _check_request_forgery(self) -> None:
        if self.headers.get("X-AgentTrail-Request") != "1":
            raise APIError(403, "csrf", "缺少请求校验头")
        origin = self.headers.get("Origin")
        if origin:
            host = self.headers.get("Host", "")
            try:
                origin_host = urlsplit(origin).netloc
            except Exception:
                origin_host = ""
            if origin_host and host and origin_host.lower() != host.lower():
                raise APIError(403, "csrf", "请求来源与当前站点不一致")

    # ------------------------------------------------------------------ static files
    def _serve_static(self, path: str) -> None:
        if path in {"/", "/index.html", "/login"}:
            target = WEB_DIR / "index.html"
        elif path == "/favicon.ico":
            target = WEB_DIR / "assets" / "favicon.svg"
        else:
            relative = path.lstrip("/")
            target = (WEB_DIR / relative).resolve()
            web_root = WEB_DIR.resolve()
            if not str(target).startswith(str(web_root) + os.sep) and target != web_root:
                self._error(404, "not_found", "Not found")
                return
        if not target.is_file():
            self._error(404, "not_found", "Not found")
            return
        content_type = MIME_TYPES.get(target.suffix.lower(), "application/octet-stream")
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if target.suffix.lower() in NO_CACHE_SUFFIXES:
            cache_control = "no-cache"
        elif target.suffix.lower() in CACHEABLE_SUFFIXES:
            cache_control = "public, max-age=86400"
        else:
            cache_control = "public, max-age=300"
        self.send_header("Cache-Control", cache_control)
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        for key, value in self._extra_headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------ API routing
    def _handle_api(self, conn: sqlite3.Connection, method: str, path: str) -> None:
        path = path.rstrip("/") or "/api"
        now = utc_now()
        if path == "/api/health" and method == "GET":
            user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            self._send_json({
                "name": APP_NAME, "name_en": APP_NAME_EN, "version": VERSION,
                "api_revision": API_REVISION, "stale": running_code_stale(),
                "status": "ok", "time": now, "initialized": user_count > 0,
            })
            return

        if path == "/api/auth/login" and method == "POST":
            self._login(conn)
            return

        if path == "/api/auth/status" and method == "GET":
            status_user = resolve_session(conn, self._cookie_token())
            if status_user:
                status_user = {key: value for key, value in status_user.items() if not key.startswith("_")}
            self._send_json({
                "authenticated": bool(status_user),
                "user": status_user,
            })
            return

        token = self._cookie_token()
        if not token:
            raise APIError(401, "unauthorized", "请先登录")
        user = resolve_session(conn, token)
        if not user:
            raise APIError(401, "unauthorized", "登录已过期，请重新登录")
        user = {key: value for key, value in user.items() if not key.startswith("_")}
        self.user = user

        if path == "/api/auth/me" and method == "GET":
            self._send_json({"user": user})
            return
        if path == "/api/auth/logout" and method == "POST":
            destroy_session(conn, token)
            self._set_cookie(None)
            self._audit(conn, "auth.logout", "web")
            self._send_json({"logged_out": True})
            return
        if path == "/api/auth/change-password" and method == "POST":
            self._change_password(conn)
            return

        if path == "/api/bootstrap" and method == "GET":
            self._bootstrap(conn)
            return
        if path == "/api/overview" and method == "GET":
            days = self._query_int("days", 14, 7, 60)
            self._send_json(overview(conn, days))
            return
        if path == "/api/meta" and method == "GET":
            self._send_json({
                "agents": list_agents_with_stats(conn),
                "event_types": EVENT_TYPES,
                "version": VERSION,
                "user": {"name": username(), "home": profile_dir(), "template": "~"},
            })
            return
        if path == "/api/agents" and method == "GET":
            agents = list_agents_with_stats(conn)
            active_jobs = {
                row["agent_key"]: dict(row) for row in conn.execute(
                    "SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY id DESC"
                )
            }
            for agent in agents:
                agent["active_job"] = active_jobs.get(agent["key"])
            self._send_json({"items": agents})
            return

        match = re.fullmatch(r"/api/agents/([a-zA-Z0-9_-]+)", path)
        if match and method == "PATCH":
            self._update_agent(conn, match.group(1))
            return
        match = re.fullmatch(r"/api/agents/([a-zA-Z0-9_-]+)/sync", path)
        if match and method == "POST":
            self._sync_agent(conn, match.group(1))
            return
        match = re.fullmatch(r"/api/agents/([a-zA-Z0-9_-]+)/reset", path)
        if match and method == "POST":
            self._reset_agent(conn, match.group(1))
            return
        if path == "/api/agents/sync-all" and method == "POST":
            self._sync_all(conn)
            return

        if path == "/api/sessions" and method == "GET":
            self._sessions(conn)
            return
        match = re.fullmatch(r"/api/sessions/(\d+)", path)
        if match and method == "GET":
            self._session_detail(conn, int(match.group(1)))
            return
        match = re.fullmatch(r"/api/events/(\d+)", path)
        if match and method == "GET":
            self._event_detail(conn, int(match.group(1)))
            return
        if path == "/api/search" and method == "GET":
            q = self._query("q", "") or ""
            self._send_json(global_search(conn, q, self._query_int("limit", 8, 1, 30)))
            return
        if path == "/api/stats" and method == "GET":
            self._stats(conn)
            return
        if path == "/api/tasks" and method == "GET":
            self._tasks(conn)
            return
        match = re.fullmatch(r"/api/tasks/(\d+)/retry", path)
        if match and method == "POST":
            self._retry_task(conn, int(match.group(1)))
            return
        match = re.fullmatch(r"/api/tasks/(\d+)", path)
        if match and method == "GET":
            self._task_detail(conn, int(match.group(1)))
            return

        if path == "/api/export" and method == "GET":
            self._export(conn)
            return
        if path == "/api/export/estimate" and method == "GET":
            self._export_estimate(conn)
            return
        if path == "/api/backups" and method == "GET":
            self._require_role("admin")
            self._send_json({
                "items": list_backups(conn),
                "stats": backup_stats(conn),
                "config": {
                    "auto_backup_enabled": get_settings(conn).get("auto_backup_enabled"),
                    "backup_interval_hours": get_settings(conn).get("backup_interval_hours"),
                    "backup_retention": get_settings(conn).get("backup_retention"),
                },
            })
            return
        if path == "/api/backups" and method == "POST":
            self._require_role("admin")
            self._create_backup(conn)
            return
        match = re.fullmatch(r"/api/backups/(\d+)/download", path)
        if match and method == "GET":
            self._require_role("admin")
            self._download_backup(conn, int(match.group(1)))
            return
        match = re.fullmatch(r"/api/backups/(\d+)", path)
        if match and method == "DELETE":
            self._require_role("admin")
            self._delete_backup(conn, int(match.group(1)))
            return

        if path == "/api/import/candidates" and method == "GET":
            self._require_role("admin")
            self._send_json({"items": candidate_exports(self.db_path)})
            return
        if path == "/api/import/preview" and method == "GET":
            self._require_role("admin")
            self._import_preview(conn)
            return
        if path == "/api/import/runs" and method == "GET":
            self._require_role("admin")
            self._send_json({
                "items": list_runs(conn, self._query_int("limit", 20, 1, 100)),
                "active_run_id": get_import_manager().active_run_id(),
            })
            return
        if path == "/api/import/runs" and method == "POST":
            self._require_role("admin")
            self._import_start(conn)
            return
        match = re.fullmatch(r"/api/import/runs/(\d+)", path)
        if match and method == "GET":
            self._require_role("admin")
            item = get_run(conn, int(match.group(1)))
            if not item:
                raise APIError(404, "not_found", "导入记录不存在")
            self._send_json({"item": item, "active_run_id": get_import_manager().active_run_id()})
            return
        match = re.fullmatch(r"/api/import/runs/(\d+)/cancel", path)
        if match and method == "POST":
            self._require_role("admin")
            run_id = int(match.group(1))
            if not get_import_manager().cancel(run_id):
                raise APIError(409, "conflict", "该导入任务不在运行中")
            self._audit(conn, "import.cancel", f"import_run:{run_id}")
            self._send_json({"canceled": True})
            return

        if path == "/api/settings" and method == "GET":
            self._require_role("admin")
            self._settings(conn)
            return
        if path == "/api/settings" and method == "PATCH":
            self._require_role("admin")
            self._update_settings(conn)
            return
        if path == "/api/users" and method == "GET":
            self._require_role("admin")
            self._send_json({"items": list_users(conn)})
            return
        if path == "/api/users" and method == "POST":
            self._require_role("admin")
            self._create_user(conn)
            return
        match = re.fullmatch(r"/api/users/(\d+)", path)
        if match and method == "PATCH":
            self._require_role("admin")
            self._update_user(conn, int(match.group(1)))
            return
        if path == "/api/logs" and method == "GET":
            self._require_role("admin")
            self._send_json(list_logs(
                conn,
                level=self._query("level"),
                module=self._query("module"),
                page=self._query_int("page", 1),
                page_size=self._query_int("page_size", 30, 1, MAX_PAGE_SIZE),
            ))
            return
        if path == "/api/audit-logs" and method == "GET":
            self._require_role("admin")
            self._send_json(list_audit_logs(
                conn,
                page=self._query_int("page", 1),
                page_size=self._query_int("page_size", 30, 1, MAX_PAGE_SIZE),
            ))
            return
        if path == "/api/sources" and method == "GET":
            self._require_role("admin")
            self._send_json(list_sources(
                conn,
                agent=self._query("agent"),
                page=self._query_int("page", 1),
                page_size=self._query_int("page_size", 20, 1, MAX_PAGE_SIZE),
            ))
            return
        if path == "/api/system" and method == "GET":
            self._require_role("admin")
            self._system_info(conn)
            return

        raise APIError(404, "not_found", f"未知接口: {method} {path}")

    # ------------------------------------------------------------------ auth
    def _login(self, conn: sqlite3.Connection) -> None:
        body = self._json_body()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if not username or not password:
            raise APIError(400, "bad_request", "请输入用户名和密码")
        ip = self.client_address[0]
        allowed, retry_after = check_login_rate(ip)
        if not allowed:
            raise APIError(429, "rate_limited", f"尝试次数过多，请 {retry_after} 秒后再试")
        user, error = authenticate(conn, username, password, ip)
        log_login(conn, username, ip, user is not None, error)
        if not user:
            raise APIError(401, "invalid_credentials", error or "用户名或密码错误")
        token = create_web_session(conn, user["id"], ip, self.headers.get("User-Agent"))
        self._set_cookie(token)
        self.user = user
        self._audit(conn, "auth.login", "web", {"username": username})
        self.log_message("login success: %s", username)
        self._send_json({"user": user})

    def _change_password(self, conn: sqlite3.Connection) -> None:
        body = self._json_body()
        ok, error = change_password(
            conn, int(self.user["id"]),
            str(body.get("current_password") or ""),
            str(body.get("new_password") or ""),
        )
        if not ok:
            raise APIError(400, "bad_request", error or "修改密码失败")
        token = self._cookie_token()
        if token:
            # Keep the current session but invalidate other sessions.
            from .auth import token_hash
            conn.execute(
                "DELETE FROM web_sessions WHERE user_id = ? AND token_hash != ?",
                (self.user["id"], token_hash(token)),
            )
            conn.commit()
        self._audit(conn, "auth.change_password", f"user:{self.user['id']}")
        self._send_json({"changed": True, "must_change_password": False})

    def _require_role(self, role: str) -> None:
        if not self.user or self.user.get("role") != role:
            raise APIError(403, "forbidden", "需要管理员权限")

    # ------------------------------------------------------------------ bootstrap / agents
    def _bootstrap(self, conn: sqlite3.Connection) -> None:
        settings = get_settings(conn)
        public_settings = {
            key: settings.get(key) for key in (
                "site_name", "auto_sync_enabled", "sync_interval_minutes",
                "default_page_size", "timezone", "auto_backup_enabled",
            )
        }
        agents = list_agents_with_stats(conn)
        self._send_json({
            "user": self.user,
            "permissions": {"admin": self.user["role"] == "admin"},
            "app": {"name": APP_NAME, "name_en": APP_NAME_EN, "version": VERSION},
            "settings": public_settings,
            "agents": agents,
            "event_types": EVENT_TYPES,
            "server_time": utc_now(),
        })

    def _update_agent(self, conn: sqlite3.Connection, key: str) -> None:
        self._require_role("admin")
        if key not in AGENT_MAP:
            raise APIError(404, "not_found", "未知 Agent")
        agent = get_agent(conn, key)
        if not agent:
            raise APIError(404, "not_found", "未知 Agent")
        body = self._json_body()
        config = dict(agent["config"] or {})
        if "roots" in body:
            roots = body["roots"]
            if isinstance(roots, str):
                roots = [line.strip() for line in roots.splitlines() if line.strip()]
            if not isinstance(roots, list) or any(not isinstance(item, str) for item in roots):
                raise APIError(400, "bad_request", "roots 必须是字符串数组")
            roots = normalize_roots([item.strip() for item in roots if item.strip()])
            if len(roots) > 20:
                raise APIError(400, "bad_request", "最多配置 20 个数据源路径")
            config["roots"] = roots
        if "patterns" in body:
            patterns = body["patterns"]
            if isinstance(patterns, str):
                patterns = [line.strip() for line in patterns.splitlines() if line.strip()]
            if not isinstance(patterns, list) or any(not isinstance(item, str) for item in patterns):
                raise APIError(400, "bad_request", "patterns 必须是字符串数组")
            config["patterns"] = [item.strip() for item in patterns if item.strip()][:20]
        for field in ("auto_sync", "follow_subagents"):
            if field in body:
                config[field] = bool(body[field])
        if "interval_minutes" in body:
            try:
                value = int(body["interval_minutes"])
            except (TypeError, ValueError):
                raise APIError(400, "bad_request", "interval_minutes 必须是整数")
            config["interval_minutes"] = max(1, min(1440, value))
        if "max_attempts" in body:
            try:
                value = int(body["max_attempts"])
            except (TypeError, ValueError):
                raise APIError(400, "bad_request", "max_attempts 必须是整数")
            config["max_attempts"] = max(1, min(10, value))
        enabled = agent["enabled"]
        if "enabled" in body:
            enabled = bool(body["enabled"])
        now = utc_now()
        status = "disabled" if not enabled else ("idle" if agent["status"] == "disabled" else agent["status"])
        status_message = "已停用" if not enabled else ("等待首次同步" if status == "idle" else agent["status_message"])
        conn.execute(
            """UPDATE agents SET enabled = ?, config_json = ?, status = ?, status_message = ?, updated_at = ?
               WHERE key = ?""",
            (1 if enabled else 0, json_dumps(config), status, status_message, now, key),
        )
        conn.commit()
        self._audit(conn, "agent.update", f"agent:{key}", {"enabled": enabled, "roots": config.get("roots"), "patterns": config.get("patterns")})
        updated = get_agent(conn, key)
        self._send_json({"item": updated})

    def _sync_agent(self, conn: sqlite3.Connection, key: str) -> None:
        self._require_role("admin")
        if key not in AGENT_MAP:
            raise APIError(404, "not_found", "未知 Agent")
        job_id, notice = get_job_manager().enqueue(key, "manual")
        if job_id is None:
            raise APIError(400, "bad_request", notice or "无法创建任务")
        self._audit(conn, "agent.sync", f"agent:{key}", {"job_id": job_id, "notice": notice})
        self._send_json({"job_id": job_id, "notice": notice}, 202)

    def _sync_all(self, conn: sqlite3.Connection) -> None:
        self._require_role("admin")
        results = []
        for agent in list_agents(conn):
            if not agent["enabled"]:
                continue
            job_id, notice = get_job_manager().enqueue(agent["key"], "manual")
            results.append({"agent": agent["key"], "job_id": job_id, "notice": notice})
        self._audit(conn, "agent.sync_all", "all", {"count": len(results)})
        self._send_json({"items": results}, 202)

    def _reset_agent(self, conn: sqlite3.Connection, key: str) -> None:
        self._require_role("admin")
        if key not in AGENT_MAP:
            raise APIError(404, "not_found", "未知 Agent")
        count = reset_source(conn, key)
        conn.execute(
            "UPDATE agents SET status = 'idle', status_message = '已重置游标，等待重新采集', updated_at = ? WHERE key = ?",
            (utc_now(), key),
        )
        conn.commit()
        self._audit(conn, "agent.reset_cursor", f"agent:{key}", {"sources": count})
        self._send_json({"reset": count})

    # ------------------------------------------------------------------ sessions / events / search
    def _sessions(self, conn: sqlite3.Connection) -> None:
        start = self._query("from")
        end = self._query("to")
        if start or end:
            start, end = resolve_time_range(None, start, end)
        settings = get_settings(conn)
        data = list_sessions(
            conn,
            agent=self._query("agent"),
            start=start,
            end=end,
            q=self._query("q"),
            project=self._query("project"),
            page=self._query_int("page", 1),
            page_size=self._query_int("page_size", int(settings.get("default_page_size") or 20), 1, MAX_PAGE_SIZE),
            sort=self._query("sort", "started_desc") or "started_desc",
        )
        self._send_json(data)

    def _session_detail(self, conn: sqlite3.Connection, session_id: int) -> None:
        session = get_session(conn, session_id)
        if not session:
            raise APIError(404, "not_found", "会话不存在")
        event_data = list_session_events(
            conn,
            session_id,
            event_type=self._query("event_type"),
            q=self._query("q"),
            page=self._query_int("page", 1),
            page_size=self._query_int("page_size", 30, 1, 100),
        )
        counts = type_counts(conn, session_id)
        tools = [dict(row) for row in conn.execute(
            """SELECT tool_name, COUNT(*) AS calls FROM events
               WHERE session_id = ? AND type='tool_call' AND tool_name IS NOT NULL
               GROUP BY tool_name ORDER BY calls DESC""",
            (session_id,),
        ).fetchall()]
        session["metadata"] = json_loads(session.get("metadata_json"), {})
        session.pop("metadata_json", None)
        self._send_json({
            "session": session,
            "events": event_data,
            "type_counts": counts,
            "tools": tools,
        })

    def _event_detail(self, conn: sqlite3.Connection, event_id: int) -> None:
        event = get_event(conn, event_id)
        if not event:
            raise APIError(404, "not_found", "事件不存在")
        event.pop("fingerprint", None)
        self._send_json({"item": event})

    def _stats(self, conn: sqlite3.Connection) -> None:
        range_key = self._query("range", "30d") or "30d"
        start, end = resolve_time_range(
            range_key,
            self._query("from"),
            self._query("to"),
        )
        days = self._query_int("days", 30, 1, 180)
        data = analytics(conn, agent=self._query("agent"), start=start, end=end, days=days)
        data["agents"] = [
            {"key": row["key"], "name": row["name"], "color": row["color"]}
            for row in conn.execute("SELECT key, name, color FROM agents ORDER BY rowid")
        ]
        self._send_json(data)

    def _tasks(self, conn: sqlite3.Connection) -> None:
        data = list_jobs(
            conn,
            status=self._query("status"),
            agent=self._query("agent"),
            page=self._query_int("page", 1),
            page_size=self._query_int("page_size", 20, 1, MAX_PAGE_SIZE),
        )
        active = conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        counts = {"queued": 0, "running": 0, "success": 0, "partial": 0, "failed": 0, "cancelled": 0}
        for row in conn.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"):
            counts[row["status"]] = row["count"]
        data["active"] = active
        data["counts"] = counts
        self._send_json(data)

    def _task_detail(self, conn: sqlite3.Connection, job_id: int) -> None:
        row = conn.execute(
            """SELECT j.*, a.name AS agent_name, a.color AS agent_color
               FROM jobs j JOIN agents a ON a.key=j.agent_key WHERE j.id=?""",
            (job_id,),
        ).fetchone()
        if not row:
            raise APIError(404, "not_found", "任务不存在")
        item = dict(row)
        item["stats"] = json_loads(item.get("stats_json"), {})
        self._send_json({"item": item})

    def _retry_task(self, conn: sqlite3.Connection, job_id: int) -> None:
        self._require_role("admin")
        new_job, notice = get_job_manager().retry_job(job_id)
        if new_job is None:
            raise APIError(400, "bad_request", notice or "无法重试任务")
        self._audit(conn, "task.retry", f"job:{job_id}", {"new_job": new_job})
        self._send_json({"job_id": new_job, "notice": notice}, 202)

    # ------------------------------------------------------------------ export
    def _export_filters(self) -> dict[str, Any]:
        start, end = resolve_time_range(
            self._query("range"),
            self._query("from"),
            self._query("to"),
        )
        types = [item for item in (self._query("types", "") or "").split(",") if item]
        # "agents" is the multi-select form used by the export panel; "agent" stays for
        # backwards compatibility with the old single-agent dialog.
        raw_agents = self._query("agents") or self._query("agent") or ""
        agents = [item.strip() for item in raw_agents.split(",") if item.strip() and item.strip() != "all"]
        session_raw = self._query("session_id")
        session_id = None
        if session_raw:
            try:
                session_id = int(session_raw)
            except ValueError:
                raise APIError(400, "bad_request", "session_id 无效")
        return {
            "agents": agents,
            "start": start,
            "end": end,
            "event_types": types,
            "q": self._query("q"),
            "session_id": session_id,
            "project": self._query("project"),
        }

    def _export_estimate(self, conn: sqlite3.Connection) -> None:
        filters = self._export_filters()
        result = estimate_export(conn, **filters)
        limit = int(get_settings(conn).get("export_max_rows") or 0)
        result["limit"] = limit
        result["unlimited"] = limit <= 0
        result["exceeded"] = limit > 0 and result["total"] > limit
        result["filters"] = {
            "agents": filters["agents"],
            "from": filters["start"],
            "to": filters["end"],
            "types": filters["event_types"],
            "session_id": filters["session_id"],
            "project": filters["project"],
        }
        self._send_json(result)

    def _export(self, conn: sqlite3.Connection) -> None:
        fmt = (self._query("format", "csv") or "csv").lower()
        if fmt not in {"csv", "jsonl", "json", "bundle"}:
            raise APIError(400, "bad_request", "不支持的导出格式")
        filters = self._export_filters()
        settings = get_settings(conn)
        try:
            exported = create_export(
                conn,
                format=fmt,
                max_rows=int(settings.get("export_max_rows") or 0),
                requested_by=self.user.get("username") if self.user else None,
                **filters,
            )
        except ExportTooLarge as exc:
            raise APIError(413, "export_too_large", str(exc))
        self._audit(
            conn, "data.export", f"format:{fmt}",
            {"counts": exported["counts"], "filters": exported["manifest"]["filters"]},
        )
        path = Path(exported["path"])
        try:
            self._send_file(path, exported["filename"], exported["content_type"])
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------------ backups
    def _create_backup(self, conn: sqlite3.Connection) -> None:
        body = self._json_body()
        try:
            item = create_backup(note=str(body.get("note") or "手动备份"), created_by=self.user["username"], db_path=self.db_path)
        except Exception as exc:
            raise APIError(500, "backup_failed", f"备份失败: {exc}")
        self._audit(conn, "backup.create", f"backup:{item['id']}", {"filename": item["filename"]})
        self._send_json({"item": item}, 201)

    def _download_backup(self, conn: sqlite3.Connection, backup_id: int) -> None:
        item = get_backup(conn, backup_id)
        if not item:
            raise APIError(404, "not_found", "备份不存在")
        path = Path(item["path"])
        if not path.is_file():
            raise APIError(404, "not_found", "备份文件已丢失")
        self._audit(conn, "backup.download", f"backup:{backup_id}", {"filename": item["filename"]})
        self._send_file(path, item["filename"], "application/gzip")

    def _delete_backup(self, conn: sqlite3.Connection, backup_id: int) -> None:
        if not delete_backup(conn, backup_id):
            raise APIError(404, "not_found", "备份不存在")
        self._audit(conn, "backup.delete", f"backup:{backup_id}")
        self._send_json({"deleted": True})

    # ------------------------------------------------------------------ legacy chatlog import
    def _import_preview(self, conn: sqlite3.Connection) -> None:
        target = (self._query("path") or "").strip()
        if not target:
            raise APIError(400, "bad_request", "请提供导出文件路径")
        try:
            data = preview_import(target, db_path=self.db_path)
        except ImportSourceError as exc:
            raise APIError(400, "bad_source", str(exc))
        except OSError as exc:
            raise APIError(400, "bad_source", f"读取文件失败：{exc}")
        self._send_json(data)

    def _import_start(self, conn: sqlite3.Connection) -> None:
        body = self._json_body()
        target = str(body.get("path") or "").strip()
        if not target:
            raise APIError(400, "bad_request", "请提供导出文件路径")
        options = {
            "include_reasoning": bool(body.get("include_reasoning")),
            "dry_run": bool(body.get("dry_run")),
        }
        try:
            run_id, error = get_import_manager().start(target, options, self.user["username"])
        except ImportSourceError as exc:
            raise APIError(400, "bad_source", str(exc))
        except OSError as exc:
            raise APIError(400, "bad_source", f"读取文件失败：{exc}")
        if error or run_id is None:
            raise APIError(409, "conflict", error or "无法启动导入")
        self._audit(conn, "import.start", f"import_run:{run_id}", {"path": target, **options})
        self._send_json({"run_id": run_id, "item": get_run(conn, run_id)}, 202)

    # ------------------------------------------------------------------ settings / users / logs / system
    def _settings(self, conn: sqlite3.Connection) -> None:
        settings = get_settings(conn)
        self._send_json({
            "settings": settings,
            "counts": table_counts(conn),
            "paths": {"data": str(DB_PATH.parent), "database": str(DB_PATH), "backups": str(BACKUP_DIR)},
        })

    def _update_settings(self, conn: sqlite3.Connection) -> None:
        body = self._json_body()
        updates: dict[str, Any] = {}
        for key, expected in {
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
        }.items():
            if key not in body:
                continue
            value = body[key]
            if expected is bool:
                if not isinstance(value, bool):
                    raise APIError(400, "bad_request", f"{key} 必须是布尔值")
            elif expected is int:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise APIError(400, "bad_request", f"{key} 必须是整数")
                value = int(value)
            else:
                if not isinstance(value, str):
                    raise APIError(400, "bad_request", f"{key} 必须是字符串")
                value = value.strip()
            updates[key] = value
        if not updates:
            raise APIError(400, "bad_request", "没有可更新的配置")
        if "site_name" in updates and not (1 <= len(updates["site_name"]) <= 60):
            raise APIError(400, "bad_request", "站点名称长度需为 1–60 个字符")
        if "sync_interval_minutes" in updates:
            updates["sync_interval_minutes"] = max(1, min(1440, updates["sync_interval_minutes"]))
        if "backup_interval_hours" in updates:
            updates["backup_interval_hours"] = max(1, min(168, updates["backup_interval_hours"]))
        if "backup_retention" in updates:
            updates["backup_retention"] = max(1, min(30, updates["backup_retention"]))
        if "default_page_size" in updates:
            updates["default_page_size"] = max(5, min(100, updates["default_page_size"]))
        if "export_max_rows" in updates:
            # 0 (or less) means "no limit"; otherwise keep a sane ceiling.
            value = int(updates["export_max_rows"])
            updates["export_max_rows"] = 0 if value <= 0 else min(1_000_000, value)
        if "auto_retry_attempts" in updates:
            updates["auto_retry_attempts"] = max(1, min(10, updates["auto_retry_attempts"]))
        if "timezone" in updates and len(updates["timezone"]) > 64:
            raise APIError(400, "bad_request", "时区名称过长")
        settings = update_settings(conn, updates)
        conn.commit()
        self._audit(conn, "settings.update", "settings", updates)
        self._send_json({"settings": settings})

    def _create_user(self, conn: sqlite3.Connection) -> None:
        body = self._json_body()
        user, error = create_user(
            conn,
            str(body.get("username") or ""),
            str(body.get("password") or ""),
            str(body.get("role") or "viewer"),
            bool(body.get("must_change_password", True)),
        )
        if not user:
            raise APIError(400, "bad_request", error or "创建用户失败")
        self._audit(conn, "user.create", f"user:{user['id']}", {"username": user["username"], "role": user["role"]})
        self._send_json({"item": user}, 201)

    def _update_user(self, conn: sqlite3.Connection, user_id: int) -> None:
        body = self._json_body()
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise APIError(404, "not_found", "用户不存在")
        role = body.get("role")
        active = body.get("active")
        if target["id"] == self.user["id"] and (role == "viewer" or active is False):
            raise APIError(400, "bad_request", "不能停用或降级当前登录的管理员")
        if target["role"] == "admin" and (role == "viewer" or active is False):
            if count_admins(conn) <= 1:
                raise APIError(400, "bad_request", "系统至少需要保留一名启用的管理员")
        password = body.get("password")
        updated, error = update_user(
            conn, user_id,
            role=str(role) if role is not None else None,
            active=bool(active) if active is not None else None,
            password=str(password) if password else None,
        )
        if not updated:
            raise APIError(400, "bad_request", error or "更新用户失败")
        self._audit(conn, "user.update", f"user:{user_id}", {
            "role": role, "active": active, "password_reset": bool(password),
        })
        self._send_json({"item": updated})

    def _system_info(self, conn: sqlite3.Connection) -> None:
        import platform
        settings = get_settings(conn)
        self._send_json({
            "version": VERSION,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "time": utc_now(),
            "paths": {
                "workspace": str(WEB_DIR.parent),
                "data": str(DB_PATH.parent),
                "database": str(DB_PATH),
                "backups": str(BACKUP_DIR),
            },
            "counts": table_counts(conn),
            "settings": settings,
            "database_size": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
        })


def create_server(
    host: str = HOST,
    port: int = PORT,
    *,
    db_path: str | Path | None = None,
    disable_scheduler: bool | None = None,
) -> tuple[AgentTrailServer, dict[str, Any]]:
    init_db(db_path)
    get_import_manager().configure(db_path)
    recover_stale_runs(db_path)
    with connection(db_path) as conn:
        created, password = ensure_default_admin(conn)
        cleanup_sessions(conn)
        if created:
            log_system(conn, "warning", "auth", "已创建初始管理员账号，请登录后修改默认密码", {"username": os.environ.get("AGENTTRAIL_ADMIN_USER", "admin")})
        log_system(conn, "info", "server", "迹录 AgentTrail 服务启动", {"version": VERSION})
    if disable_scheduler is None:
        disable_scheduler = os.environ.get("AGENTTRAIL_DISABLE_SCHEDULER") == "1"
    init_job_manager(disable_scheduler=disable_scheduler)
    server = AgentTrailServer((host, port), RequestHandler, db_path=db_path)
    info = {
        "host": host, "port": server.server_address[1],
        "initial_admin_created": created,
        "initial_admin_password": password,
        "db_path": str(db_path or DB_PATH),
    }
    return server, info


def serve(host: str = HOST, port: int = PORT, db_path: str | Path | None = None) -> None:
    server, info = create_server(host, port, db_path=db_path)
    print("=" * 68)
    print(f"  {APP_NAME} · {APP_NAME_EN}  v{VERSION}")
    print(f"  WebUI   http://{host}:{info['port']}")
    print(f"  DB      {info['db_path']}")
    if info["initial_admin_created"]:
        print(f"  初始管理员  admin / {info['initial_admin_password']}  (请立即修改)")
    print("  Ctrl+C to stop")
    print("=" * 68)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n正在停止服务…")
    finally:
        manager = get_job_manager()
        manager.stop()
        server.server_close()
