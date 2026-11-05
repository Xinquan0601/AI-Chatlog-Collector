"""Import sessions from a chatlog export database or an AgentTrail archive.

Two source kinds share one intake:

* chatlog (legacy) - the previous tool exported everything into a single SQLite
  file with sessions(session_pk, source, session_id, ...) and messages(row_id,
  session_pk, seq, role, text, ...).  It is opened read-only with SQLite URI
  mode=ro&immutable=1 and mapped onto the normalized model.
* archive - this collector's own export (ZIP bundle, CSV, JSONL or JSON),
  produced on another machine.  Events carry their original fingerprint, so a
  merge only inserts records that are not in the local archive yet, and records
  that exist on both machines are counted as duplicates.

Both paths write through the regular ingestion pipeline, which keeps the whole
operation idempotent and recomputes session aggregates after every batch.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from . import archive as archive_io
from .constants import DB_PATH, EVENT_TYPES
from .db import connection, log_system
from .ingestion import import_batch
from .utils import clean_user_text, json_dumps, json_loads, shorten, utc_now

# Old export source labels -> AgentTrail agent keys.
OLD_SOURCE_AGENTS: dict[str, str] = {
    "claudecode": "claudecode",
    "codex": "codex",
    "opencode-local": "opencode",
    "opencode-linux": "opencode",
    "vscodecopilot": "vscodecopilot",
    "workbuddy": "workbuddy",
    "zcode": "zcode",
    "deepseekharness": "deepseekharness",
}

# Sources whose session identifiers may collide inside one agent.
NAMESPACED_SOURCES = {"opencode-local", "opencode-linux"}

CHUNK_SIZE = 10
MAX_ERRORS = 60
MAX_WARNINGS = 60

ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]


class ImportSourceError(RuntimeError):
    pass


class _Cancelled(Exception):
    pass


def detect_import_kind(path: str | os.PathLike[str]) -> str:
    """Pick the reader for an intake file: own archive or legacy chatlog DB."""
    return "archive" if archive_io.is_archive_file(path) else "legacy"


# ---------------------------------------------------------------------------
# Reading the export
# ---------------------------------------------------------------------------

def _sqlite_uri(path: Path) -> str:
    text = path.as_posix()
    for char, encoded in (("%", "%25"), ("#", "%23"), ("?", "%3f")):
        text = text.replace(char, encoded)
    return "file:" + text + "?mode=ro&immutable=1"


def open_export(path: str | os.PathLike[str]) -> sqlite3.Connection:
    source = Path(path).expanduser()
    if not source.exists():
        raise ImportSourceError(f"文件不存在：{source}")
    if not source.is_file():
        raise ImportSourceError(f"不是文件：{source}")
    try:
        conn = sqlite3.connect(_sqlite_uri(source), uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    except sqlite3.DatabaseError as exc:
        raise ImportSourceError(f"无法读取 SQLite 文件：{exc}") from exc
    if not {"sessions", "messages"} <= tables:
        conn.close()
        raise ImportSourceError("这不是 chatlog 导出文件：缺少 sessions / messages 表")
    return conn


def _read_meta(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = conn.execute("SELECT key, value FROM export_meta").fetchall()
    except sqlite3.DatabaseError:
        return {}
    return {str(row["key"]): str(row["value"]) for row in rows}


def ms_to_iso(value: Any) -> str | None:
    """Convert a millisecond epoch (the old export unit) into ISO 8601 UTC."""
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            return None
    if number <= 0:
        return None
    if number < 10**11:  # tolerate second-precision timestamps
        number *= 1000
    moment = datetime.fromtimestamp(number / 1000, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{number % 1000:03d}Z"


def project_from_path(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).replace(chr(92), "/").rstrip("/")
    name = text.rsplit("/", 1)[-1]
    if not name or len(name) <= 1 or name.endswith(":"):
        return None
    return name


def _json_input(value: Any) -> str | None:
    text = (value or "").strip() if isinstance(value, str) else value
    if not text:
        return None
    if isinstance(text, str):
        try:
            parsed = json_loads(text, None)
        except Exception:
            parsed = None
        if parsed is not None:
            return json_dumps(parsed)
        return json_dumps({"text": text})
    return json_dumps(text)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def preview(
    path: str | os.PathLike[str],
    db_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Return what the export contains and how it maps onto the local archive."""
    if detect_import_kind(path) == "archive":
        try:
            with connection(db_path) as conn:
                return archive_io.preview(path, conn)
        except archive_io.ArchiveError as exc:
            raise ImportSourceError(str(exc)) from exc
    return _preview_legacy(path)


