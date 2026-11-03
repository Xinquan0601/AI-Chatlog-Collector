from __future__ import annotations

import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .auth import cleanup_sessions
from .db import connection, get_agent, get_settings, list_agents, log_system
from .ingestion import SyncError, sync_agent
from .utils import json_dumps, json_loads, shorten, utc_now


class JobManager:
    def __init__(self, worker_count: int = 2, disable_scheduler: bool = False) -> None:
        self.worker_count = worker_count
        self.disable_scheduler = disable_scheduler
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._scheduler_thread: threading.Thread | None = None
        self._last_backup_check = 0.0

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._recover()
        for index in range(self.worker_count):
            thread = threading.Thread(target=self._worker, name=f"agenttrail-worker-{index + 1}", daemon=True)
            thread.start()
            self._threads.append(thread)
        if not self.disable_scheduler:
            self._scheduler_thread = threading.Thread(target=self._scheduler, name="agenttrail-scheduler", daemon=True)
            self._scheduler_thread.start()

    def stop(self) -> None:
        self._stop.set()
        for _ in self._threads:
            self._queue.put(None)
        for thread in self._threads:
            thread.join(timeout=2)

    def _recover(self) -> None:
        with connection() as conn:
            now = utc_now()
            running = conn.execute("SELECT id FROM jobs WHERE status = 'running'").fetchall()
            for row in running:
                conn.execute(
                    "UPDATE jobs SET status = 'failed', error = ?, finished_at = ?, message = ? WHERE id = ?",
                    ("服务重启导致任务中断，请手动重试", now, "任务中断", row["id"]),
                )
            queued = conn.execute("SELECT id FROM jobs WHERE status = 'queued' ORDER BY id").fetchall()
            conn.commit()
        for row in queued:
            self._queue.put(row["id"])

    # ------------------------------------------------------------------ public API
    def active_job(self, agent_key: str) -> dict[str, Any] | None:
        with connection() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE agent_key = ? AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
                (agent_key,),
            ).fetchone()
            return dict(row) if row else None

    def enqueue(self, agent_key: str, trigger: str = "manual") -> tuple[int | None, str | None]:
        with connection() as conn:
            agent = get_agent(conn, agent_key)
            if not agent:
                return None, "未知 Agent"
            active = conn.execute(
                "SELECT id, status FROM jobs WHERE agent_key = ? AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
                (agent_key,),
            ).fetchone()
            if active:
                return int(active["id"]), "已有采集任务正在执行"
            max_attempts = max(1, min(10, int(agent.get("max_attempts") or 3)))
            now = utc_now()
            cursor = conn.execute(
                """INSERT INTO jobs(agent_key, trigger, status, attempts, max_attempts, progress,
                   message, queued_at, created_at)
                   VALUES (?, ?, 'queued', 0, ?, 0, '等待工作线程', ?, ?)""",
                (agent_key, trigger, max_attempts, now, now),
            )
            job_id = int(cursor.lastrowid)
            conn.commit()
            log_system(conn, "info", "jobs", f"已排队 {agent['name']} 采集任务", {"job_id": job_id, "trigger": trigger})
        self._queue.put(job_id)
        return job_id, None

    def retry_job(self, job_id: int) -> tuple[int | None, str | None]:
        with connection() as conn:
            old = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not old:
                return None, "任务不存在"
            if old["status"] not in {"failed", "partial", "success"}:
                return None, "仅已结束的任务可以重试"
        return self.enqueue(old["agent_key"], "retry")

    # ------------------------------------------------------------------ worker
    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=1)
            except queue.Empty:
                continue
            if job_id is None:
                self._queue.task_done()
                break
            try:
                self._run_job(job_id)
            except Exception as exc:  # A worker must never die from one bad job.
                with connection() as conn:
                    conn.execute(
                        "UPDATE jobs SET status = 'failed', error = ?, finished_at = ?, message = ? WHERE id = ?",
                        (f"{type(exc).__name__}: {exc}", utc_now(), "工作线程异常", job_id),
                    )
                    conn.commit()
                    log_system(conn, "error", "jobs", "采集工作线程异常", {"job_id": job_id, "error": str(exc)})
            finally:
                self._queue.task_done()

    def _update(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "status", "attempts", "max_attempts", "progress", "message", "stats_json",
            "error", "queued_at", "started_at", "finished_at",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with connection() as conn:
            conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", (*updates.values(), job_id))
            conn.commit()

    def _run_job(self, job_id: int) -> None:
        with connection() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not job or job["status"] != "queued":
                return
            agent = get_agent(conn, job["agent_key"])
            if not agent:
                conn.execute(
                    "UPDATE jobs SET status = 'failed', error = 'Agent 已不存在', finished_at = ? WHERE id = ?",
                    (utc_now(), job_id),
                )
                conn.commit()
                return
            conn.execute(
                "UPDATE jobs SET status = 'running', started_at = COALESCE(started_at, ?), message = ? WHERE id = ?",
                (utc_now(), "开始采集", job_id),
            )
            conn.commit()

        max_attempts = max(1, int(job["max_attempts"] or agent.get("max_attempts") or 3))
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            self._update(job_id, attempts=attempt, status="running", message=f"第 {attempt}/{max_attempts} 次尝试")
            last_progress = {"value": -1, "message": ""}

            def progress(value: int, message: str) -> None:
                value = max(0, min(100, int(value)))
                if value == last_progress["value"] and message == last_progress["message"]:
                    return
                last_progress["value"] = value
                last_progress["message"] = message
                self._update(job_id, progress=value, message=shorten(message, 500))

            try:
                stats = sync_agent(agent["key"], trigger=job["trigger"], progress=progress)
                status = "success"
                if stats.get("sources_failed") or (stats.get("errors") and stats.get("sources_ok")):
                    status = "partial"
                self._update(
                    job_id,
                    status=status,
                    progress=100,
                    message=(
                        f"完成 · 新增 {stats.get('events_inserted', 0)} 条 · "
                        f"去重 {stats.get('duplicates', 0)} 条"
                    ),
                    stats_json=json_dumps(stats),
                    error=None,
                    finished_at=utc_now(),
                )
                with connection() as conn:
                    log_system(
                        conn, "info" if status == "success" else "warning", "jobs",
                        f"{agent['name']} 采集任务完成",
                        {"job_id": job_id, "status": status, "stats": stats},
                    )
                return
            except SyncError as exc:
                last_error = str(exc)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < max_attempts:
                backoff = min(2 ** (attempt - 1), 8)
                self._update(
                    job_id,
                    status="running",
                    message=f"第 {attempt} 次失败，{backoff}s 后重试",
                    error=last_error[:3000],
                )
                time.sleep(backoff)
            else:
                self._update(
                    job_id,
                    status="failed",
                    progress=0,
                    message=f"采集失败，已自动重试 {max_attempts} 次",
                    error=last_error[:3000],
                    finished_at=utc_now(),
                )
                with connection() as conn:
                    conn.execute(
                        "UPDATE agents SET status = 'error', status_message = ?, last_error_at = ?, updated_at = ? WHERE key = ?",
                        (shorten(last_error, 500), utc_now(), utc_now(), agent["key"]),
                    )
                    conn.commit()
                    log_system(conn, "error", "jobs", f"{agent['name']} 采集任务失败", {"job_id": job_id, "error": last_error})

    # ------------------------------------------------------------------ scheduler
    def _scheduler(self) -> None:
        # Small initial delay keeps server start responsive.
        for _ in range(15):
            if self._stop.wait(2):
                return
        while not self._stop.is_set():
            try:
                self._run_scheduled_syncs()
                self._run_auto_backup()
            except Exception as exc:
                with connection() as conn:
                    log_system(conn, "error", "scheduler", "调度器异常", {"error": str(exc)})
            if self._stop.wait(30):
                return

    def _run_scheduled_syncs(self) -> None:
        now = datetime.now(timezone.utc)
        with connection() as conn:
            settings = get_settings(conn)
            if not settings.get("auto_sync_enabled", True):
                return
            agents = list_agents(conn)
            cleanup_sessions(conn)
        for agent in agents:
            if not agent["enabled"] or not agent.get("auto_sync", True):
                continue
            interval = max(1, int(agent.get("interval_minutes") or settings.get("sync_interval_minutes") or 30))
            last = agent.get("last_scan_at") or agent.get("last_success_at")
            if last:
                try:
                    parsed = datetime.fromisoformat(last.replace("Z", "+00:00"))
                    if now - parsed < timedelta(minutes=interval):
                        continue
                except ValueError:
                    pass
            self.enqueue(agent["key"], "scheduled")

    def _run_auto_backup(self) -> None:
        # Imported lazily to avoid a circular dependency.
        from .backup import auto_backup_if_due

        auto_backup_if_due()


job_manager: JobManager | None = None


def get_job_manager() -> JobManager:
    global job_manager
    if job_manager is None:
        job_manager = JobManager()
    return job_manager


def init_job_manager(disable_scheduler: bool = False) -> JobManager:
    global job_manager
    job_manager = JobManager(disable_scheduler=disable_scheduler)
    job_manager.start()
    return job_manager
