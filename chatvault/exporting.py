from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .analytics import _agent_condition, _append_range, _escape_like
from .constants import EVENT_TYPES
from .utils import utc_now


class ExportTooLarge(RuntimeError):
    pass


def _export_where(
    agent: str | None,
    start: str | None,
    end: str | None,
    event_types: list[str] | None,
    q: str | None,
    session_id: int | None,
    project: str | None,
) -> tuple[str, list[Any]]:
    where = ["1=1"]
    params: list[Any] = []
    _agent_condition(where, params, agent, alias="e")
    _append_range(where, params, "e", start, end)
    types = [item for item in (event_types or []) if item in EVENT_TYPES]
    if types:
        where.append(f"e.type IN ({','.join('?' for _ in types)})")
        params.extend(types)
    if q:
        needle = f"%{_escape_like(q)}%"
        where.append("(e.content LIKE ? ESCAPE '\\' OR e.tool_name LIKE ? ESCAPE '\\')")
        params.extend([needle, needle])
    if session_id:
        where.append("e.session_id = ?")
        params.append(session_id)
    if project:
        where.append("s.project = ?")
        params.append(project)
    return " AND ".join(where), params


def _event_rows(
    conn: sqlite3.Connection,
    where_sql: str,
    params: list[Any],
    limit: int,
) -> Iterable[sqlite3.Row]:
    return conn.execute(
        f"""SELECT e.id, e.agent_key, e.session_id, e.type, e.source_event_id, e.sequence,
                   e.timestamp, e.content, e.tool_name, e.tool_call_id, e.tool_status,
                   e.tool_input_json, e.model, e.tokens_input, e.tokens_output,
                   e.metadata_json, e.fingerprint,
                   s.external_id AS session_external_id, s.title AS session_title,
                   s.project, s.cwd, s.source_path
            FROM events e JOIN sessions s ON s.id=e.session_id
            WHERE {where_sql}
            ORDER BY COALESCE(e.timestamp,e.created_at), e.session_id, e.sequence, e.id
            LIMIT ?""",
        (*params, limit),
    )


def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": row["id"],
        "agent": row["agent_key"],
        "session_id": row["session_external_id"],
        "session_title": row["session_title"],
        "project": row["project"],
        "event_type": row["type"],
        "timestamp": row["timestamp"],
        "content": row["content"],
        "tool_name": row["tool_name"],
        "tool_call_id": row["tool_call_id"],
        "tool_status": row["tool_status"],
        "tool_input_json": row["tool_input_json"],
        "model": row["model"],
        "tokens_input": row["tokens_input"],
        "tokens_output": row["tokens_output"],
        "source_path": row["source_path"],
        "fingerprint": row["fingerprint"],
    }


CSV_COLUMNS = [
    "event_id", "agent", "session_id", "session_title", "project", "event_type", "timestamp",
    "content", "tool_name", "tool_call_id", "tool_status", "tool_input_json", "model",
    "tokens_input", "tokens_output", "source_path", "fingerprint",
]