def _preview_legacy(path: str | os.PathLike[str]) -> dict[str, Any]:
    source = Path(path).expanduser()
    conn = open_export(source)
    try:
        meta = _read_meta(conn)
        rows = conn.execute(
            """SELECT source, COUNT(*) AS sessions,
                      COALESCE(SUM(msg_count), 0) AS messages,
                      COALESCE(SUM(CASE WHEN is_subagent THEN 1 ELSE 0 END), 0) AS subagents,
                      MIN(start_ms) AS first_ms,
                      MAX(COALESCE(end_ms, start_ms)) AS last_ms,
                      COALESCE(SUM(input_tokens), 0) AS tokens_input,
                      COALESCE(SUM(output_tokens), 0) AS tokens_output
               FROM sessions GROUP BY source ORDER BY sessions DESC"""
        ).fetchall()
        message_stats = {
            row["source"]: row
            for row in conn.execute(
                """SELECT s.source AS source,
                          COUNT(*) AS total,
                          COALESCE(SUM(CASE WHEN m.is_reasoning = 1 THEN 1 ELSE 0 END), 0) AS reasoning,
                          COALESCE(SUM(CASE WHEN m.role = 'tool_call' THEN 1 ELSE 0 END), 0) AS tool_calls,
                          COALESCE(SUM(CASE WHEN m.role = 'tool_result' THEN 1 ELSE 0 END), 0) AS tool_results
                   FROM messages m JOIN sessions s ON s.session_pk = m.session_pk
                   GROUP BY s.source"""
            ).fetchall()
        }
    finally:
        conn.close()

    sources: list[dict[str, Any]] = []
    warnings: list[str] = []
    totals = {
        "sessions": 0, "messages": 0, "reasoning": 0, "tool_calls": 0,
        "tool_results": 0, "subagents": 0, "tokens_input": 0, "tokens_output": 0,
    }
    for row in rows:
        old_source = str(row["source"] or "")
        agent_key = OLD_SOURCE_AGENTS.get(old_source)
        stats = message_stats.get(old_source)
        item = {
            "source": old_source,
            "agent_key": agent_key,
            "supported": agent_key is not None,
            "sessions": int(row["sessions"] or 0),
            "messages": int(stats["total"] if stats else row["messages"] or 0),
            "reasoning": int(stats["reasoning"] if stats else 0),
            "tool_calls": int(stats["tool_calls"] if stats else 0),
            "tool_results": int(stats["tool_results"] if stats else 0),
            "subagents": int(row["subagents"] or 0),
            "tokens_input": int(row["tokens_input"] or 0),
            "tokens_output": int(row["tokens_output"] or 0),
            "first_at": ms_to_iso(row["first_ms"]),
            "last_at": ms_to_iso(row["last_ms"]),
        }
        sources.append(item)
        totals["sessions"] += item["sessions"]
        totals["messages"] += item["messages"]
        totals["reasoning"] += item["reasoning"]
        totals["tool_calls"] += item["tool_calls"]
        totals["tool_results"] += item["tool_results"]
        totals["subagents"] += item["subagents"]
        totals["tokens_input"] += item["tokens_input"]
        totals["tokens_output"] += item["tokens_output"]
        if agent_key is None:
            warnings.append(f"来源 {old_source} 没有对应的 Agent，导入时会跳过")

    return {
        "kind": "legacy",
        "path": str(source),
        "name": source.name,
        "size_bytes": source.stat().st_size,
        "exported_at": meta.get("exported_at"),
        "tool_version": meta.get("tool_version"),
        "source_db": meta.get("source_db"),
        "meta": meta,
        "sources": sources,
        "totals": totals,
        "warnings": warnings,
    }


