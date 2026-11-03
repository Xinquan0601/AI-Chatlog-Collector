from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .constants import BACKUP_DIR, DB_PATH
from .db import connection, get_settings, log_system
from .utils import utc_now


def _backup_dir() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR


def create_backup(
    note: str | None = None,
    created_by: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    source_path = Path(db_path or DB_PATH)
    if not source_path.exists():
        raise RuntimeError("数据库文件不存在，无法备份")
    target_dir = _backup_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"agenttrail-{stamp}.db.gz"
    final_path = target_dir / filename
    with tempfile.NamedTemporaryFile(prefix="agenttrail-backup-", suffix=".db", delete=False, dir=str(target_dir)) as handle:
        temp_path = Path(handle.name)
    try:
        src = sqlite3.connect(str(source_path), timeout=20)
        dst = sqlite3.connect(str(temp_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with temp_path.open("rb") as raw, gzip.open(final_path, "wb", compresslevel=6) as compressed:
            shutil.copyfileobj(raw, compressed)
        size = final_path.stat().st_size
        with connection() as conn:
            cursor = conn.execute(
                """INSERT INTO backups(filename, path, size_bytes, status, note, created_by, created_at)
                   VALUES (?, ?, ?, 'ready', ?, ?, ?)""",
                (filename, str(final_path), size, note or "手动备份", created_by, utc_now()),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM backups WHERE id = ?", (cursor.lastrowid,)).fetchone()
            log_system(conn, "info", "backup", "数据库备份已创建", {"id": row["id"], "filename": filename, "size": size})
            _apply_retention(conn)
        return dict(row)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _apply_retention(conn: sqlite3.Connection) -> None:
    settings = get_settings(conn)
    retention = max(1, int(settings.get("backup_retention") or 7))
    rows = conn.execute("SELECT * FROM backups WHERE status = 'ready' ORDER BY id DESC").fetchall()
    for row in rows[retention:]:
        try:
            Path(row["path"]).unlink(missing_ok=True)
        except OSError:
            pass
        conn.execute("DELETE FROM backups WHERE id = ?", (row["id"],))
    conn.commit()


def list_backups(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM backups ORDER BY id DESC").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["exists"] = Path(row["path"]).exists()
            if item["exists"]:
                item["size_bytes"] = Path(row["path"]).stat().st_size
        except OSError:
            item["exists"] = False
        result.append(item)
    return result


def get_backup(conn: sqlite3.Connection, backup_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM backups WHERE id = ?", (backup_id,)).fetchone()
    return dict(row) if row else None


def delete_backup(conn: sqlite3.Connection, backup_id: int) -> bool:
    row = conn.execute("SELECT * FROM backups WHERE id = ?", (backup_id,)).fetchone()
    if not row:
        return False
    try:
        Path(row["path"]).unlink(missing_ok=True)
    except OSError:
        pass
    conn.execute("DELETE FROM backups WHERE id = ?", (backup_id,))
    conn.commit()
    log_system(conn, "warning", "backup", "备份文件已删除", {"id": backup_id, "filename": row["filename"]})
    return True


def auto_backup_if_due() -> dict[str, Any] | None:
    with connection() as conn:
        settings = get_settings(conn)
        if not settings.get("auto_backup_enabled", True):
            return None
        last = conn.execute("SELECT * FROM backups WHERE status = 'ready' ORDER BY id DESC LIMIT 1").fetchone()
        interval = max(1, int(settings.get("backup_interval_hours") or 24))
        due = True
        if last:
            try:
                created = datetime.fromisoformat(last["created_at"].replace("Z", "+00:00"))
                due = datetime.now(timezone.utc) - created >= timedelta(hours=interval)
            except ValueError:
                due = True
        if not due:
            return None
    try:
        return create_backup(note="自动备份", created_by="system")
    except Exception as exc:
        with connection() as conn:
            log_system(conn, "error", "backup", "自动备份失败", {"error": str(exc)})
        return None


def backup_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT COUNT(*) AS count, COALESCE(SUM(size_bytes),0) AS size FROM backups WHERE status='ready'").fetchone()
    latest = conn.execute("SELECT created_at FROM backups WHERE status='ready' ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "count": int(rows["count"] or 0),
        "size_bytes": int(rows["size"] or 0),
        "last_backup_at": latest["created_at"] if latest else None,
    }
