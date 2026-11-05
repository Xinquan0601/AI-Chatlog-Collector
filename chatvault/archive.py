"""Read AgentTrail export files written on another machine.

The data export page produces a ZIP bundle (events.csv + sessions.csv +
manifest.json + summary.json + data_dictionary.md) or a flat CSV / JSONL /
JSON event list.  Every event carries the fingerprint computed by the collector
that wrote it, so importing the same bundle twice - or importing a bundle that
overlaps the local archive - only inserts genuinely new records.

This module only parses, validates and counts.  importing.run_archive_import
performs the actual writes through the regular ingestion pipeline.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .constants import EVENT_TYPES

CORE_COLUMNS = {"agent", "session_id", "event_type", "fingerprint"}
# Exporting prefixes cells that start with these characters with an apostrophe
# so spreadsheets do not evaluate them as formulas.  The import side removes
# that guard again, otherwise chat content starting with "-" or "@" would gain
# a stray apostrophe.
CSV_GUARD_PREFIX = ("=", "+", "-", "@", "\t", "\r")
ARCHIVE_SUFFIXES = {".zip", ".csv", ".jsonl", ".json"}
FINGERPRINT_CHUNK = 400


def _raise_csv_field_limit() -> None:
    """Lift the stdlib 128 KiB per-field cap.

    Tool results and web-search outputs routinely exceed it, and the CSV module
    raises "field larger than field limit" instead of returning the row.
    """
    limit = sys.maxsize
    while limit > 131072:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:  # pragma: no cover - 32-bit builds
            limit //= 10


_raise_csv_field_limit()


class ArchiveError(RuntimeError):
    """The selected file is not a readable AgentTrail export."""


def is_archive_file(path: str | Path) -> bool:
    """Cheap extension check; the content is validated when the file is opened."""
    candidate = Path(path).expanduser()
    if not candidate.is_file():
        return False
    return candidate.suffix.lower() in ARCHIVE_SUFFIXES


def _csv_unescape(value: Any) -> Any:
    if isinstance(value, str) and len(value) > 1 and value[0] == "'" and value[1] in CSV_GUARD_PREFIX:
        return value[1:]
    return value


def _csv_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _csv_unescape(value) for key, value in row.items() if key is not None}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def normalize_event(row: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Map one exported row onto the ingestion event shape.

    The exported fingerprint is reused verbatim: it is the identity the source
    machine computed, so it matches local events for the same agent session.
    """
    agent = _text(row.get("agent"))
    session_id = _text(row.get("session_id"))
    event_type = _text(row.get("event_type"))
    fingerprint = _text(row.get("fingerprint"))
    if not agent or not session_id:
        return None, "缺少 agent 或 session_id"
    if event_type not in EVENT_TYPES:
        return None, f"未知事件类型 {event_type or '(空)'}"
    if not fingerprint:
        return None, "缺少 fingerprint"
    return {
        "agent_key": agent,
        "session_external_id": session_id,
        "type": event_type,
        "sequence": _int(row.get("sequence")) or 0,
        "timestamp": _text(row.get("timestamp")) or None,
        "content": row.get("content") or "",
        "tool_name": _text(row.get("tool_name")) or None,
        "tool_call_id": _text(row.get("tool_call_id")) or None,
        "tool_status": _text(row.get("tool_status")) or None,
        "tool_input_json": row.get("tool_input_json") or None,
        "model": _text(row.get("model")) or None,
        "tokens_input": _int(row.get("tokens_input")),
        "tokens_output": _int(row.get("tokens_output")),
        "fingerprint": fingerprint,
    }, None


# ---------------------------------------------------------------------------
# Bundle readers
# ---------------------------------------------------------------------------