def candidate_exports(db_path: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    """Find intake files next to the archive so the UI can offer them.

    Both legacy chatlog databases and this collector's ZIP bundles (dropped in
    the data directory after copying them over from another machine) are
    listed; the UI picks the preview layout from the returned kind.
    """
    base = Path(db_path or DB_PATH).parent
    legacy: list[Path] = []
    archives: list[Path] = []
    if base.exists():
        for candidate in base.rglob("*.db"):
            try:
                if candidate.resolve() == Path(db_path or DB_PATH).resolve():
                    continue
            except OSError:
                continue
            if "backups" in candidate.parts:
                continue
            legacy.append(candidate)
        # Bundles may be dropped in data/ or a subdirectory such as other_data/.
        for candidate in base.rglob("*.zip"):
            if "backups" in candidate.parts:
                continue
            archives.append(candidate)

    items: list[dict[str, Any]] = []
    for path in legacy:
        try:
            conn = sqlite3.connect(_sqlite_uri(path), uri=True, timeout=5)
            try:
                has_meta = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'export_meta'"
                ).fetchone()
                if not has_meta:
                    continue
                sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
                messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            finally:
                conn.close()
        except sqlite3.DatabaseError:
            continue
        except OSError:
            continue
        items.append({
            "kind": "legacy",
            "path": str(path),
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "sessions": int(sessions or 0),
            "messages": int(messages or 0),
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        })

    for path in archives:
        try:
            with zipfile.ZipFile(path) as bundle:
                names = set(bundle.namelist())
                if "events.csv" not in names:
                    continue
                manifest: dict[str, Any] = {}
                if "manifest.json" in names:
                    try:
                        manifest = json_loads(bundle.read("manifest.json").decode("utf-8", "replace"), {}) or {}
                    except (OSError, ValueError):
                        manifest = {}
        except (zipfile.BadZipFile, OSError):
            continue
        counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
        items.append({
            "kind": "archive",
            "path": str(path),
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "sessions": int(counts.get("sessions") or 0),
            "messages": int(counts.get("events") or 0),
            "events": int(counts.get("events") or 0),
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        })

    items.sort(key=lambda item: item["modified_at"], reverse=True)
    return items[:20]


# ---------------------------------------------------------------------------
# Session conversion
# ---------------------------------------------------------------------------

def _session_external_id(old_source: str, session_id: Any) -> str:
    text = str(session_id or "").strip()
    if not text:
        text = "unknown"
    if old_source in NAMESPACED_SOURCES:
        return f"{old_source}:{text}"
    return text


def _session_metadata(row: sqlite3.Row) -> dict[str, Any]:
    extra = json_loads(row["meta_json"], {}) if row["meta_json"] else {}
    return {
        "imported_from": "chatlog",
        "legacy_source": row["source"],
        "legacy_session_pk": row["session_pk"],
        "legacy_tool": row["tool"],
        "legacy_session_id": row["session_id"],
        "parent_session_id": row["parent_session_id"],
        "root_session_id": row["root_session_id"],
        "is_subagent": bool(row["is_subagent"]),
        "project_path": row["project_path"],
        "project_path_raw": row["project_path_raw"],
        "archived_ms": row["archived_ms"],
        "src_msg_count": row["src_msg_count"],
        "user_msg_count": row["user_msg_count"],
        "reasoning_tokens": row["reasoning_tokens"],
        "cache_read_tokens": row["cache_read_tokens"],
        "cache_write_tokens": row["cache_write_tokens"],
        "legacy_meta": extra if isinstance(extra, dict) else {},
    }


def _result_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"ok", "success", "completed", "done"}:
        return "success"
    if text in {"error", "failed", "failure"}:
        return "error"
    return text or "completed"


