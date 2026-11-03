from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from .constants import DEFAULT_SETTINGS
from .paths import expand_user_path, normalize_roots


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def iso_from_any(value: Any) -> str | None:
    """Normalize seconds / milliseconds / ISO timestamps to UTC ISO-8601."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{10,13}", text):
        return iso_from_any(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except ValueError:
        return None


def local_day(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).date().isoformat()
    except ValueError:
        return None


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def stable_hash(*parts: Any, length: int = 40) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def json_loads(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def safe_json(value: Any, max_chars: int = 2_000_000) -> str | None:
    if value is None:
        return None
    try:
        text = json_dumps(value)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…[truncated]"
    return text


def expand_path(path: str) -> str:
    """Expand "~" / environment variables into a resolved absolute path."""
    if not path:
        return path
    text = expand_user_path(path)
    try:
        return str(Path(text).expanduser().resolve())
    except OSError:
        return text


def shorten(text: str | None, limit: int = 120) -> str:
    if not text:
        return ""
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit] + ("…" if len(compact) > limit else "")


def clean_user_text(text: str | None) -> str:
    """Remove agent-injected context wrappers from user turns."""
    if not text:
        return ""
    result = text
    # WorkBuddy wraps the actual ask in <user_query>.
    match = re.search(r"<user_query>\s*(.*?)\s*</user_query>", result, re.S | re.I)
    if match:
        result = match.group(1)
    # Strip complete system-reminder / context blocks.
    result = re.sub(r"<system-reminder\b[^>]*>.*?</system-reminder>", "", result, flags=re.S | re.I)
    result = re.sub(r"<(?:app-context|permissions instructions|environment_context)>.*?</(?:app-context|permissions instructions|environment_context)>", "", result, flags=re.S | re.I)
    result = re.sub(r"^\s*# AGENTS\.md instructions.*?</INSTRUCTIONS>\s*", "", result, flags=re.S | re.I)
    return result.strip()


def normalize_content_blocks(blocks: Any) -> list[dict[str, Any]]:
    if blocks is None:
        return []
    if isinstance(blocks, str):
        return [{"type": "text", "text": blocks}]
    if isinstance(blocks, dict):
        return [blocks]
    if not isinstance(blocks, list):
        return [{"type": "text", "text": str(blocks)}]
    normalized: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, str):
            normalized.append({"type": "text", "text": block})
        elif isinstance(block, dict):
            normalized.append(block)
    return normalized


def text_from_blocks(blocks: Any, *, include_reasoning: bool = False) -> str:
    chunks: list[str] = []
    for block in normalize_content_blocks(blocks):
        kind = str(block.get("type") or "").lower()
        if kind in {"text", "input_text", "output_text", "markdown"}:
            text = block.get("text") or block.get("content") or ""
            if text:
                chunks.append(str(text))
        elif kind in {"reasoning", "thinking"} and include_reasoning:
            text = block.get("text") or block.get("thinking") or ""
            if text:
                chunks.append(str(text))
    return "\n\n".join(chunks).strip()


def content_media_summary(blocks: Any) -> list[dict[str, Any]]:
    media = []
    for block in normalize_content_blocks(blocks):
        kind = str(block.get("type") or "").lower()
        if kind in {"image", "input_image", "audio", "file", "document"}:
            media.append({
                "type": kind,
                "name": block.get("name") or block.get("filename") or block.get("path") or "",
                "source": block.get("source") or block.get("url") or "",
            })
    return media


def duration_label(start: str | None, end: str | None) -> str:
    if not start or not end:
        return "—"
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
        seconds = max(0, int((b - a).total_seconds()))
    except ValueError:
        return "—"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def parse_date_bound(value: str | None) -> str | None:
    if not value:
        return None
    normalized = iso_from_any(value)
    if normalized:
        return normalized
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except ValueError:
        return None


def default_roots_for(defn: dict[str, Any]) -> list[str]:
    """Default roots stay in portable ("~"/"%VAR%") form inside the database."""
    return normalize_roots(defn.get("default_roots", []))


def merge_settings(overrides: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(DEFAULT_SETTINGS)
    if overrides:
        result.update(overrides)
    return result


def humanize_number(value: int | float | None) -> str:
    number = float(value or 0)
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}k"
    return str(int(number))


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def week_bounds(days: int) -> tuple[str, str] | None:
    if days <= 0:
        return None
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start.isoformat(timespec="seconds").replace("+00:00", "Z"), end.isoformat(timespec="seconds").replace("+00:00", "Z")
