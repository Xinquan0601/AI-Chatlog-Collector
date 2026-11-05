from __future__ import annotations

import sqlite3
from typing import Any, Callable

from .collectors import Source, collect_source, discover_sources
from .constants import DEFAULT_AGENT_CONFIG
from .db import get_agent, log_system
from .utils import (
    iso_from_any,
    json_dumps,
    json_loads,
    parse_date_bound,
    safe_json,
    sha256_text,
    shorten,
    utc_now,
)

ProgressFn = Callable[[int, str], None]


class SyncError(RuntimeError):
    pass


def ensure_source(conn: sqlite3.Connection, agent_key: str, source: Source) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM sources WHERE agent_key = ? AND path = ?",
        (agent_key, source.path),
    ).fetchone()
    now = utc_now()
    if row:
        conn.execute(
            """UPDATE sources SET source_type = ?, size_bytes = ?, mtime_ns = ?, updated_at = ?
               WHERE id = ?""",
            (source.source_type, source.size_bytes, source.mtime_ns, now, row["id"]),
        )
        refreshed = conn.execute("SELECT * FROM sources WHERE id = ?", (row["id"],)).fetchone()
        return dict(refreshed)
    cursor = conn.execute(
        """INSERT INTO sources(agent_key, path, source_type, size_bytes, mtime_ns, cursor_json,
           status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, NULL, 'pending', ?, ?)""",
        (agent_key, source.path, source.source_type, source.size_bytes, source.mtime_ns, now, now),
    )
    refreshed = conn.execute("SELECT * FROM sources WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(refreshed)


def _session_fallback_title(agent_key: str, external_id: str, project: str | None) -> str:
    if project:
        return f"{project} · {agent_key}"
    return f"{agent_key} · {external_id}"


def upsert_session(conn: sqlite3.Connection, agent_key: str, session: dict[str, Any]) -> int:
    external_id = str(session.get("external_id") or "").strip()
    if not external_id:
        raise ValueError("session external_id is empty")
    now = utc_now()
    existing = conn.execute(
        "SELECT * FROM sessions WHERE agent_key = ? AND external_id = ?",
        (agent_key, external_id),
    ).fetchone()
    title = (session.get("title") or "").strip()
    project = session.get("project")
    model = session.get("model")
    cwd = session.get("cwd")
    source_path = session.get("source_path")
    started_at = parse_date_bound(session.get("started_at"))
    ended_at = parse_date_bound(session.get("ended_at"))
    tokens_input = int(session.get("tokens_input") or 0)
    tokens_output = int(session.get("tokens_output") or 0)
    metadata = session.get("metadata") or {}

    if not existing:
        cursor = conn.execute(
            """INSERT INTO sessions
            (agent_key, external_id, title, project, model, cwd, started_at, ended_at,
             tokens_input, tokens_output, source_path, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                agent_key, external_id, title or _session_fallback_title(agent_key, external_id, project),
                project, model, cwd, started_at, ended_at, tokens_input, tokens_output,
                source_path, safe_json(metadata, 200_000) if metadata else None, now, now,
            ),
        )
        return int(cursor.lastrowid)

    existing_title = existing["title"] or ""
    is_fallback = (
        not existing_title
        or existing_title == existing["external_id"]
        or existing_title.startswith(f"{agent_key} ·")
        or existing_title.startswith("未命名")
    )
    next_title = title if (title and (is_fallback or not existing_title)) else existing_title
    old_metadata = json_loads(existing["metadata_json"], {})
    if isinstance(metadata, dict) and metadata:
        old_metadata.update(metadata)
    conn.execute(
        """UPDATE sessions SET
            title = ?, project = COALESCE(?, project), model = COALESCE(?, model),
            cwd = COALESCE(?, cwd), source_path = COALESCE(?, source_path),
            started_at = CASE WHEN ? IS NULL THEN started_at
                              WHEN started_at IS NULL THEN ?
                              WHEN ? < started_at THEN ? ELSE started_at END,
            ended_at = CASE WHEN ? IS NULL THEN ended_at
                            WHEN ended_at IS NULL THEN ?
                            WHEN ? > ended_at THEN ? ELSE ended_at END,
            tokens_input = MAX(tokens_input, ?), tokens_output = MAX(tokens_output, ?),
            metadata_json = ?, updated_at = ?
        WHERE id = ?""",
        (
            next_title, project, model, cwd, source_path,
            started_at, started_at, started_at, started_at,
            ended_at, ended_at, ended_at, ended_at,
            tokens_input, tokens_output,
            safe_json(old_metadata, 200_000) if old_metadata else existing["metadata_json"], now,
            existing["id"],
        ),
    )
    return int(existing["id"])


def event_fingerprint(agent_key: str, external_session_id: str, event: dict[str, Any]) -> str:
    source_event_id = str(event.get("source_event_id") or "")
    base = "|".join([
        agent_key,
        external_session_id,
        str(event.get("type") or ""),
        source_event_id,
        str(event.get("tool_call_id") or ""),
    ])
    # Synthetic line numbers restart for each incremental chunk, so include the
    # payload in the identity for those events.
    if not source_event_id or source_event_id.startswith("line-") or source_event_id.startswith("block-"):
        base += "|" + sha256_text(str(event.get("timestamp") or ""))
        base += "|" + sha256_text(str(event.get("content") or ""))
        base += "|" + sha256_text(str(event.get("tool_input_json") or ""))
    return sha256_text(base)


_FINGERPRINT_CHUNK = 400


def _prefetch_events(conn: sqlite3.Connection, fingerprints: list[str]) -> dict[str, dict[str, Any]]:
    """Resolve a whole batch of fingerprints with a few IN queries.

    Re-reading one row per event dominated the sync time on large sources, and
    every lookup pulled the full payload (including raw_json) just to compare a
    few columns. Chunked lookups keep each statement under SQLite's parameter
    limit and only fetch the fields the merge decision needs.
    """
    found: dict[str, dict[str, Any]] = {}
    for start in range(0, len(fingerprints), _FINGERPRINT_CHUNK):
        chunk = fingerprints[start:start + _FINGERPRINT_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"""SELECT id, fingerprint, content, tool_status, tool_input_json
                FROM events WHERE fingerprint IN ({placeholders})""",
            chunk,
        ):
            found[row["fingerprint"]] = dict(row)
    return found


def import_batch(conn: sqlite3.Connection, agent_key: str, batch: dict[str, Any]) -> dict[str, int]:
    sessions_by_external: dict[str, int] = {}
    stats = {"sessions": 0, "events_seen": 0, "events_inserted": 0, "duplicates": 0, "events_updated": 0}
    for session in batch.get("sessions", []):
        session_id = upsert_session(conn, agent_key, session)
        sessions_by_external[session["external_id"]] = session_id
        stats["sessions"] += 1

    prepared: list[tuple[dict[str, Any], int, str]] = []
    for event in batch.get("events", []):
        external_sid = event["session_external_id"]
        session_id = sessions_by_external.get(external_sid)
        if session_id is None:
            session_id = upsert_session(conn, agent_key, {
                "external_id": external_sid,
                "source_path": batch.get("source_path"),
                "title": _session_fallback_title(agent_key, external_sid, None),
                "started_at": event.get("timestamp"),
                "ended_at": event.get("timestamp"),
            })
            sessions_by_external[external_sid] = session_id
        # Archive imports carry the fingerprint computed by the source machine.
        # Reusing it keeps cross-machine merges idempotent: rows that already
        # exist locally under the same identity are treated as duplicates.
        fingerprint = str(event.get("fingerprint") or "").strip() or event_fingerprint(
            agent_key, external_sid, event
        )
        prepared.append((event, session_id, fingerprint))

    existing_map = _prefetch_events(conn, [fingerprint for _, _, fingerprint in prepared])

    for event, session_id, fingerprint in prepared:
        stats["events_seen"] += 1
        existing = existing_map.get(fingerprint)
        content = event.get("content") or ""
        if existing:
            stats["duplicates"] += 1
            # Tool results can arrive after a tool call, and providers sometimes
            # rewrite a streaming message. Keep the newest non-empty payload.
            changed = False
            next_content = content if content else existing["content"]
            next_status = event.get("tool_status") or existing["tool_status"]
            next_input = event.get("tool_input_json")
            if next_content != existing["content"]:
                changed = True
            if next_status != existing["tool_status"]:
                changed = True
            if next_input and next_input != existing["tool_input_json"]:
                changed = True
            if changed:
                conn.execute(
                    """UPDATE events SET content = ?, tool_status = COALESCE(?, tool_status),
                       tool_input_json = COALESCE(?, tool_input_json), raw_json = COALESCE(?, raw_json)
                       WHERE id = ?""",
                    (next_content, next_status, next_input, event.get("raw_json"), existing["id"]),
                )
                existing["content"] = next_content
                existing["tool_status"] = next_status or existing["tool_status"]
                existing["tool_input_json"] = next_input or existing["tool_input_json"]
                stats["events_updated"] += 1
            continue
        # Store one canonical timestamp format (UTC, millisecond precision). Mixed
        # precisions would otherwise break the lexicographic range comparison used
        # by the export filters, e.g. "...:00Z" sorts before "...:00.000Z".
        timestamp = iso_from_any(event.get("timestamp")) or iso_from_any(utc_now())
        try:
            cursor = conn.execute(
                """INSERT INTO events
                (agent_key, session_id, type, source_event_id, sequence, timestamp, content,
                 tool_name, tool_call_id, tool_status, tool_input_json, model, tokens_input,
                 tokens_output, metadata_json, raw_json, fingerprint, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    agent_key, session_id, event["type"], event.get("source_event_id"),
                    int(event.get("sequence") or 0), timestamp, content,
                    event.get("tool_name"), event.get("tool_call_id"), event.get("tool_status"),
                    event.get("tool_input_json"), event.get("model"),
                    event.get("tokens_input"), event.get("tokens_output"),
                    event.get("metadata_json"), event.get("raw_json"), fingerprint, utc_now(),
                ),
            )
            # Keep the map current so a second occurrence in the same batch is
            # merged against the row we just wrote, exactly like a re-read.
            existing_map[fingerprint] = {
                "id": cursor.lastrowid,
                "content": content,
                "tool_status": event.get("tool_status"),
                "tool_input_json": event.get("tool_input_json"),
            }
            stats["events_inserted"] += 1
        except sqlite3.IntegrityError:
            stats["duplicates"] += 1

    for session_id in set(sessions_by_external.values()):
        refresh_session_aggregates(conn, session_id)
    return stats


def refresh_session_aggregates(conn: sqlite3.Connection, session_id: int) -> None:
    counts = conn.execute(
        """SELECT
             SUM(CASE WHEN type = 'user_message' THEN 1 ELSE 0 END) AS user_count,
             SUM(CASE WHEN type = 'assistant_message' THEN 1 ELSE 0 END) AS assistant_count,
             SUM(CASE WHEN type = 'tool_call' THEN 1 ELSE 0 END) AS tool_call_count,
             SUM(CASE WHEN type = 'tool_result' THEN 1 ELSE 0 END) AS tool_result_count,
             COUNT(*) AS event_count,
             MIN(timestamp) AS first_at,
             MAX(timestamp) AS last_at,
             COALESCE(SUM(tokens_input), 0) AS tokens_in,
             COALESCE(SUM(tokens_output), 0) AS tokens_out
           FROM events WHERE session_id = ?""",
        (session_id,),
    ).fetchone()
    if not counts:
        return
    user_count = int(counts["user_count"] or 0)
    assistant_count = int(counts["assistant_count"] or 0)
    conn.execute(
        """UPDATE sessions SET
             user_message_count = ?, assistant_message_count = ?,
             tool_call_count = ?, tool_result_count = ?, event_count = ?,
             message_count = ?,
             started_at = CASE WHEN ? IS NULL THEN started_at
                               WHEN started_at IS NULL THEN ?
                               WHEN ? < started_at THEN ? ELSE started_at END,
             ended_at = CASE WHEN ? IS NULL THEN ended_at
                             WHEN ended_at IS NULL THEN ?
                             WHEN ? > ended_at THEN ? ELSE ended_at END,
             tokens_input = MAX(tokens_input, ?), tokens_output = MAX(tokens_output, ?),
             updated_at = ?
           WHERE id = ?""",
        (
            user_count, assistant_count, int(counts["tool_call_count"] or 0),
            int(counts["tool_result_count"] or 0), int(counts["event_count"] or 0),
            user_count + assistant_count,
            counts["first_at"], counts["first_at"], counts["first_at"], counts["first_at"],
            counts["last_at"], counts["last_at"], counts["last_at"], counts["last_at"],
            int(counts["tokens_in"] or 0), int(counts["tokens_out"] or 0),
            utc_now(), session_id,
        ),
    )


def update_source_result(
    conn: sqlite3.Connection,
    source_row: dict[str, Any],
    *,
    next_cursor: dict[str, Any] | None,
    status: str,
    error: str | None,
    inserted: int = 0,
) -> None:
    now = utc_now()
    cursor_json = json_dumps(next_cursor) if next_cursor is not None else source_row.get("cursor_json")
    conn.execute(
        """UPDATE sources SET cursor_json = ?, status = ?, last_error = ?, last_synced_at = ?,
           events_imported = events_imported + ?, updated_at = ? WHERE id = ?""",
        (cursor_json, status, error, now, inserted, now, source_row["id"]),
    )


def _mark_agent(
    conn: sqlite3.Connection,
    agent_key: str,
    status: str,
    message: str,
    *,
    success: bool = False,
) -> None:
    now = utc_now()
    conn.execute(
        """UPDATE agents SET status = ?, status_message = ?, last_scan_at = ?,
           last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END,
           last_error_at = CASE WHEN ? = 'error' THEN ? ELSE last_error_at END,
           updated_at = ? WHERE key = ?""",
        (status, shorten(message, 500), now, 1 if success else 0, now, status, now, now, agent_key),
    )


def sync_agent(
    agent_key: str,
    trigger: str = "manual",
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    from .db import connection

    started = utc_now()
    with connection() as conn:
        agent = get_agent(conn, agent_key)
        if not agent:
            raise SyncError(f"未知 Agent: {agent_key}")
        if not agent["enabled"] and trigger != "manual":
            return {
                "agent_key": agent_key, "skipped": True, "reason": "disabled",
                "sources_total": 0, "sources_ok": 0, "sources_failed": 0,
                "sessions": 0, "events_inserted": 0, "duplicates": 0,
                "errors": [], "warnings": [], "started_at": started, "finished_at": utc_now(),
            }
        _mark_agent(conn, agent_key, "syncing", "正在扫描数据源…")
        conn.commit()
        if progress:
            progress(5, "正在发现数据源…")

        sources, warnings = discover_sources(agent)
        stats: dict[str, Any] = {
            "agent_key": agent_key,
            "trigger": trigger,
            "sources_total": len(sources),
            "sources_ok": 0,
            "sources_failed": 0,
            "sessions": 0,
            "events_seen": 0,
            "events_inserted": 0,
            "duplicates": 0,
            "events_updated": 0,
            "errors": [],
            "warnings": warnings,
            "started_at": started,
            "finished_at": None,
        }
        if not sources:
            message = "未发现可采集的数据源"
            if warnings:
                message += "：" + "；".join(warnings[:2])
            _mark_agent(conn, agent_key, "warning", message)
            conn.commit()
            log_system(conn, "warning", "collector", f"{agent['name']} 未发现数据源", {"warnings": warnings})
            stats["finished_at"] = utc_now()
            stats["status"] = "warning"
            return stats

        total = len(sources)
        for index, source in enumerate(sources):
            source_row = ensure_source(conn, agent_key, source)
            conn.commit()
            state = json_loads(source_row.get("cursor_json"), {})
            label = shorten(source.path, 80)
            if progress:
                progress(10 + int(80 * index / max(1, total)), f"正在采集 {index + 1}/{total} · {PathName(source.path)}")
            try:
                batch, next_cursor = collect_source(agent_key, source, state)
                import_stats = import_batch(conn, agent_key, batch)
                parser_errors = batch.get("errors") or []
                source_status = "warning" if parser_errors else "ok"
                error_text = "；".join(parser_errors[:5]) if parser_errors else None
                if parser_errors:
                    stats["errors"].append(f"{label}: {error_text}")
                update_source_result(
                    conn, source_row, next_cursor=next_cursor, status=source_status,
                    error=error_text, inserted=import_stats["events_inserted"],
                )
                stats["sources_ok"] += 1
                for key in ("sessions", "events_seen", "events_inserted", "duplicates", "events_updated"):
                    stats[key] += import_stats.get(key, 0)
                conn.commit()
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                update_source_result(
                    conn, source_row, next_cursor=None, status="error",
                    error=message[:2000], inserted=0,
                )
                stats["sources_failed"] += 1
                stats["errors"].append(f"{label}: {message}")
                log_system(conn, "error", "collector", f"{agent['name']} 采集失败: {label}", {"error": message})
                conn.commit()

        stats["finished_at"] = utc_now()
        if stats["sources_ok"] == 0 and stats["sources_failed"] > 0:
            status = "error"
            message = f"全部 {stats['sources_failed']} 个数据源采集失败"
            _mark_agent(conn, agent_key, status, message)
            stats["status"] = status
            conn.commit()
            raise SyncError(f"{message}：" + "；".join(stats["errors"][:3]))
        if stats["sources_failed"] or stats["errors"] or warnings:
            status = "warning"
            message = (
                f"已采集 {stats['sources_ok']}/{stats['sources_total']} 个源，"
                f"新增 {stats['events_inserted']} 条记录"
            )
            if stats["errors"]:
                message += f"，{len(stats['errors'])} 个异常"
            elif warnings:
                message += "；" + "；".join(warnings[:2])
        else:
            status = "ok"
            message = (
                f"已采集 {stats['sources_ok']} 个源，新增 {stats['events_inserted']} 条记录"
            )
        _mark_agent(conn, agent_key, status, message, success=True)
        log_system(
            conn, "warning" if status == "warning" else "info", "collector",
            f"{agent['name']} 同步完成",
            {key: stats[key] for key in ("sources_total", "sources_ok", "sources_failed", "sessions", "events_inserted", "duplicates")},
        )
        # Bulk inserts skew the planner statistics; refresh them when they are
        # stale instead of paying for a full ANALYZE on every sync.
        conn.execute("PRAGMA optimize")
        conn.commit()
        stats["status"] = status
        if progress:
            progress(100, message)
        return stats


def PathName(path: str) -> str:
    from pathlib import Path
    try:
        return Path(path).name
    except Exception:
        return path


def sync_all(trigger: str = "scheduled") -> list[str]:
    from .db import connection, list_agents
    queued = []
    with connection() as conn:
        agents = list_agents(conn)
    for agent in agents:
        if agent["enabled"]:
            queued.append(agent["key"])
    return queued


def reset_source(conn: sqlite3.Connection, agent_key: str, source_path: str | None = None) -> int:
    if source_path:
        cursor = conn.execute(
            "UPDATE sources SET cursor_json = NULL, status = 'pending', last_error = NULL WHERE agent_key = ? AND path = ?",
            (agent_key, source_path),
        )
    else:
        cursor = conn.execute(
            "UPDATE sources SET cursor_json = NULL, status = 'pending', last_error = NULL WHERE agent_key = ?",
            (agent_key,),
        )
    conn.commit()
    return cursor.rowcount