def build_session_batch(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    include_reasoning: bool,
    export_path: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Convert one old session row plus its messages into a normalized batch."""
    old_source = str(row["source"] or "")
    agent_key = OLD_SOURCE_AGENTS.get(old_source)
    if not agent_key:
        raise ImportSourceError(f"来源 {old_source} 没有对应的 Agent")
    external_id = _session_external_id(old_source, row["session_id"])
    counters = {"events": 0, "skipped_reasoning": 0, "skipped_empty": 0, "skipped_tool": 0}

    messages = conn.execute(
        "SELECT * FROM messages WHERE session_pk = ? ORDER BY seq, row_id",
        (row["session_pk"],),
    ).fetchall()

    events: list[dict[str, Any]] = []
    pending_calls: list[str] = []
    sequence = 0
    first_ts: str | None = None
    last_ts: str | None = None

    def add_event(**payload: Any) -> None:
        nonlocal sequence, first_ts, last_ts
        sequence += 10
        payload.setdefault("sequence", sequence)
        payload["session_external_id"] = external_id
        stamp = payload.get("timestamp")
        if stamp:
            first_ts = min(first_ts, stamp) if first_ts else stamp
            last_ts = max(last_ts, stamp) if last_ts else stamp
        events.append(payload)

    for message in messages:
        role = str(message["role"] or "").lower()
        stamp = ms_to_iso(message["timestamp_ms"])
        reasoning = bool(message["is_reasoning"])
        if reasoning and not include_reasoning:
            counters["skipped_reasoning"] += 1
            continue
        if role == "user":
            content = clean_user_text(message["text"])
            if not content:
                counters["skipped_empty"] += 1
                continue
            add_event(
                type="user_message",
                source_event_id=f"row:{message['row_id']}",
                timestamp=stamp,
                content=content,
                model=message["model"],
                raw_json=json_dumps({"row_id": message["row_id"], "seq": message["seq"]}),
            )
            counters["events"] += 1
        elif role == "assistant":
            content = (message["text"] or "").strip()
            if not content:
                counters["skipped_empty"] += 1
                continue
            metadata = {"reasoning": True} if reasoning else None
            add_event(
                type="assistant_message",
                source_event_id=f"row:{message['row_id']}",
                timestamp=stamp,
                content=content,
                model=message["model"],
                metadata_json=json_dumps(metadata) if metadata else None,
                raw_json=json_dumps({"row_id": message["row_id"], "seq": message["seq"], "reasoning": reasoning}),
            )
            counters["events"] += 1
        elif role == "tool_call":
            tool_name = str(message["tool"] or "unknown").strip().lower()
            call_id = f"chatlog-call-{message['row_id']}"
            pending_calls.append(call_id)
            add_event(
                type="tool_call",
                source_event_id=f"row:{message['row_id']}",
                timestamp=stamp,
                content="",
                tool_name=tool_name,
                tool_call_id=call_id,
                tool_status="requested",
                tool_input_json=_json_input(message["tool_input"]),
                model=message["model"],
                raw_json=json_dumps({"row_id": message["row_id"], "seq": message["seq"]}),
            )
            counters["events"] += 1
        elif role == "tool_result":
            output = (message["tool_output"] or "").strip()
            if not output:
                counters["skipped_empty"] += 1
                continue
            call_id = pending_calls.pop(0) if pending_calls else None
            add_event(
                type="tool_result",
                source_event_id=f"row:{message['row_id']}",
                timestamp=stamp,
                content=output,
                tool_call_id=call_id,
                tool_status=_result_status(message["tool_status"]),
                raw_json=json_dumps({"row_id": message["row_id"], "seq": message["seq"]}),
            )
            counters["events"] += 1
        else:
            counters["skipped_tool"] += 1

    project = project_from_path(row["project_path"]) or project_from_path(row["project_path_raw"])
    title = (row["display_title"] or row["title"] or "").strip()
    if not title:
        for event in events:
            if event["type"] == "user_message":
                title = shorten(event.get("content"), 72)
                break
    if not title:
        title = f"{row['tool'] or old_source} · {external_id}"

    started_at = ms_to_iso(row["start_ms"]) or first_ts
    ended_at = ms_to_iso(row["end_ms"]) or ms_to_iso(row["archived_ms"]) or last_ts
    session = {
        "external_id": external_id,
        "title": title,
        "project": project,
        "cwd": row["project_path_raw"] or row["project_path"],
        "model": row["model"],
        "started_at": started_at,
        "ended_at": ended_at,
        "tokens_input": int(row["input_tokens"] or 0),
        "tokens_output": int(row["output_tokens"] or 0),
        "source_path": f"{export_path}::{old_source}",
        "metadata": _session_metadata(row),
    }
    batch = {
        "agent_key": agent_key,
        "source_path": session["source_path"],
        "sessions": [session],
        "events": events if events else [],
        "errors": [],
    }
    return batch, counters


# ---------------------------------------------------------------------------
# Run import
# ---------------------------------------------------------------------------

def run_import(
    path: str | os.PathLike[str],
    *,
    include_reasoning: bool = False,
    dry_run: bool = False,
    cursor_session_pk: int = 0,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
    source_label: str | None = None,
    db_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Import an export file synchronously and return aggregate statistics."""
    export_path = str(Path(path).expanduser())
    source = open_export(export_path)
    counters: dict[str, Any] = {
        "sessions_total": 0,
        "sessions_imported": 0,
        "sessions_skipped": 0,
        "events_seen": 0,
        "events_inserted": 0,
        "events_updated": 0,
        "duplicates": 0,
        "skipped_reasoning": 0,
        "skipped_empty": 0,
        "errors": [],
        "warnings": [],
        "by_source": {},
        "cursor_session_pk": int(cursor_session_pk or 0),
        "dry_run": bool(dry_run),
    }
    try:
        meta = _read_meta(source)
        counters["export_meta"] = meta
        total = source.execute(
            "SELECT COUNT(*) FROM sessions WHERE session_pk > ?", (counters["cursor_session_pk"],)
        ).fetchone()[0]
        counters["sessions_total"] = int(total or 0)
        processed = 0
        while True:
            if cancel and cancel():
                raise _Cancelled()
            rows = source.execute(
                "SELECT * FROM sessions WHERE session_pk > ? ORDER BY session_pk LIMIT ?",
                (counters["cursor_session_pk"], CHUNK_SIZE),
            ).fetchall()
            if not rows:
                break
            for row in rows:
                if cancel and cancel():
                    raise _Cancelled()
                old_source = str(row["source"] or "")
                bucket = counters["by_source"].setdefault(
                    old_source, {"sessions": 0, "skipped": 0, "events": 0}
                )
                try:
                    batch, session_counters = build_session_batch(
                        source, row,
                        include_reasoning=include_reasoning,
                        export_path=export_path,
                    )
                except ImportSourceError as exc:
                    counters["sessions_skipped"] += 1
                    bucket["skipped"] += 1
                    if len(counters["warnings"]) < MAX_WARNINGS:
                        counters["warnings"].append(f"session {row['session_pk']}: {exc}")
                    counters["cursor_session_pk"] = int(row["session_pk"])
                    continue
                counters["skipped_reasoning"] += session_counters["skipped_reasoning"]
                counters["skipped_empty"] += session_counters["skipped_empty"]
                counters["events_seen"] += len(batch["events"])
                if not batch["events"]:
                    counters["sessions_skipped"] += 1
                    bucket["skipped"] += 1
                elif dry_run:
                    counters["sessions_imported"] += 1
                    bucket["sessions"] += 1
                    bucket["events"] += len(batch["events"])
                else:
                    try:
                        with connection(db_path) as conn:
                            stats = import_batch(conn, batch["agent_key"], batch)
                    except Exception as exc:  # keep going, report the session
                        counters["sessions_skipped"] += 1
                        bucket["skipped"] += 1
                        if len(counters["errors"]) < MAX_ERRORS:
                            counters["errors"].append(
                                f"session {row['session_pk']} ({old_source}): {type(exc).__name__}: {exc}"
                            )
                    else:
                        counters["sessions_imported"] += 1
                        bucket["sessions"] += 1
                        bucket["events"] += stats["events_inserted"]
                        counters["events_inserted"] += stats["events_inserted"]
                        counters["events_updated"] += stats["events_updated"]
                        counters["duplicates"] += stats["duplicates"]
                counters["cursor_session_pk"] = int(row["session_pk"])
                processed += 1
            if progress:
                progress(processed, counters["sessions_total"], f"已处理 {processed}/{counters['sessions_total']} 个会话")
        if progress:
            progress(counters["sessions_total"], counters["sessions_total"], f"完成 {counters['sessions_imported']} 个会话")
        counters["finished_at"] = utc_now()
        counters["source_label"] = source_label or Path(export_path).name
        return counters
    finally:
        source.close()


# ---------------------------------------------------------------------------
# Archive merge (exports written on another machine)
# ---------------------------------------------------------------------------

ARCHIVE_BATCH_EVENTS = 1500


def run_archive_import(
    path: str | os.PathLike[str],
    *,
    dry_run: bool = False,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
    db_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Merge an AgentTrail export into the local archive.

    Every event is matched by the fingerprint computed on the source machine,
    so overlapping records are counted as duplicates and only records that are
    missing locally are inserted.  Batches run through import_batch, which also
    refreshes session aggregates and keeps the operation idempotent.
    """
    source = str(Path(path).expanduser())
    counters: dict[str, Any] = {
        "kind": "archive",
        "format": None,
        "events_total": 0,
        "events_seen": 0,
        "events_inserted": 0,
        "events_updated": 0,
        "duplicates": 0,
        "sessions_seen": 0,
        "sessions_merged": 0,
        "skipped": 0,
        "unknown_agents": 0,
        "by_agent": {},
        "by_type": {item: 0 for item in EVENT_TYPES},
        "errors": [],
        "warnings": [],
        "dry_run": bool(dry_run),
        "started_at": utc_now(),
    }
    with connection(db_path) as conn:
        known_agents = {row["key"] for row in conn.execute("SELECT key FROM agents")}

    touched: set[tuple[str, str]] = set()
    preexisting: dict[tuple[str, str], bool] = {}
    pending: dict[str, list[dict[str, Any]]] = {}
    processed = 0
    last_progress = 0.0

    def note(message: str) -> None:
        if len(counters["warnings"]) < MAX_WARNINGS:
            counters["warnings"].append(message)

    def flush(agent_key: str, bundle: archive_io.Bundle) -> None:
        events = pending.pop(agent_key, [])
        if not events:
            return
        sessions: list[dict[str, Any]] = []
        seen_sessions: set[tuple[str, str]] = set()
        for event in events:
            key = (agent_key, str(event["session_external_id"]))
            if key in seen_sessions:
                continue
            seen_sessions.add(key)
            meta = bundle.sessions.get(key)
            if meta:
                sessions.append(meta)
        unresolved = [key for key in seen_sessions if key not in preexisting]
        if unresolved:
            # Resolve session overlap before this batch writes anything, so a
            # session created by an earlier batch still counts as new.
            with connection(db_path) as conn:
                known = archive_io.existing_session_keys(conn, set(unresolved))
            for key in unresolved:
                preexisting[key] = key in known
        batch = {
            "agent_key": agent_key,
            "source_path": source,
            "sessions": sessions,
            "events": events,
            "errors": [],
        }
        try:
            if dry_run:
                with connection(db_path) as conn:
                    existing = archive_io.existing_fingerprints(
                        conn, [str(item["fingerprint"]) for item in events]
                    )
                for item in events:
                    if str(item["fingerprint"]) in existing:
                        counters["duplicates"] += 1
                    else:
                        counters["events_inserted"] += 1
            else:
                with connection(db_path) as conn:
                    stats = import_batch(conn, agent_key, batch)
                counters["events_inserted"] += stats["events_inserted"]
                counters["events_updated"] += stats["events_updated"]
                counters["duplicates"] += stats["duplicates"]
        except Exception as exc:  # noqa: BLE001 - keep merging the remaining batches
            if len(counters["errors"]) < MAX_ERRORS:
                counters["errors"].append(f"{agent_key}: {type(exc).__name__}: {exc}")

    with archive_io.open_bundle(path) as bundle:
        counters["format"] = bundle.kind
        counters["events_total"] = bundle.expected_events
        total = counters["events_total"]
        for raw in bundle.iter_events():
            if cancel and cancel():
                for agent_key in list(pending):
                    flush(agent_key, bundle)
                raise _Cancelled()
            event, error = archive_io.normalize_event(raw)
            if event is None:
                counters["skipped"] += 1
                note(error or "无法解析的事件")
                continue
            agent_key = event.pop("agent_key")
            if agent_key not in known_agents:
                counters["unknown_agents"] += 1
                note(f"本机没有 Agent「{agent_key}」，导入时会跳过")
                continue
            processed += 1
            counters["events_seen"] = processed
            counters["by_type"][event["type"]] = counters["by_type"].get(event["type"], 0) + 1
            entry = counters["by_agent"].setdefault(
                agent_key, {"events": 0, "inserted": 0, "duplicates": 0}
            )
            entry["events"] += 1
            touched.add((agent_key, str(event["session_external_id"])))
            pending.setdefault(agent_key, []).append(event)
            if len(pending[agent_key]) >= ARCHIVE_BATCH_EVENTS:
                before_inserted = counters["events_inserted"]
                before_duplicates = counters["duplicates"]
                flush(agent_key, bundle)
                entry["inserted"] += counters["events_inserted"] - before_inserted
                entry["duplicates"] += counters["duplicates"] - before_duplicates
            now = time.monotonic()
            if progress and (now - last_progress >= 0.5 or processed % 500 == 0):
                last_progress = now
                progress(processed, total, f"已处理 {processed} 条事件")
        for agent_key in list(pending):
            before_inserted = counters["events_inserted"]
            before_duplicates = counters["duplicates"]
            flush(agent_key, bundle)
            entry = counters["by_agent"].setdefault(
                agent_key, {"events": 0, "inserted": 0, "duplicates": 0}
            )
            entry["inserted"] += counters["events_inserted"] - before_inserted
            entry["duplicates"] += counters["duplicates"] - before_duplicates
        counters["events_total"] = max(counters["events_total"], counters["events_seen"])

        counters["sessions_seen"] = len(touched)
        counters["sessions_merged"] = sum(1 for key in touched if preexisting.get(key))
        if progress:
            progress(processed, counters["events_total"], f"已处理 {processed} 条事件")

    counters["finished_at"] = utc_now()
    counters["source_label"] = Path(source).name
    return counters


# ---------------------------------------------------------------------------
# Run records + background manager
# ---------------------------------------------------------------------------

def create_run(conn: sqlite3.Connection, path: str, options: dict[str, Any], user: str | None) -> int:
    now = utc_now()
    cursor = conn.execute(
        """INSERT INTO import_runs(source_path, source_label, status, options_json,
           created_by, created_at, updated_at) VALUES (?, ?, 'queued', ?, ?, ?, ?)""",
        (path, Path(path).name, json_dumps(options), user, now, now),
    )
    return int(cursor.lastrowid)


def update_run(conn: sqlite3.Connection, run_id: int, **fields: Any) -> None:
    allowed = {
        "status", "total_sessions", "total_messages", "processed_sessions",
        "imported_sessions", "skipped_sessions", "imported_events",
        "duplicate_events", "skipped_messages", "cursor_session_pk",
        "errors_json", "warnings_json", "summary_json", "started_at",
        "finished_at", "updated_at",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    updates.setdefault("updated_at", utc_now())
    assignments = ", ".join(f"{key} = ?" for key in updates)
    conn.execute(f"UPDATE import_runs SET {assignments} WHERE id = ?", (*updates.values(), run_id))


def get_run(conn: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM import_runs WHERE id = ?", (run_id,)).fetchone()
    return _serialize_run(row) if row else None


def list_runs(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM import_runs ORDER BY id DESC LIMIT ?", (max(1, min(100, limit)),)
    ).fetchall()
    return [_serialize_run(row) for row in rows]


def _serialize_run(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["options"] = json_loads(data.pop("options_json", None), {})
    data["kind"] = str(data["options"].get("kind") or "legacy")
    data["summary"] = json_loads(data.pop("summary_json", None), {}) or {}
    data["errors"] = json_loads(data.pop("errors_json", None), []) or []
    data["warnings"] = json_loads(data.pop("warnings_json", None), []) or []
    data["progress"] = (
        100 if data.get("status") in {"success", "partial"}
        else 0 if not data.get("total_sessions")
        else min(99, int(int(data.get("processed_sessions") or 0) * 100 / int(data["total_sessions"])))
    )
    return data


def recover_stale_runs(db_path: str | os.PathLike[str] | None = None) -> int:
    with connection(db_path) as conn:
        stale = conn.execute(
            "SELECT id FROM import_runs WHERE status IN ('queued','running')"
        ).fetchall()
        if stale:
            now = utc_now()
            conn.execute(
                """UPDATE import_runs SET status = 'interrupted', finished_at = ?,
                   updated_at = ?, warnings_json = COALESCE(warnings_json, '[]')
                   WHERE status IN ('queued','running')""",
                (now, now),
            )
        return len(stale)


class ImportManager:
    """Single background worker so two imports never fight over the archive."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._run_id: int | None = None
        self._cancel = threading.Event()
        self._db_path: str | os.PathLike[str] | None = None

    def configure(self, db_path: str | os.PathLike[str] | None) -> None:
        self._db_path = db_path

    def active_run_id(self) -> int | None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self._run_id
            return None

    def start(self, path: str, options: dict[str, Any], user: str | None) -> tuple[int | None, str | None]:
        kind = detect_import_kind(path)
        try:
            if kind == "archive":
                with archive_io.open_bundle(path):
                    pass
            else:
                probe = open_export(path)
                probe.close()
        except archive_io.ArchiveError as exc:
            raise ImportSourceError(str(exc)) from exc
        options = {**options, "kind": kind}
        with self._lock:
            if self._thread and self._thread.is_alive():
                return None, "已有导入任务正在执行"
            with connection(self._db_path) as conn:
                run_id = create_run(conn, path, options, user)
            self._run_id = run_id
            self._cancel.clear()
            self._thread = threading.Thread(
                target=self._worker, args=(run_id,), name="agenttrail-import", daemon=True
            )
            self._thread.start()
            return run_id, None

    def cancel(self, run_id: int) -> bool:
        with self._lock:
            if self._run_id != run_id or not self._thread or not self._thread.is_alive():
                return False
        self._cancel.set()
        return True

    def _worker(self, run_id: int) -> None:
        with connection(self._db_path) as conn:
            run = conn.execute("SELECT * FROM import_runs WHERE id = ?", (run_id,)).fetchone()
            if not run:
                return
            options = json_loads(run["options_json"], {}) or {}
            start_cursor = int(run["cursor_session_pk"] or 0)
            kind = str(options.get("kind") or detect_import_kind(run["source_path"]))
            conn.execute(
                "UPDATE import_runs SET status = 'running', started_at = ?, updated_at = ? WHERE id = ?",
                (utc_now(), utc_now(), run_id),
            )
            conn.commit()
        if kind == "archive":
            with archive_io.open_bundle(run["source_path"]) as bundle:
                total_units = bundle.expected_events
        else:
            probe = open_export(run["source_path"])
            try:
                total_units = int(probe.execute(
                    "SELECT COUNT(*) FROM sessions WHERE session_pk > ?", (start_cursor,)
                ).fetchone()[0] or 0)
            finally:
                probe.close()
        with connection(self._db_path) as conn:
            conn.execute(
                "UPDATE import_runs SET total_sessions = ?, updated_at = ? WHERE id = ?",
                (total_units, utc_now(), run_id),
            )
            conn.commit()
        last_flush = {"at": 0.0}

        def progress(processed: int, total: int, message: str) -> None:
            now = time.time()
            if now - last_flush["at"] < 0.6:
                return
            last_flush["at"] = now
            with connection(self._db_path) as conn:
                conn.execute(
                    "UPDATE import_runs SET processed_sessions = ?, updated_at = ? WHERE id = ?",
                    (int(processed), utc_now(), run_id),
                )

        try:
            if kind == "archive":
                result = run_archive_import(
                    run["source_path"],
                    dry_run=bool(options.get("dry_run")),
                    progress=progress,
                    cancel=self._cancel.is_set,
                    db_path=self._db_path,
                )
                status = "partial" if result["errors"] else "success"
                with connection(self._db_path) as conn:
                    update_run(
                        conn, run_id,
                        status=status,
                        total_sessions=int(result["events_total"] or result["events_seen"]),
                        processed_sessions=int(result["events_seen"]),
                        total_messages=int(result["sessions_seen"]),
                        imported_sessions=int(result["sessions_seen"]),
                        imported_events=int(result["events_inserted"]),
                        duplicate_events=int(result["duplicates"]),
                        skipped_messages=int(result["skipped"]) + int(result["unknown_agents"]),
                        errors_json=json_dumps(result["errors"]),
                        warnings_json=json_dumps(result["warnings"]),
                        summary_json=json_dumps(result),
                        finished_at=utc_now(),
                    )
                    log_system(
                        conn, "info" if status == "success" else "warning", "import",
                        f"档案包合并完成：新增 {result['events_inserted']} 条，去重 {result['duplicates']} 条",
                        {"run_id": run_id, "path": run["source_path"], "stats": {
                            "events": result["events_inserted"],
                            "duplicates": result["duplicates"],
                            "sessions": result["sessions_seen"],
                        }},
                    )
            else:
                result = run_import(
                    run["source_path"],
                    include_reasoning=bool(options.get("include_reasoning")),
                    dry_run=bool(options.get("dry_run")),
                    cursor_session_pk=start_cursor,
                    progress=progress,
                    cancel=self._cancel.is_set,
                    db_path=self._db_path,
                )
                status = "partial" if result["errors"] else "success"
                with connection(self._db_path) as conn:
                    update_run(
                        conn, run_id,
                        status=status,
                        total_sessions=int(result.get("sessions_total") or 0),
                        processed_sessions=int(result.get("sessions_total") or 0),
                        imported_sessions=int(result["sessions_imported"]),
                        skipped_sessions=int(result["sessions_skipped"]),
                        imported_events=int(result["events_inserted"]),
                        duplicate_events=int(result["duplicates"]),
                        skipped_messages=int(result["skipped_reasoning"]) + int(result["skipped_empty"]),
                        cursor_session_pk=int(result["cursor_session_pk"]),
                        errors_json=json_dumps(result["errors"]),
                        warnings_json=json_dumps(result["warnings"]),
                        summary_json=json_dumps(result),
                        finished_at=utc_now(),
                    )
                    log_system(
                        conn, "info" if status == "success" else "warning", "import",
                        f"旧数据导入完成：{result['sessions_imported']} 个会话",
                        {"run_id": run_id, "path": run["source_path"], "stats": {
                            "sessions": result["sessions_imported"],
                            "events": result["events_inserted"],
                            "duplicates": result["duplicates"],
                        }},
                    )
        except _Cancelled:
            with connection(self._db_path) as conn:
                update_run(
                    conn, run_id, status="canceled", finished_at=utc_now(),
                    warnings_json=json_dumps(["导入被手动取消，已写入的部分保留；重新执行会按指纹去重"]),
                )
        except Exception as exc:  # noqa: BLE001 - surface the failure in the run record
            with connection(self._db_path) as conn:
                update_run(
                    conn, run_id, status="failed", finished_at=utc_now(),
                    errors_json=json_dumps([f"{type(exc).__name__}: {exc}"]),
                )
                log_system(conn, "error", "import", "数据导入失败", {"run_id": run_id, "error": str(exc)})


_manager = ImportManager()


def get_import_manager() -> ImportManager:
    return _manager
