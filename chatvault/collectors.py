from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

from .constants import AGENT_MAP
from .parsers import (
    parse_claude_jsonl,
    parse_codex_jsonl,
    parse_dsh_jsonl,
    parse_generic_document,
    parse_generic_jsonl,
)
from .utils import expand_path, iso_from_any, json_loads, shorten, safe_json, utc_now


ProgressFn = Callable[[int, str], None]


@dataclass
class Source:
    path: str
    source_type: str
    size_bytes: int = 0
    mtime_ns: int = 0


def _now_iso_from_ns(value: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def read_zstd(path: Path) -> str:
    raw = path.read_bytes()
    try:
        import zstandard  # type: ignore
        decompressor = zstandard.ZstdDecompressor()
        with path.open("rb") as handle:
            return decompressor.stream_reader(handle).read().decode("utf-8", "replace")
    except ImportError:
        pass
    except Exception as exc:
        raise RuntimeError(f"zstd 解压失败: {exc}") from exc
    executable = shutil.which("zstd") or os.environ.get("AGENTTRAIL_ZSTD")
    if executable:
        try:
            proc = subprocess.run(
                [executable, "-d", "-c", str(path)],
                capture_output=True, check=True, timeout=120,
            )
            return proc.stdout.decode("utf-8", "replace")
        except Exception as exc:
            raise RuntimeError(f"zstd 解压失败: {exc}") from exc
    raise RuntimeError("该来源为 zstd 压缩格式，请安装可选依赖 zstandard（pip install zstandard）")


def discover_sources(agent: dict[str, Any]) -> tuple[list[Source], list[str]]:
    """Discover readable source files for an agent.

    Missing roots are warnings, not failures: agents are commonly absent on a
    given workstation and the collector should simply report nothing to scan.
    """
    key = agent["key"]
    config = agent.get("config") or {}
    roots = [expand_path(str(root)) for root in config.get("roots", []) if str(root).strip()]
    patterns = config.get("patterns") or AGENT_MAP.get(key, {}).get("patterns") or ["*.jsonl"]
    max_files = int(config.get("max_files", 5000))
    max_source_mb = int(config.get("max_source_mb", 1024))
    sources: list[Source] = []
    warnings: list[str] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        try:
            if not path.is_file():
                return
            resolved = str(path.resolve())
            if resolved in seen:
                return
            seen.add(resolved)
            stat = path.stat()
            if stat.st_size > max_source_mb * 1024 * 1024:
                warnings.append(f"跳过超大文件（>{max_source_mb}MB）: {path}")
                return
            suffix = path.name.lower()
            if suffix.endswith(".jsonl.zstd") or suffix.endswith(".zst"):
                source_type = "jsonl.zstd"
            elif suffix.endswith(".jsonl"):
                source_type = "jsonl"
            elif suffix.endswith((".db", ".sqlite", ".sqlite3")):
                source_type = "sqlite"
            elif suffix.endswith(".json"):
                source_type = "json"
            else:
                source_type = "jsonl"
            sources.append(Source(str(path), source_type, stat.st_size, stat.st_mtime_ns))
        except OSError as exc:
            warnings.append(f"无法读取 {path}: {exc}")

    for root_text in roots:
        root = Path(root_text)
        if not root.exists():
            warnings.append(f"数据目录不存在: {root_text}")
            continue
        if root.is_file():
            if root.name == "opencode.db" or root.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                add(root)
            else:
                add(root)
            continue
        # SQLite-based agents usually point directly at the database, but a
        # directory root is convenient and should still work.
        if key == "opencode":
            for candidate in root.glob("opencode.db"):
                add(candidate)
            continue
        for pattern in patterns:
            try:
                for candidate in root.rglob(pattern):
                    add(candidate)
                    if len(sources) >= max_files:
                        warnings.append(f"达到单次扫描上限 {max_files} 个文件，剩余文件将被忽略")
                        break
            except (OSError, ValueError) as exc:
                warnings.append(f"扫描失败 {root_text} / {pattern}: {exc}")
            if len(sources) >= max_files:
                break
        if len(sources) >= max_files:
            break
    sources.sort(key=lambda item: item.path)
    return sources, warnings


def default_session_id(agent_key: str, path: str) -> str:
    p = Path(path)
    if agent_key == "deepseekharness":
        return p.parent.name
    if agent_key == "codex":
        stem = p.name
        if stem.endswith(".jsonl.zstd"):
            stem = stem[:-11]
        elif stem.endswith(".jsonl"):
            stem = stem[:-6]
        import re
        match = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", stem)
        return match.group(1) if match else stem
    stem = p.name
    for suffix in (".jsonl.zstd", ".jsonl", ".json", ".zst"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem


def parse_text(
    agent_key: str,
    text: str,
    path: str,
    fallback_time: str,
    *,
    start_sequence: int = 0,
    source_type: str = "jsonl",
) -> dict[str, Any]:
    sid = default_session_id(agent_key, path)
    if agent_key == "codex":
        return parse_codex_jsonl(text.splitlines(), path, fallback_time, sid, start_sequence=start_sequence)
    if agent_key in {"claudecode", "workbuddy"}:
        return parse_claude_jsonl(
            text.splitlines(), path, fallback_time, sid,
            agent_key=agent_key, start_sequence=start_sequence,
        )
    if agent_key == "deepseekharness":
        return parse_dsh_jsonl(text.splitlines(), path, fallback_time, sid, start_sequence=start_sequence)
    if source_type == "json":
        return parse_generic_document(
            text, path, fallback_time, sid, agent_key=agent_key, start_sequence=start_sequence
        )
    return parse_generic_jsonl(
        text.splitlines(), path, fallback_time, sid, agent_key=agent_key, start_sequence=start_sequence
    )


def collect_file_source(
    agent_key: str,
    source: Source,
    state: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read one file source and return (parsed_batch, next_cursor)."""
    path = Path(source.path)
    stat = path.stat()
    fallback_time = _now_iso_from_ns(stat.st_mtime_ns)
    cursor = dict(state or {})

    if source.source_type in {"jsonl.zstd"}:
        # Compressed streams can be appended as additional zstd frames; if the
        # compressed file is untouched, skip it. A changed file is reparsed and
        # per-event fingerprints remove duplicates.
        if cursor.get("size") == stat.st_size and cursor.get("mtime_ns") == stat.st_mtime_ns and cursor.get("done"):
            return {"agent_key": agent_key, "source_path": source.path, "sessions": [], "events": [], "errors": []}, cursor
        text = read_zstd(path)
        batch = parse_text(agent_key, text, source.path, fallback_time, source_type="jsonl")
        next_cursor = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "done": True}
        return batch, next_cursor

    if source.source_type == "json":
        if cursor.get("size") == stat.st_size and cursor.get("mtime_ns") == stat.st_mtime_ns and cursor.get("done"):
            return {"agent_key": agent_key, "source_path": source.path, "sessions": [], "events": [], "errors": []}, cursor
        text = path.read_text(encoding="utf-8", errors="replace")
        batch = parse_text(agent_key, text, source.path, fallback_time, source_type="json")
        next_cursor = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "done": True}
        return batch, next_cursor

    if source.source_type == "sqlite":
        raise RuntimeError("SQLite 来源必须由专用采集器处理")

    # Uncompressed JSONL: read complete new lines from the byte offset.
    offset = int(cursor.get("offset") or 0)
    if stat.st_size < offset:
        offset = 0
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
    last_newline = data.rfind(b"\n")
    if last_newline < 0:
        # No complete line yet. Do not advance, otherwise a partial JSON line
        # would be lost when the writer finishes it.
        return {"agent_key": agent_key, "source_path": source.path, "sessions": [], "events": [], "errors": []}, {
            "offset": offset,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    consumed = last_newline + 1
    text = data[:consumed].decode("utf-8", errors="replace")
    batch = parse_text(agent_key, text, source.path, fallback_time, start_sequence=offset, source_type="jsonl")
    next_cursor = {
        "offset": offset + consumed,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    return batch, next_cursor


# ---------------------------------------------------------------------------
# OpenCode SQLite adapter
# ---------------------------------------------------------------------------

def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def collect_opencode_source(source: Source, state: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    path = Path(source.path)
    cursor = dict(state or {})
    last_updated = int(cursor.get("updated_ms") or 0)
    uri_path = path.as_posix().replace("?", "%3F").replace("#", "%23")
    conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "session_v2" not in tables or "session_message" not in tables:
            raise RuntimeError(
                "未识别的 OpenCode 数据库结构（需要 session_v2 / session_message 表）。"
                "请确认已升级到新版 OpenCode，或配置其他数据源。"
            )
        messages = conn.execute(
            """SELECT id, session_id, type, seq, time_created, time_updated, data
               FROM session_message WHERE time_updated > ? ORDER BY time_created, seq""",
            (last_updated,),
        ).fetchall()
        if not messages:
            return {"agent_key": "opencode", "source_path": source.path, "sessions": [], "events": [], "errors": []}, cursor
        session_ids = sorted({row["session_id"] for row in messages})
        placeholders = ",".join("?" for _ in session_ids)
        sessions = conn.execute(
            f"""SELECT s.*, p.worktree AS project_worktree, p.name AS project_name
                FROM session_v2 s LEFT JOIN project p ON p.id = s.project_id
                WHERE s.id IN ({placeholders})""",
            session_ids,
        ).fetchall()
    finally:
        conn.close()

    # Reuse ParsedBatch for canonical structures.
    from .parsers import ParsedBatch
    fallback = _now_iso_from_ns(int(source.mtime_ns or 0)) if source.mtime_ns else utc_now()
    batch = ParsedBatch("opencode", source.path, fallback, Path(source.path).stem)
    session_info: dict[str, sqlite3.Row] = {row["id"]: row for row in sessions}
    next_updated = last_updated

    for row in sessions:
        model = None
        try:
            raw_model = row["model"]
            if raw_model:
                parsed_model = json.loads(raw_model) if isinstance(raw_model, str) else raw_model
                if isinstance(parsed_model, dict):
                    model = parsed_model.get("id") or parsed_model.get("name") or parsed_model.get("modelID")
        except (TypeError, ValueError):
            model = None
        cwd = row["directory"] or row["project_worktree"] or row["project_name"]
        batch.session(
            row["id"],
            title=row["title"] or "",
            project=(Path(cwd).name if cwd else None),
            cwd=cwd,
            model=model,
            started_at=row["time_created"],
            ended_at=row["time_updated"],
            tokens_input=row["tokens_input"],
            tokens_output=row["tokens_output"],
            metadata={
                "slug": row["slug"],
                "agent": row["agent"],
                "cost": row["cost"],
                "version": row["version"],
                "directory": cwd,
            },
        )

    for row in messages:
        session_id = row["session_id"]
        info = session_info.get(session_id)
        if not info:
            continue
        try:
            data = json.loads(row["data"])
        except (TypeError, ValueError):
            continue
        timestamp = row["time_created"] or row["time_updated"]
        message_id = row["id"]
        msg_type = row["type"]
        model = None
        if isinstance(data.get("model"), dict):
            model = data["model"].get("id") or data["model"].get("name")
        elif isinstance(data.get("model"), str):
            model = data["model"]

        if msg_type == "user":
            text = data.get("text")
            if not text:
                text = _as_dict(data.get("metadata")).get("displayText")
            text = (text or "").strip()
            if text:
                batch.event(
                    session_id, "user_message",
                    source_event_id=message_id,
                    timestamp=timestamp,
                    content=text,
                    metadata={
                        "attachments": data.get("attachments") or [],
                        "files": data.get("files") or [],
                    },
                    raw=data,
                    sequence=int(row["seq"] or 0) * 1000,
                )
        elif msg_type == "assistant":
            content = data.get("content") or []
            text = ""
            if isinstance(content, list):
                for block_index, block in enumerate(content):
                    if not isinstance(block, dict):
                        continue
                    block_type = str(block.get("type") or "")
                    if block_type == "text":
                        block_text = block.get("text") or ""
                        if block_text:
                            text = (text + "\n\n" + block_text).strip()
                    elif block_type == "tool":
                        call_id = block.get("id") or f"{message_id}:tool:{block_index}"
                        tool_state = _as_dict(block.get("state"))
                        status = tool_state.get("status") or ("completed" if tool_state.get("completed") else "requested")
                        batch.event(
                            session_id, "tool_call",
                            source_event_id=f"{message_id}:call:{block_index}",
                            timestamp=timestamp,
                            content="",
                            tool_name=block.get("name") or "unknown",
                            tool_call_id=call_id,
                            tool_status=status,
                            tool_input=tool_state.get("input"),
                            model=model,
                            raw=block,
                            sequence=int(row["seq"] or 0) * 1000 + block_index + 1,
                        )
                        output_blocks = tool_state.get("content")
                        if output_blocks is None:
                            output_blocks = tool_state.get("output")
                        output_text = ""
                        if isinstance(output_blocks, str):
                            output_text = output_blocks
                        elif isinstance(output_blocks, list):
                            from .utils import text_from_blocks
                            output_text = text_from_blocks(output_blocks)
                        elif output_blocks is not None:
                            output_text = safe_json(output_blocks) or str(output_blocks)
                        if output_text or status in {"completed", "error", "failed"}:
                            batch.event(
                                session_id, "tool_result",
                                source_event_id=f"{message_id}:result:{block_index}",
                                timestamp=block.get("time", {}).get("completed") if isinstance(block.get("time"), dict) else timestamp,
                                content=output_text or "",
                                tool_name=block.get("name") or "unknown",
                                tool_call_id=call_id,
                                tool_status="error" if status in {"error", "failed"} else "success",
                                metadata={"json": tool_state.get("metadata")},
                                raw=block,
                                sequence=int(row["seq"] or 0) * 1000 + block_index + 2,
                            )
            if text:
                batch.event(
                    session_id, "assistant_message",
                    source_event_id=f"{message_id}:text",
                    timestamp=timestamp,
                    content=text,
                    model=model,
                    tokens_input=data.get("tokens", {}).get("input") if isinstance(data.get("tokens"), dict) else None,
                    tokens_output=data.get("tokens", {}).get("output") if isinstance(data.get("tokens"), dict) else None,
                    metadata={"agent": data.get("agent")},
                    raw=data,
                    sequence=int(row["seq"] or 0) * 1000 + 900,
                )
        next_updated = max(next_updated, int(row["time_updated"] or 0))

    result = batch.finalize()
    for session in result["sessions"]:
        if not session.get("title") or session["title"].startswith("opencode ·"):
            from .parsers import _derive_title_from_events
            session["title"] = _derive_title_from_events(
                result["events"], session["external_id"], "OpenCode", session.get("project")
            )
    next_cursor = {"updated_ms": next_updated, "size": source.size_bytes, "mtime_ns": source.mtime_ns, "done": True}
    return result, next_cursor


def collect_source(agent_key: str, source: Source, state: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    if source.source_type == "sqlite":
        if agent_key != "opencode":
            raise RuntimeError(f"{agent_key} 暂不支持 SQLite 来源")
        return collect_opencode_source(source, state)
    return collect_file_source(agent_key, source, state)