class Bundle:
    """An opened export.  iter_events streams rows, never loading them all."""

    def __init__(
        self,
        path: Path,
        kind: str,
        manifest: dict[str, Any],
        sessions: dict[tuple[str, str], dict[str, Any]],
        archive: zipfile.ZipFile | None = None,
    ) -> None:
        self.path = path
        self.kind = kind
        self.manifest = manifest
        self.sessions = sessions
        self._archive = archive

    @property
    def exported_at(self) -> str | None:
        return _text(self.manifest.get("generated_at")) or None

    @property
    def expected_events(self) -> int:
        counts = self.manifest.get("counts")
        if isinstance(counts, dict):
            value = _int(counts.get("events"))
            if value is not None:
                return value
        return 0

    def iter_events(self) -> Iterator[dict[str, Any]]:
        if self.kind == "bundle":
            assert self._archive is not None
            with self._archive.open("events.csv") as raw:
                with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
                    yield from _csv_events(text, self.path)
            return
        if self.kind == "csv":
            with self.path.open("r", encoding="utf-8-sig", newline="") as text:
                yield from _csv_events(text, self.path)
            return
        if self.kind == "jsonl":
            with self.path.open("r", encoding="utf-8-sig") as text:
                for line_number, line in enumerate(text, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ArchiveError(f"第 {line_number} 行不是合法 JSON：{exc}") from exc
                    if isinstance(row, dict):
                        yield row
            return
        if self.kind == "json":
            yield from _json_events(self.path)
            return
        raise ArchiveError(f"不支持的导出格式：{self.kind}")

    def close(self) -> None:
        if self._archive is not None:
            self._archive.close()
            self._archive = None


def _csv_events(handle: io.TextIOBase, path: Path) -> Iterator[dict[str, Any]]:
    reader = csv.DictReader(handle)
    _check_columns(reader.fieldnames, path)
    for row in reader:
        yield _csv_row(row)


def _check_columns(fieldnames: list[str] | None, path: Path) -> None:
    present = set(fieldnames or [])
    missing = CORE_COLUMNS - present
    if missing:
        raise ArchiveError(
            f"{path.name} 缺少必要字段：{', '.join(sorted(missing))}"
        )


def _read_sessions(zf: zipfile.ZipFile, names: set[str]) -> dict[tuple[str, str], dict[str, Any]]:
    sessions: dict[tuple[str, str], dict[str, Any]] = {}
    if "sessions.csv" not in names:
        return sessions
    with zf.open("sessions.csv") as raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
            for raw_row in csv.DictReader(text):
                row = _csv_row(raw_row)
                agent = _text(row.get("agent_key"))
                external_id = _text(row.get("external_id"))
                if not agent or not external_id:
                    continue
                sessions[(agent, external_id)] = {
                    "external_id": external_id,
                    "title": _text(row.get("title")) or None,
                    "project": _text(row.get("project")) or None,
                    "model": _text(row.get("model")) or None,
                    "cwd": _text(row.get("cwd")) or None,
                    "started_at": _text(row.get("started_at")) or None,
                    "ended_at": _text(row.get("ended_at")) or None,
                    "tokens_input": _int(row.get("tokens_input")) or 0,
                    "tokens_output": _int(row.get("tokens_output")) or 0,
                    "source_path": _text(row.get("source_path")) or None,
                }
    return sessions


def _read_manifest(zf: zipfile.ZipFile, names: set[str]) -> dict[str, Any]:
    if "manifest.json" not in names:
        return {}
    try:
        with zf.open("manifest.json") as raw:
            data = json.loads(raw.read().decode("utf-8", "replace"))
    except (json.JSONDecodeError, KeyError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


@contextmanager
def open_bundle(path: str | Path) -> Iterator[Bundle]:
    """Open a bundle/CSV/JSONL/JSON export and validate its basic shape."""
    candidate = Path(path).expanduser()
    if not candidate.is_file():
        raise ArchiveError(f"文件不存在：{candidate}")
    if zipfile.is_zipfile(candidate):
        with zipfile.ZipFile(candidate) as zf:
            names = set(zf.namelist())
            if "events.csv" not in names:
                raise ArchiveError("压缩包内没有 events.csv，不是 AgentTrail 分析包")
            bundle = Bundle(
                candidate, "bundle", _read_manifest(zf, names), _read_sessions(zf, names), zf
            )
            try:
                yield bundle
            finally:
                bundle.close()
        return
    suffix = candidate.suffix.lower()
    if suffix == ".csv":
        with candidate.open("r", encoding="utf-8-sig", newline="") as text:
            reader = csv.reader(text)
            _check_columns(next(reader, None), candidate)
        yield Bundle(candidate, "csv", {}, {})
        return
    if suffix == ".jsonl":
        with candidate.open("r", encoding="utf-8-sig") as text:
            for line in text:
                if line.strip():
                    json.loads(line)
                    break
        yield Bundle(candidate, "jsonl", {}, {})
        return
    if suffix == ".json":
        with candidate.open("r", encoding="utf-8-sig") as text:
            head = text.read(8192)
        if '"events"' not in head:
            raise ArchiveError("JSON 文件缺少 events 数组，不是 AgentTrail 导出")
        yield Bundle(candidate, "json", {}, {})
        return
    raise ArchiveError(f"不支持的导入文件：{candidate.name}")


class _JsonEventStream:
    """Incremental reader for the pretty-printed JSON export's events array."""

    def __init__(self, path: Path) -> None:
        self._handle = path.open("r", encoding="utf-8-sig")
        self._buffer = ""
        self._pos = 0
        self._eof = False
        self._decoder = json.JSONDecoder()

    def _fill(self) -> bool:
        if self._eof:
            return False
        chunk = self._handle.read(262144)
        if not chunk:
            self._eof = True
            return False
        if self._pos:
            self._buffer = self._buffer[self._pos:]
            self._pos = 0
        self._buffer += chunk
        return True

    def __iter__(self) -> Iterator[dict[str, Any]]:
        pattern = re.compile(r'"events"\s*:\s*\[')
        while True:
            match = pattern.search(self._buffer)
            if match:
                self._pos = match.end()
                break
            if not self._fill():
                raise ArchiveError("JSON 导出缺少 events 数组")
        while True:
            while self._pos < len(self._buffer) and self._buffer[self._pos] in " \t\r\n,":
                self._pos += 1
            if self._pos >= len(self._buffer):
                if not self._fill():
                    raise ArchiveError("JSON 导出不完整")
                continue
            if self._buffer[self._pos] == "]":
                return
            try:
                item, end = self._decoder.raw_decode(self._buffer, self._pos)
            except json.JSONDecodeError:
                if not self._fill():
                    raise ArchiveError("JSON 导出不完整")
                continue
            self._pos = end
            if isinstance(item, dict):
                yield item

    def close(self) -> None:
        self._handle.close()


def _json_events(path: Path) -> Iterator[dict[str, Any]]:
    stream = _JsonEventStream(path)
    try:
        yield from stream
    finally:
        stream.close()


# ---------------------------------------------------------------------------
# Preview against the local archive
# ---------------------------------------------------------------------------

def _chunks(items: list[Any], size: int = FINGERPRINT_CHUNK) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def existing_fingerprints(conn: sqlite3.Connection, fingerprints: list[str]) -> set[str]:
    found: set[str] = set()
    for chunk in _chunks(fingerprints):
        placeholders = ",".join("?" * len(chunk))
        found.update(
            row[0] for row in conn.execute(
                f"SELECT DISTINCT fingerprint FROM events WHERE fingerprint IN ({placeholders})",
                chunk,
            )
        )
    return found


def session_count(conn: sqlite3.Connection, keys: set[tuple[str, str]]) -> int:
    """How many of the given (agent, external_id) sessions already exist."""
    return len(existing_session_keys(conn, keys))


def existing_session_keys(
    conn: sqlite3.Connection, keys: set[tuple[str, str]]
) -> set[tuple[str, str]]:
    by_agent: dict[str, list[str]] = {}
    for agent, external_id in keys:
        by_agent.setdefault(agent, []).append(external_id)
    found: set[tuple[str, str]] = set()
    for agent, ids in by_agent.items():
        for chunk in _chunks(ids):
            placeholders = ",".join("?" * len(chunk))
            for row in conn.execute(
                f"SELECT agent_key, external_id FROM sessions WHERE agent_key = ? AND external_id IN ({placeholders})",
                (agent, *chunk),
            ):
                found.add((row[0], row[1]))
    return found


def preview(path: str | Path, conn: sqlite3.Connection, warning_limit: int = 8) -> dict[str, Any]:
    """Summarize an export and how much of it already exists locally."""
    known_agents = {row["key"] for row in conn.execute("SELECT key FROM agents")}
    totals = {
        "events": 0, "sessions": 0, "new_events": 0, "duplicate_events": 0,
        "new_sessions": 0, "merged_sessions": 0, "skipped": 0, "unknown_agents": 0,
    }
    by_type = {item: 0 for item in EVENT_TYPES}
    by_agent: dict[str, dict[str, Any]] = {}
    referenced: set[tuple[str, str]] = set()
    warnings: list[str] = []
    first_at: str | None = None
    last_at: str | None = None
    pending: list[tuple[str, str]] = []

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        fingerprints = [fingerprint for _, fingerprint in pending]
        existing = existing_fingerprints(conn, fingerprints)
        new = dup = 0
        for agent, fingerprint in pending:
            if fingerprint in existing:
                dup += 1
                by_agent[agent]["duplicate_events"] += 1
            else:
                new += 1
                by_agent[agent]["new_events"] += 1
        totals["new_events"] += new
        totals["duplicate_events"] += dup
        pending = []

    with open_bundle(path) as bundle:
        for raw in bundle.iter_events():
            event, error = normalize_event(raw)
            if event is None:
                totals["skipped"] += 1
                if len(warnings) < warning_limit:
                    warnings.append(error or "无法解析的事件")
                continue
            agent = event["agent_key"]
            if agent not in known_agents:
                totals["unknown_agents"] += 1
                if len(warnings) < warning_limit:
                    warnings.append(f"本机没有 Agent「{agent}」，导入时会跳过")
                continue
            totals["events"] += 1
            by_type[event["type"]] = by_type.get(event["type"], 0) + 1
            referenced.add((agent, event["session_external_id"]))
            entry = by_agent.setdefault(agent, {"agent_key": agent, "events": 0, "sessions": 0, "new_events": 0, "duplicate_events": 0})
            entry["events"] += 1
            stamp = event["timestamp"]
            if stamp:
                first_at = min(first_at, stamp) if first_at else stamp
                last_at = max(last_at, stamp) if last_at else stamp
            pending.append((agent, event["fingerprint"]))
            if len(pending) >= FINGERPRINT_CHUNK:
                flush()
        flush()
        manifest = bundle.manifest
        existing_sessions = session_count(conn, referenced)
        totals["sessions"] = len(referenced)
        totals["merged_sessions"] = existing_sessions
        totals["new_sessions"] = max(0, len(referenced) - existing_sessions)
        for agent_key, _external_id in referenced:
            by_agent[agent_key]["sessions"] += 1
        return {
            "kind": "archive",
            "path": str(bundle.path),
            "name": bundle.path.name,
            "size_bytes": bundle.path.stat().st_size,
            "format": bundle.kind,
            "exported_at": bundle.exported_at,
            "manifest": manifest,
            "filters": manifest.get("filters") if isinstance(manifest.get("filters"), dict) else {},
            "by_agent": sorted(by_agent.values(), key=lambda item: (-item["events"], item["agent_key"])),
            "by_type": by_type,
            "totals": totals,
            "first_at": first_at,
            "last_at": last_at,
            "warnings": warnings,
        }
