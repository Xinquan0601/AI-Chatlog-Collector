from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .constants import BACKUP_DIR, DB_PATH, EVENT_TYPES, MAX_PAGE_SIZE
from .utils import clamp, iso_from_any, json_loads, utc_now


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def resolve_time_range(
    range_key: str | None = None,
    from_value: str | None = None,
    to_value: str | None = None,
) -> tuple[str | None, str | None]:
    now = datetime.now(timezone.utc)
    start = parse_bound(from_value)
    end = parse_bound(to_value)
    if range_key and not start:
        mapping = {
            "24h": timedelta(hours=24),
            "7d": timedelta(days=7),
            "30d": timedelta(days=30),
            "90d": timedelta(days=90),
        }
        if range_key in mapping:
            start = now - mapping[range_key]
    return (_utc_iso(start) if start else None, _utc_iso(end) if end else None)


def parse_bound(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = iso_from_any(value)
    if normalized:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _append_range(where: list[str], params: list[Any], alias: str, start: str | None, end: str | None) -> None:
    column = f"{alias}.timestamp" if alias else "timestamp"
    if start:
        where.append(f"{column} >= ?")
        params.append(start)
    if end:
        where.append(f"{column} <= ?")
        params.append(end)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _agent_condition(where: list[str], params: list[Any], agent: str | None, alias: str = "s") -> None:
    if agent and agent != "all":
        where.append(f"{alias}.agent_key = ?")
        params.append(agent)


def _session_where(
    agent: str | None = None,
    start: str | None = None,
    end: str | None = None,
    q: str | None = None,
    project: str | None = None,
) -> tuple[str, list[Any]]:
    where = ["1=1"]
    params: list[Any] = []
    _agent_condition(where, params, agent)
    column = "COALESCE(s.started_at, s.ended_at, s.created_at)"
    if start:
        where.append(f"{column} >= ?")
        params.append(start)
    if end:
        where.append(f"{column} <= ?")
        params.append(end)
    if project:
        where.append("s.project = ?")
        params.append(project)
    if q:
        needle = f"%{_escape_like(q)}%"
        where.append(
            """(s.title LIKE ? ESCAPE '\\' OR s.project LIKE ? ESCAPE '\\' OR s.cwd LIKE ? ESCAPE '\\'
                OR EXISTS (SELECT 1 FROM events se WHERE se.session_id = s.id AND se.content LIKE ? ESCAPE '\\'))"""
        )
        params.extend([needle, needle, needle, needle])
    return " AND ".join(where), params


def list_sessions(
    conn: sqlite3.Connection,
    *,
    agent: str | None = None,
    start: str | None = None,
    end: str | None = None,
    q: str | None = None,
    project: str | None = None,
    page: int = 1,
    page_size: int = 20,
    sort: str = "started_desc",
) -> dict[str, Any]:
    page_size = clamp(page_size, 1, MAX_PAGE_SIZE)
    page = max(1, page)
    where, params = _session_where(agent, start, end, q, project)
    total = conn.execute(f"SELECT COUNT(*) FROM sessions s WHERE {where}", params).fetchone()[0]
    order_by = {
        "started_desc": "COALESCE(s.started_at, s.created_at) DESC",
        "started_asc": "COALESCE(s.started_at, s.created_at) ASC",
        "events_desc": "s.event_count DESC, COALESCE(s.started_at, s.created_at) DESC",
        "updated_desc": "s.updated_at DESC",
        "title_asc": "s.title COLLATE NOCASE ASC",
    }.get(sort, "COALESCE(s.started_at, s.created_at) DESC")
    rows = conn.execute(
        f"""SELECT s.*, a.name AS agent_name, a.color AS agent_color
            FROM sessions s JOIN agents a ON a.key = s.agent_key
            WHERE {where}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?""",
        (*params, page_size, (page - 1) * page_size),
    ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


def get_session(conn: sqlite3.Connection, session_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT s.*, a.name AS agent_name, a.color AS agent_color, a.vendor AS agent_vendor
           FROM sessions s JOIN agents a ON a.key = s.agent_key WHERE s.id = ?""",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def list_session_events(
    conn: sqlite3.Connection,
    session_id: int,
    *,
    event_type: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 30,
    preview_chars: int = 12000,
) -> dict[str, Any]:
    page_size = clamp(page_size, 1, 100)
    page = max(1, page)
    where = ["session_id = ?"]
    params: list[Any] = [session_id]
    if event_type and event_type != "all":
        types = [item for item in event_type.split(",") if item in EVENT_TYPES]
        if types:
            where.append(f"type IN ({','.join('?' for _ in types)})")
            params.extend(types)
    if q:
        needle = f"%{_escape_like(q)}%"
        where.append(
            "(content LIKE ? ESCAPE '\\' OR tool_name LIKE ? ESCAPE '\\' OR tool_input_json LIKE ? ESCAPE '\\')"
        )
        params.extend([needle, needle, needle])
    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM events WHERE {where_sql}", params).fetchone()[0]
    # Only the preview slice travels out of SQLite: raw_json / metadata_json are
    # never part of the timeline payload, and a 60 KB message would otherwise be
    # copied in full just to be cut in Python.
    rows = conn.execute(
        f"""SELECT id, session_id, type, source_event_id, sequence, timestamp,
                   substr(content, 1, ?) AS content, length(content) AS content_length,
                   tool_name, tool_call_id, tool_status, tool_input_json, model,
                   tokens_input, tokens_output, metadata_json, created_at
            FROM events WHERE {where_sql}
            ORDER BY COALESCE(timestamp, created_at), sequence, id
            LIMIT ? OFFSET ?""",
        (preview_chars, *params, page_size, (page - 1) * page_size),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        if item.get("content") is None:
            item["content"] = ""
        item["content_length"] = int(item.pop("content_length") or 0)
        item["truncated"] = item["content_length"] > preview_chars
        tool_input = json_loads(item.get("tool_input_json"), None)
        item["tool_input"] = tool_input if isinstance(tool_input, (dict, list)) else (item.get("tool_input_json") or None)
        if isinstance(item["tool_input"], str) and len(item["tool_input"]) > 4000:
            item["tool_input"] = item["tool_input"][:4000] + "…"
        item.pop("fingerprint", None)
        items.append(item)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


def get_event(conn: sqlite3.Connection, event_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT e.*, s.external_id AS session_external_id, s.title AS session_title
           FROM events e JOIN sessions s ON s.id = e.session_id WHERE e.id = ?""",
        (event_id,),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["tool_input"] = json_loads(item.get("tool_input_json"), None)
    item["raw"] = json_loads(item.get("raw_json"), None)
    return item


def _type_summary(conn: sqlite3.Connection) -> tuple[dict[str, int], int]:
    """Type histogram + tool-result success count, both served by covering indexes."""
    counts = {key: 0 for key in EVENT_TYPES}
    for row in conn.execute("SELECT type, COUNT(*) AS count FROM events GROUP BY type"):
        counts[row["type"]] = row["count"]
    success = 0
    for row in conn.execute(
        "SELECT tool_status, COUNT(*) AS count FROM events WHERE type='tool_result' GROUP BY tool_status"
    ):
        if row["tool_status"] in ("success", "completed"):
            success += int(row["count"] or 0)
    return counts, success


def type_counts(conn: sqlite3.Connection, session_id: int | None = None) -> dict[str, int]:
    if not session_id:
        return _type_summary(conn)[0]
    counts = {key: 0 for key in EVENT_TYPES}
    for row in conn.execute(
        "SELECT type, COUNT(*) AS count FROM events WHERE session_id = ? GROUP BY type", (session_id,)
    ):
        counts[row["type"]] = row["count"]
    return counts


def list_agents_with_stats(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    # Three grouped joins instead of eight correlated sub-queries per agent:
    # the aggregates are computed once for the whole archive and the paid cost
    # no longer multiplies with the number of adapters.
    rows = conn.execute(
        """SELECT a.*,
             COALESCE(ses.session_count, 0) AS session_count,
             COALESCE(ev.event_count, 0) AS event_count,
             COALESCE(ev.tool_count, 0) AS tool_count,
             COALESCE(ev.event_tokens, 0) AS event_tokens,
             COALESCE(src.source_count, 0) AS source_count,
             COALESCE(src.source_errors, 0) AS source_errors,
             ev.last_event_at AS last_event_at
           FROM agents a
           LEFT JOIN (
               SELECT agent_key, COUNT(*) AS session_count FROM sessions GROUP BY agent_key
           ) ses ON ses.agent_key = a.key
           LEFT JOIN (
               SELECT agent_key,
                      COUNT(*) AS event_count,
                      SUM(CASE WHEN type = 'tool_call' THEN 1 ELSE 0 END) AS tool_count,
                      SUM(tokens_input + tokens_output) AS event_tokens,
                      MAX(timestamp) AS last_event_at
               FROM events GROUP BY agent_key
           ) ev ON ev.agent_key = a.key
           LEFT JOIN (
               SELECT agent_key,
                      COUNT(*) AS source_count,
                      SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS source_errors
               FROM sources GROUP BY agent_key
           ) src ON src.agent_key = a.key
           ORDER BY a.rowid"""
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        item["config"] = json_loads(item["config_json"], {})
        item["roots"] = item["config"].get("roots", [])
        item["patterns"] = item["config"].get("patterns", [])
        item["auto_sync"] = bool(item["config"].get("auto_sync", True))
        item["interval_minutes"] = int(item["config"].get("interval_minutes", 30))
        item["max_attempts"] = int(item["config"].get("max_attempts", 3))
        item.pop("config_json", None)
        item["status"] = item.get("status") or "idle"
        result.append(item)
    return result


def overview(conn: sqlite3.Connection, days: int = 14) -> dict[str, Any]:
    agents = list_agents_with_stats(conn)
    # The per-agent roll-up already visited every row, so the archive totals are
    # summed from it instead of scanning sessions and events again.
    total_sessions = sum(int(agent["session_count"] or 0) for agent in agents)
    total_events = sum(int(agent["event_count"] or 0) for agent in agents)
    counts, result_ok = _type_summary(conn)
    now = datetime.now(timezone.utc)
    start_24h = _utc_iso(now - timedelta(hours=24))
    start_7d = _utc_iso(now - timedelta(days=7))
    events_24h = conn.execute("SELECT COUNT(*) FROM events WHERE timestamp >= ?", (start_24h,)).fetchone()[0]
    sessions_7d = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE COALESCE(started_at, created_at) >= ?", (start_7d,)
    ).fetchone()[0]
    tokens = conn.execute(
        "SELECT COALESCE(SUM(tokens_input),0) AS input, COALESCE(SUM(tokens_output),0) AS output FROM sessions"
    ).fetchone()
    active_sources = conn.execute("SELECT COUNT(*) FROM sources WHERE status IN ('ok','warning')").fetchone()[0]
    source_errors = conn.execute("SELECT COUNT(*) FROM sources WHERE status='error'").fetchone()[0]
    jobs = [dict(row) for row in conn.execute(
        """SELECT id, agent_key, status, progress, message, attempts, max_attempts,
                  queued_at, started_at, finished_at
           FROM jobs ORDER BY id DESC LIMIT 6"""
    ).fetchall()]
    recent_sessions = [dict(row) for row in conn.execute(
        """SELECT s.id, s.agent_key, s.external_id, s.title, s.project, s.model, s.started_at, s.ended_at,
                  s.event_count, s.message_count, s.tool_call_count, s.tokens_input, s.tokens_output,
                  a.name AS agent_name, a.color AS agent_color
           FROM sessions s JOIN agents a ON a.key=s.agent_key
           ORDER BY COALESCE(s.started_at, s.created_at) DESC LIMIT 8"""
    ).fetchall()]

    since = now - timedelta(days=max(1, days) - 1)
    rows = conn.execute(
        """SELECT substr(timestamp,1,10) AS day, agent_key, type, COUNT(*) AS count
           FROM events WHERE timestamp >= ? GROUP BY day, agent_key, type ORDER BY day""",
        (_utc_iso(datetime.combine(since.date(), datetime.min.time(), tzinfo=timezone.utc)),),
    ).fetchall()
    daily_map: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: {"agents": defaultdict(int), "types": defaultdict(int)})
    for row in rows:
        daily_map[row["day"]]["agents"][row["agent_key"]] += row["count"]
        daily_map[row["day"]]["types"][row["type"]] += row["count"]
    daily = []
    for index in range(days):
        day = (since.date() + timedelta(days=index)).isoformat()
        entry = daily_map.get(day)
        values = {agent["key"]: (entry["agents"].get(agent["key"], 0) if entry else 0) for agent in agents}
        types = {key: (entry["types"].get(key, 0) if entry else 0) for key in EVENT_TYPES}
        daily.append({"day": day, "total": sum(values.values()), "agents": values, "types": types})

    tools = [dict(row) for row in conn.execute(
        """SELECT tool_name, COUNT(*) AS calls,
                  SUM(CASE WHEN tool_status IN ('success','completed') THEN 1 ELSE 0 END) AS success,
                  SUM(CASE WHEN tool_status IN ('error','failed') THEN 1 ELSE 0 END) AS errors
           FROM events WHERE type='tool_call' AND tool_name IS NOT NULL
           GROUP BY tool_name ORDER BY calls DESC LIMIT 6"""
    ).fetchall()]

    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    backup_total = conn.execute(
        "SELECT COALESCE(SUM(size_bytes),0) FROM backups WHERE status='ready'"
    ).fetchone()[0]
    backup_size = int(backup_total or 0)
    result_total = int(counts.get("tool_result") or 0)
    success_rate = round(result_ok * 100 / result_total, 1) if result_total else None

    return {
        "totals": {
            "sessions": total_sessions,
            "events": total_events,
            "agents": len(agents),
            "enabled_agents": sum(1 for agent in agents if agent["enabled"]),
            "events_24h": events_24h,
            "sessions_7d": sessions_7d,
            "tokens_input": int(tokens["input"] or 0),
            "tokens_output": int(tokens["output"] or 0),
            "active_sources": active_sources,
            "source_errors": source_errors,
            "db_size": db_size,
            "backup_size": backup_size,
            "tool_success_rate": success_rate,
        },
        "type_counts": counts,
        "agents": agents,
        "daily": daily,
        "recent_sessions": recent_sessions,
        "jobs": jobs,
        "top_tools": tools,
    }


def analytics(
    conn: sqlite3.Connection,
    *,
    agent: str | None = None,
    start: str | None = None,
    end: str | None = None,
    days: int = 30,
) -> dict[str, Any]:
    where = ["1=1"]
    params: list[Any] = []
    if agent and agent != "all":
        where.append("e.agent_key = ?")
        params.append(agent)
    _append_range(where, params, "e", start, end)
    where_sql = " AND ".join(where)
    summary = conn.execute(
        f"""SELECT COUNT(*) AS events,
                   SUM(CASE WHEN e.type='user_message' THEN 1 ELSE 0 END) AS user_messages,
                   SUM(CASE WHEN e.type='assistant_message' THEN 1 ELSE 0 END) AS assistant_messages,
                   SUM(CASE WHEN e.type='tool_call' THEN 1 ELSE 0 END) AS tool_calls,
                   SUM(CASE WHEN e.type='tool_result' THEN 1 ELSE 0 END) AS tool_results,
                   COALESCE(SUM(e.tokens_input),0) AS tokens_input,
                   COALESCE(SUM(e.tokens_output),0) AS tokens_output,
                   COUNT(DISTINCT e.session_id) AS sessions,
                   SUM(CASE WHEN e.type='tool_result' AND e.tool_status IN ('success','completed') THEN 1 ELSE 0 END) AS tool_ok,
                   SUM(CASE WHEN e.type='tool_result' AND e.tool_status IN ('error','failed') THEN 1 ELSE 0 END) AS tool_failed
            FROM events e WHERE {where_sql}""",
        params,
    ).fetchone()
    total_events = int(summary["events"] or 0)
    tool_results = int(summary["tool_results"] or 0)
    success_rate = round((summary["tool_ok"] or 0) * 100 / tool_results, 1) if tool_results else None

    start_dt = parse_bound(start) or (datetime.now(timezone.utc) - timedelta(days=max(1, days) - 1))
    end_dt = parse_bound(end) or datetime.now(timezone.utc)
    day_count = max(1, min(180, (end_dt.date() - start_dt.date()).days + 1))
    daily_rows = conn.execute(
        f"""SELECT substr(e.timestamp,1,10) AS day, e.agent_key, e.type, COUNT(*) AS count
            FROM events e WHERE {where_sql} GROUP BY day, e.agent_key, e.type ORDER BY day""",
        params,
    ).fetchall()
    daily_map: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "agents": defaultdict(int), "types": defaultdict(int)}
    )
    for row in daily_rows:
        day = row["day"]
        daily_map[day]["agents"][row["agent_key"]] += row["count"]
        daily_map[day]["types"][row["type"]] += row["count"]
        daily_map[day]["total"] += row["count"]
    daily = []
    for index in range(day_count):
        day = (start_dt.date() + timedelta(days=index)).isoformat()
        values = daily_map.get(day)
        daily.append({
            "day": day,
            "total": values["total"] if values else 0,
            "agents": dict(values["agents"]) if values else {},
            "types": {key: (values["types"].get(key, 0) if values else 0) for key in EVENT_TYPES},
        })

    agent_rows = conn.execute(
        f"""SELECT e.agent_key, COUNT(*) AS events,
                   COUNT(DISTINCT e.session_id) AS sessions,
                   SUM(CASE WHEN e.type='user_message' THEN 1 ELSE 0 END) AS user_messages,
                   SUM(CASE WHEN e.type='assistant_message' THEN 1 ELSE 0 END) AS assistant_messages,
                   SUM(CASE WHEN e.type='tool_call' THEN 1 ELSE 0 END) AS tool_calls,
                   SUM(CASE WHEN e.type='tool_result' THEN 1 ELSE 0 END) AS tool_results,
                   COALESCE(SUM(e.tokens_input),0) AS tokens_input,
                   COALESCE(SUM(e.tokens_output),0) AS tokens_output
            FROM events e WHERE {where_sql} GROUP BY e.agent_key ORDER BY events DESC""",
        params,
    ).fetchall()
    agent_meta = {row["key"]: dict(row) for row in conn.execute("SELECT key, name, color FROM agents")}
    by_agent = []
    for row in agent_rows:
        item = dict(row)
        meta = agent_meta.get(item["agent_key"], {})
        item["name"] = meta.get("name", item["agent_key"]) if meta else item["agent_key"]
        item["color"] = meta.get("color", "#8a8478") if meta else "#8a8478"
        item["avg_events"] = round(item["events"] / item["sessions"], 1) if item["sessions"] else 0
        by_agent.append(item)

    tools = [dict(row) for row in conn.execute(
        f"""SELECT tool_name, COUNT(*) AS calls,
                   SUM(CASE WHEN tool_status IN ('success','completed') THEN 1 ELSE 0 END) AS success,
                   SUM(CASE WHEN tool_status IN ('error','failed') THEN 1 ELSE 0 END) AS errors
            FROM events e WHERE {where_sql} AND e.type='tool_call' AND tool_name IS NOT NULL
            GROUP BY tool_name ORDER BY calls DESC LIMIT 12""",
        params,
    ).fetchall()]

    projects = [dict(row) for row in conn.execute(
        f"""SELECT COALESCE(s.project,'未标注') AS project, COUNT(DISTINCT e.session_id) AS sessions, COUNT(*) AS events
            FROM events e JOIN sessions s ON s.id=e.session_id
            WHERE {where_sql} GROUP BY project ORDER BY events DESC LIMIT 8""",
        params,
    ).fetchall()]

    return {
        "summary": {
            "events": total_events,
            "sessions": int(summary["sessions"] or 0),
            "user_messages": int(summary["user_messages"] or 0),
            "assistant_messages": int(summary["assistant_messages"] or 0),
            "tool_calls": int(summary["tool_calls"] or 0),
            "tool_results": tool_results,
            "tokens_input": int(summary["tokens_input"] or 0),
            "tokens_output": int(summary["tokens_output"] or 0),
            "tool_success_rate": success_rate,
            "tool_failures": int(summary["tool_failed"] or 0),
            "avg_events_per_session": round(total_events / summary["sessions"], 1) if summary["sessions"] else 0,
        },
        "type_counts": {
            "user_messages": int(summary["user_messages"] or 0),
            "assistant_messages": int(summary["assistant_messages"] or 0),
            "tool_calls": int(summary["tool_calls"] or 0),
            "tool_results": int(summary["tool_results"] or 0),
        },
        "daily": daily,
        "by_agent": by_agent,
        "top_tools": tools,
        "top_projects": projects,
        "range": {"start": start, "end": end},
    }


def list_jobs(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    agent: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    page_size = clamp(page_size, 1, MAX_PAGE_SIZE)
    page = max(1, page)
    where = ["1=1"]
    params: list[Any] = []
    if status and status != "all":
        where.append("j.status = ?")
        params.append(status)
    if agent and agent != "all":
        where.append("j.agent_key = ?")
        params.append(agent)
    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM jobs j WHERE {where_sql}", params).fetchone()[0]
    rows = conn.execute(
        f"""SELECT j.*, a.name AS agent_name, a.color AS agent_color
            FROM jobs j JOIN agents a ON a.key=j.agent_key
            WHERE {where_sql} ORDER BY j.id DESC LIMIT ? OFFSET ?""",
        (*params, page_size, (page - 1) * page_size),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["stats"] = json_loads(item.get("stats_json"), {})
        items.append(item)
    return {
        "items": items, "total": total, "page": page, "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


def list_sources(
    conn: sqlite3.Connection,
    *,
    agent: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    page_size = clamp(page_size, 1, MAX_PAGE_SIZE)
    page = max(1, page)
    where = ["1=1"]
    params: list[Any] = []
    if agent and agent != "all":
        where.append("src.agent_key = ?")
        params.append(agent)
    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM sources src WHERE {where_sql}", params).fetchone()[0]
    rows = conn.execute(
        f"""SELECT src.*, a.name AS agent_name, a.color AS agent_color
            FROM sources src JOIN agents a ON a.key=src.agent_key
            WHERE {where_sql} ORDER BY src.updated_at DESC LIMIT ? OFFSET ?""",
        (*params, page_size, (page - 1) * page_size),
    ).fetchall()
    return {
        "items": [dict(row) for row in rows], "total": total, "page": page,
        "page_size": page_size, "pages": max(1, (total + page_size - 1) // page_size),
    }


def list_logs(
    conn: sqlite3.Connection,
    *,
    level: str | None = None,
    module: str | None = None,
    page: int = 1,
    page_size: int = 30,
) -> dict[str, Any]:
    page_size = clamp(page_size, 1, MAX_PAGE_SIZE)
    page = max(1, page)
    where = ["1=1"]
    params: list[Any] = []
    if level and level != "all":
        where.append("level = ?")
        params.append(level)
    if module:
        where.append("module = ?")
        params.append(module)
    where_sql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM system_logs WHERE {where_sql}", params).fetchone()[0]
    rows = conn.execute(
        f"""SELECT * FROM system_logs WHERE {where_sql}
            ORDER BY id DESC LIMIT ? OFFSET ?""",
        (*params, page_size, (page - 1) * page_size),
    ).fetchall()
    return {
        "items": [dict(row) for row in rows], "total": total, "page": page,
        "page_size": page_size, "pages": max(1, (total + page_size - 1) // page_size),
    }


def list_audit_logs(conn: sqlite3.Connection, *, page: int = 1, page_size: int = 30) -> dict[str, Any]:
    page_size = clamp(page_size, 1, MAX_PAGE_SIZE)
    page = max(1, page)
    total = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    rows = conn.execute(
        "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ? OFFSET ?",
        (page_size, (page - 1) * page_size),
    ).fetchall()
    return {
        "items": [dict(row) for row in rows], "total": total, "page": page,
        "page_size": page_size, "pages": max(1, (total + page_size - 1) // page_size),
    }


def global_search(conn: sqlite3.Connection, q: str, limit: int = 8) -> dict[str, Any]:
    if not q or len(q.strip()) < 1:
        return {"sessions": [], "events": []}
    needle = f"%{_escape_like(q.strip())}%"
    sessions = [dict(row) for row in conn.execute(
        """SELECT s.id, s.agent_key, s.title, s.project, s.started_at,
                  a.name AS agent_name, a.color AS agent_color
           FROM sessions s JOIN agents a ON a.key=s.agent_key
           WHERE s.title LIKE ? ESCAPE '\\' OR s.project LIKE ? ESCAPE '\\'
           ORDER BY COALESCE(s.started_at,s.created_at) DESC LIMIT ?""",
        (needle, needle, limit),
    ).fetchall()]
    events = [dict(row) for row in conn.execute(
        """SELECT e.id, e.session_id, e.type, e.agent_key, e.timestamp,
                  substr(e.content,1,240) AS preview, s.title AS session_title,
                  a.name AS agent_name, a.color AS agent_color
           FROM events e JOIN sessions s ON s.id=e.session_id JOIN agents a ON a.key=e.agent_key
           WHERE e.content LIKE ? ESCAPE '\\'
           ORDER BY e.timestamp DESC LIMIT ?""",
        (needle, limit),
    ).fetchall()]
    return {"sessions": sessions, "events": events}