def _csv_safe(value: Any) -> Any:
    """Neutralize spreadsheet formula injection while preserving numbers."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def _write_events_csv(path: Path, rows: Iterable[sqlite3.Row], bom: bool = False) -> None:
    encoding = "utf-8-sig" if bom else "utf-8"
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            item = _event_dict(row)
            writer.writerow([_csv_safe(item.get(column)) for column in CSV_COLUMNS])


def _write_bundle(
    path: Path,
    conn: sqlite3.Connection,
    where_sql: str,
    params: list[Any],
    rows: Iterable[sqlite3.Row],
    manifest: dict[str, Any],
) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        fd, csv_name = tempfile.mkstemp(suffix=".csv")
        csv_path = Path(csv_name)
        try:
            import os
            os.close(fd)
            _write_events_csv(csv_path, rows, bom=False)
            archive.write(csv_path, "events.csv")
        finally:
            csv_path.unlink(missing_ok=True)

        session_rows = conn.execute(
            f"""SELECT DISTINCT s.* FROM sessions s JOIN events e ON e.session_id=s.id WHERE {where_sql}""",
            params,
        ).fetchall()
        fd, session_name = tempfile.mkstemp(suffix=".csv")
        session_path = Path(session_name)
        try:
            import os
            os.close(fd)
            fields = [
                "id", "agent_key", "external_id", "title", "project", "model", "cwd",
                "started_at", "ended_at", "message_count", "user_message_count",
                "assistant_message_count", "tool_call_count", "tool_result_count",
                "event_count", "tokens_input", "tokens_output", "source_path",
            ]
            with session_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                for row in session_rows:
                    writer.writerow({field: _csv_safe(row[field]) for field in fields})
            archive.write(session_path, "sessions.csv")
        finally:
            session_path.unlink(missing_ok=True)

        summary = {
            "manifest": manifest,
            "agents": [
                dict(row) for row in conn.execute(
                    f"""SELECT e.agent_key, COUNT(*) AS events, COUNT(DISTINCT e.session_id) AS sessions
                        FROM events e JOIN sessions s ON s.id=e.session_id
                        WHERE {where_sql} GROUP BY e.agent_key""",
                    params,
                )
            ],
            "event_types": [
                dict(row) for row in conn.execute(
                    f"""SELECT e.type, COUNT(*) AS count FROM events e JOIN sessions s ON s.id=e.session_id
                        WHERE {where_sql} GROUP BY e.type""",
                    params,
                )
            ],
        }
        archive.writestr("summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("data_dictionary.md", DATA_DICTIONARY)


def create_export(
    conn: sqlite3.Connection,
    *,
    format: str = "csv",
    agent: str | None = None,
    start: str | None = None,
    end: str | None = None,
    event_types: list[str] | None = None,
    q: str | None = None,
    session_id: int | None = None,
    project: str | None = None,
    max_rows: int = 200_000,
    requested_by: str | None = None,
) -> dict[str, Any]:
    where_sql, params = _export_where(agent, start, end, event_types, q, session_id, project)
    total = conn.execute(
        f"SELECT COUNT(*) FROM events e JOIN sessions s ON s.id=e.session_id WHERE {where_sql}", params
    ).fetchone()[0]
    if total > max_rows:
        raise ExportTooLarge(
            f"导出记录数为 {total:,}，超过上限 {max_rows:,}，请缩小时间范围或增加筛选条件。"
        )
    session_total = conn.execute(
        f"SELECT COUNT(DISTINCT e.session_id) FROM events e JOIN sessions s ON s.id=e.session_id WHERE {where_sql}",
        params,
    ).fetchone()[0]
    rows = _event_rows(conn, where_sql, params, max_rows)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = {"csv": ".csv", "jsonl": ".jsonl", "json": ".json", "bundle": ".zip"}.get(format, ".csv")
    fd, temp_name = tempfile.mkstemp(prefix="agenttrail-export-", suffix=suffix)
    import os
    os.close(fd)
    path = Path(temp_name)
    manifest = {
        "export_version": "1.0",
        "generated_at": utc_now(),
        "requested_by": requested_by,
        "format": format,
        "filters": {
            "agent": agent, "from": start, "to": end,
            "event_types": event_types or [], "q": q, "session_id": session_id, "project": project,
        },
        "counts": {"events": total, "sessions": session_total},
    }
    try:
        if format == "csv":
            _write_events_csv(path, rows, bom=True)
        elif format == "jsonl":
            with path.open("w", encoding="utf-8", newline="\n") as handle:
                for row in rows:
                    handle.write(json.dumps(_event_dict(row), ensure_ascii=False, separators=(",", ":")) + "\n")
        elif format == "json":
            data = {"manifest": manifest, "events": [_event_dict(row) for row in rows]}
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        elif format == "bundle":
            _write_bundle(path, conn, where_sql, params, rows, manifest)
        else:
            raise ValueError(f"不支持的导出格式: {format}")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    size = path.stat().st_size
    filename = f"agenttrail-{stamp}{suffix}"
    content_type = {
        "csv": "text/csv; charset=utf-8",
        "jsonl": "application/x-ndjson; charset=utf-8",
        "json": "application/json; charset=utf-8",
        "bundle": "application/zip",
    }[format]
    return {
        "path": str(path), "filename": filename, "size_bytes": size,
        "content_type": content_type, "counts": manifest["counts"], "manifest": manifest,
    }


DATA_DICTIONARY = """# AgentTrail 导出数据字典

## 文件

| 文件 | 说明 |
|---|---|
| events.csv | 规范化事件表，一行一条消息 / 工具调用 / 工具结果。 |
| sessions.csv | 会话摘要表，一行一个会话，含聚合计数与 token。 |
| summary.json | 按 Agent 与事件类型的汇总。 |
| manifest.json | 导出筛选条件、生成时间和记录数。 |
| data_dictionary.md | 本说明文件。 |

## events.csv 字段

| 字段 | 说明 |
|---|---|
| event_id | 本地事件主键，稳定递增。 |
| agent | 来源 Agent key（codex / claudecode / opencode / workbuddy / deepseekharness / zcode）。 |
| session_id | 来源侧会话 ID，同一 Agent 内唯一。 |
| session_title | 会话标题（通常取首条用户消息）。 |
| project | 项目 / 工作区名称。 |
| event_type | 四类规范结构之一：user_message、assistant_message、tool_call、tool_result。 |
| timestamp | UTC ISO-8601 时间，毫秒精度。 |
| content | 消息正文或工具结果正文。 |
| tool_name | 工具名，仅 tool_call 有值。 |
| tool_call_id | 用于关联 tool_call 与 tool_result 的调用 ID。 |
| tool_status | 工具结果状态：success / error / requested 等。 |
| tool_input_json | 工具入参 JSON（字符串）。 |
| model | 模型标识（来源提供时）。 |
| tokens_input / tokens_output | 事件级 token；会话级汇总见 sessions.csv。 |
| source_path | 原始记录文件路径，便于溯源。 |
| fingerprint | 去重指纹，可用于二次导入校验。 |

## 分析建议

- 使用 event_type 过滤四类结构；工具调用链通过 tool_call_id 关联。
- 时间字段均为 UTC，展示或分组前请按本地时区转换。
- session_id + agent 唯一标识会话；fingerprint 全局唯一。
- CSV 采用 UTF-8 编码；JSONL 每行是一个独立 JSON 对象，适合流式处理。
"""
