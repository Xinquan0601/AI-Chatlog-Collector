from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .utils import (
    clean_user_text,
    content_media_summary,
    iso_from_any,
    normalize_content_blocks,
    safe_json,
    sha256_text,
    shorten,
    text_from_blocks,
    utc_now,
)


UUID_RE = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")
INJECTED_PREFIXES = (
    "<permissions instructions>",
    "<environment_context>",
    "<app-context>",
    "# AGENTS.md instructions",
    "<skill>",
    "<system-reminder",
)


def _clean_text(text: str | None, *, user: bool = False) -> str:
    if not text:
        return ""
    value = text.strip()
    if user:
        value = clean_user_text(value)
    return value


def _is_injected_user_text(text: str) -> bool:
    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in INJECTED_PREFIXES)


def _derive_project(cwd: str | None, source_path: str | None = None) -> str | None:
    if cwd:
        try:
            return Path(cwd).name or cwd
        except Exception:
            return cwd
    if source_path:
        return Path(source_path).parent.name
    return None


def _tool_input(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


class ParsedBatch:
    """Collects normalized sessions and the four canonical event types."""

    def __init__(
        self,
        agent_key: str,
        source_path: str | None = None,
        fallback_time: str | None = None,
        default_session_id: str | None = None,
    ) -> None:
        self.agent_key = agent_key
        self.source_path = source_path
        self.fallback_time = iso_from_any(fallback_time) or utc_now()
        self.default_session_id = default_session_id or (
            Path(source_path).stem if source_path else "unknown-session"
        )
        self.sessions: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self._sequence = 0
        self._seen_event_keys: set[str] = set()

    def session(self, external_id: str | None = None, **meta: Any) -> dict[str, Any]:
        sid = str(external_id or self.default_session_id)
        record = self.sessions.get(sid)
        if record is None:
            record = {
                "external_id": sid,
                "title": "",
                "project": _derive_project(meta.get("cwd"), self.source_path),
                "model": None,
                "cwd": None,
                "started_at": None,
                "ended_at": None,
                "tokens_input": 0,
                "tokens_output": 0,
                "metadata": {},
                "source_path": self.source_path,
            }
            self.sessions[sid] = record
        for key in ("title", "project", "model", "cwd"):
            value = meta.get(key)
            if value and not record.get(key):
                record[key] = value
        if meta.get("cwd") and not record.get("cwd"):
            record["cwd"] = meta["cwd"]
        if meta.get("project") and not record.get("project"):
            record["project"] = meta["project"]
        for key in ("tokens_input", "tokens_output"):
            value = meta.get(key)
            if isinstance(value, (int, float)):
                record[key] = max(int(record.get(key) or 0), int(value))
        for key in ("started_at", "ended_at"):
            value = iso_from_any(meta.get(key))
            if value:
                record[key] = min(record[key], value) if key == "started_at" and record.get(key) else (
                    max(record[key], value) if key == "ended_at" and record.get(key) else value
                )
        metadata = meta.get("metadata")
        if isinstance(metadata, dict):
            record["metadata"].update(metadata)
        return record

    def event(
        self,
        session_id: str | None,
        event_type: str,
        *,
        source_event_id: str | None = None,
        timestamp: Any = None,
        content: str | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        tool_status: str | None = None,
        tool_input: Any = None,
        model: str | None = None,
        tokens_input: int | None = None,
        tokens_output: int | None = None,
        metadata: dict[str, Any] | None = None,
        raw: Any = None,
        sequence: int | None = None,
        dedupe_key: str | None = None,
    ) -> dict[str, Any] | None:
        sid = str(session_id or self.default_session_id)
        content = (content or "").strip()
        tool_input = _tool_input(tool_input)
        # Empty text messages carry no analytical value.
        if event_type in {"user_message", "assistant_message"} and not content:
            return None
        if not source_event_id:
            source_event_id = f"{event_type}:{sequence if sequence is not None else self._sequence}"
        key = dedupe_key or f"{sid}|{event_type}|{source_event_id}|{tool_call_id or ''}"
        if key in self._seen_event_keys:
            return None
        self._seen_event_keys.add(key)
        self._sequence += 1
        ts = iso_from_any(timestamp) or self.fallback_time
        record = self.session(sid)
        if ts:
            if not record.get("started_at") or ts < record["started_at"]:
                record["started_at"] = ts
            if not record.get("ended_at") or ts > record["ended_at"]:
                record["ended_at"] = ts
        if event_type == "user_message" and not record.get("title"):
            record["title"] = shorten(content, 72)
        if model and not record.get("model"):
            record["model"] = model
        event = {
            "session_external_id": sid,
            "type": event_type,
            "source_event_id": str(source_event_id),
            "sequence": sequence if sequence is not None else self._sequence,
            "timestamp": ts,
            "content": content,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "tool_status": tool_status,
            "tool_input_json": safe_json(tool_input, 1_000_000) if tool_input is not None else None,
            "model": model,
            "tokens_input": int(tokens_input) if tokens_input is not None else None,
            "tokens_output": int(tokens_output) if tokens_output is not None else None,
            "metadata_json": safe_json(metadata) if metadata else None,
            "raw_json": safe_json(raw, 500_000) if raw is not None else None,
        }
        self.events.append(event)
        return event

    def finalize(self) -> dict[str, Any]:
        for record in self.sessions.values():
            if not record.get("title"):
                record["title"] = shorten(
                    f"{self.agent_key} · {record['external_id']}", 72
                ) or "未命名会话"
            if not record.get("project"):
                record["project"] = _derive_project(record.get("cwd"), self.source_path)
            if not record.get("started_at"):
                record["started_at"] = self.fallback_time
            if not record.get("ended_at"):
                record["ended_at"] = record.get("started_at")
        return {
            "agent_key": self.agent_key,
            "source_path": self.source_path,
            "sessions": list(self.sessions.values()),
            "events": self.events,
            "errors": self.errors,
        }


def _blocks_from_message(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content")
    return message


def _parse_usage(usage: Any) -> tuple[int | None, int | None]:
    if not isinstance(usage, dict):
        return None, None
    input_tokens = usage.get("input_tokens", usage.get("inputTokens", usage.get("prompt_tokens")))
    output_tokens = usage.get("output_tokens", usage.get("outputTokens", usage.get("completion_tokens")))
    return (
        int(input_tokens) if isinstance(input_tokens, (int, float)) else None,
        int(output_tokens) if isinstance(output_tokens, (int, float)) else None,
    )


# ---------------------------------------------------------------------------
# Codex rollout JSONL
# ---------------------------------------------------------------------------

def parse_codex_jsonl(
    lines: Iterable[str],
    source_path: str | None = None,
    fallback_time: str | None = None,
    default_session_id: str | None = None,
    start_sequence: int = 0,
) -> dict[str, Any]:
    if not default_session_id and source_path:
        match = UUID_RE.search(Path(source_path).stem)
        default_session_id = match.group(1) if match else Path(source_path).stem
    batch = ParsedBatch("codex", source_path, fallback_time, default_session_id)
    batch._sequence = start_sequence
    current_session = default_session_id or (Path(source_path).stem if source_path else "codex-session")
    model: str | None = None
    title_set = False
    seen_message_hashes: set[str] = set()

    for line_no, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            batch.errors.append(f"line {line_no + 1}: invalid JSON")
            continue
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        timestamp = item.get("timestamp") or payload.get("timestamp")

        if item_type == "session_meta":
            sid = payload.get("session_id") or payload.get("id")
            if sid:
                current_session = str(sid)
            batch.session(
                current_session,
                cwd=payload.get("cwd"),
                project=_derive_project(payload.get("cwd")),
                model=payload.get("model"),
                started_at=timestamp,
                metadata={"originator": payload.get("originator"), "source": payload.get("source")},
            )
            continue

        if item_type == "turn_context":
            model = payload.get("model") or model
            continue

        if item_type == "event_msg" and payload.get("type") == "thread_settings_applied":
            settings = payload.get("thread_settings") or {}
            model = settings.get("model") or model
            continue

        if item_type == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info") or {}
            total = info.get("total_token_usage") or {}
            batch.session(
                current_session,
                model=model,
                tokens_input=total.get("input_tokens"),
                tokens_output=total.get("output_tokens"),
            )
            continue

        if item_type == "event_msg" and payload.get("type") == "item_completed":
            completed = payload.get("item") if isinstance(payload.get("item"), dict) else {}
            completed_type = str(completed.get("type") or "")
            if completed_type in {"UserMessage", "AgentMessage"}:
                role = "user" if completed_type == "UserMessage" else "assistant"
                text = _clean_text(text_from_blocks(completed.get("content")), user=(role == "user"))
                key = sha256_text(text)
                if text and not (role == "user" and _is_injected_user_text(text)) and key not in seen_message_hashes:
                    seen_message_hashes.add(key)
                    batch.event(
                        current_session,
                        "user_message" if role == "user" else "assistant_message",
                        source_event_id=completed.get("id") or f"item-{line_no}",
                        timestamp=timestamp,
                        content=text,
                        model=model if role == "assistant" else None,
                        metadata={"source": "event_msg.item_completed"},
                        raw=item,
                        sequence=line_no,
                    )
            continue

        if item_type != "response_item":
            continue

        ptype = payload.get("type")
        if ptype == "message":
            role = str(payload.get("role") or "").lower()
            if role not in {"user", "assistant"}:
                continue
            text = _clean_text(text_from_blocks(payload.get("content")), user=(role == "user"))
            if not text or (role == "user" and _is_injected_user_text(text)):
                continue
            seen_message_hashes.add(sha256_text(text))
            event_type = "user_message" if role == "user" else "assistant_message"
            batch.event(
                current_session,
                event_type,
                source_event_id=payload.get("id") or f"line-{line_no}",
                timestamp=timestamp,
                content=text,
                model=model if role == "assistant" else None,
                metadata={"media": content_media_summary(payload.get("content"))},
                raw=item,
                sequence=line_no,
            )
            if role == "user" and not title_set:
                title_set = True
            continue

        if ptype in {"function_call", "custom_tool_call"}:
            name = payload.get("name") or "unknown"
            call_id = payload.get("call_id") or payload.get("id")
            arguments = payload.get("arguments", payload.get("input"))
            batch.event(
                current_session,
                "tool_call",
                source_event_id=f"{call_id or payload.get('id') or line_no}:call",
                timestamp=timestamp,
                content="",
                tool_name=name,
                tool_call_id=call_id,
                tool_status=payload.get("status") or "requested",
                tool_input=arguments,
                model=model,
                metadata={"custom": ptype == "custom_tool_call"},
                raw=item,
                sequence=line_no,
            )
            continue

        if ptype in {"function_call_output", "custom_tool_call_output"}:
            call_id = payload.get("call_id")
            output = payload.get("output")
            if isinstance(output, dict):
                output_text = output.get("content") or output.get("text") or safe_json(output)
            else:
                output_text = str(output or "")
            batch.event(
                current_session,
                "tool_result",
                source_event_id=f"{payload.get('id') or call_id or line_no}:result",
                timestamp=timestamp,
                content=output_text,
                tool_name=None,
                tool_call_id=call_id,
                tool_status="success",
                metadata={"media": content_media_summary(output)},
                raw=item,
                sequence=line_no,
            )
            continue

    result = batch.finalize()
    # The first real user turn is a more useful title than the session UUID.
    for session in result["sessions"]:
        if session.get("title", "").startswith("codex ·"):
            session["title"] = _derive_title_from_events(result["events"], session["external_id"], "Codex", session.get("project"))
    return result


def _derive_title_from_events(events: list[dict[str, Any]], session_id: str, agent_name: str, project: str | None = None) -> str:
    for event in events:
        if event.get("session_external_id") == session_id and event.get("type") == "user_message":
            return shorten(event.get("content"), 72)
    if project:
        return f"{project} · {agent_name}"
    return f"{agent_name} 会话"


# ---------------------------------------------------------------------------
# Claude Code / WorkBuddy JSONL (shared shape)
# ---------------------------------------------------------------------------

def parse_claude_jsonl(
    lines: Iterable[str],
    source_path: str | None = None,
    fallback_time: str | None = None,
    default_session_id: str | None = None,
    agent_key: str = "claudecode",
    start_sequence: int = 0,
) -> dict[str, Any]:
    batch = ParsedBatch(agent_key, source_path, fallback_time, default_session_id)
    batch._sequence = start_sequence
    current_session = default_session_id or (Path(source_path).stem if source_path else f"{agent_key}-session")
    current_cwd: str | None = None
    current_model: str | None = None

    for line_no, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            batch.errors.append(f"line {line_no + 1}: invalid JSON")
            continue
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type in {"session-meta", "session_meta"}:
            sid = item.get("sessionId") or item.get("session_id") or (item.get("meta") or {}).get("sessionId")
            if sid:
                current_session = str(sid)
            current_cwd = current_cwd or item.get("cwd") or (item.get("meta") or {}).get("cwd")
            continue

        # WorkBuddy / CodeBuddy writes tool calls as top-level events rather
        # than content blocks. Claude-style blocks are handled further below.
        if item_type in {"function_call", "function_call_result"}:
            sid = item.get("sessionId") or item.get("session_id") or current_session
            current_session = str(sid)
            current_cwd = item.get("cwd") or current_cwd
            provider = item.get("providerData") if isinstance(item.get("providerData"), dict) else {}
            current_model = provider.get("model") or item.get("model") or current_model
            ts = item.get("timestamp")
            if item_type == "function_call":
                call_id = item.get("callId") or item.get("call_id") or item.get("id")
                batch.event(
                    current_session, "tool_call",
                    source_event_id=f"{item.get('id') or call_id}:call",
                    timestamp=ts,
                    content="",
                    tool_name=item.get("name") or "unknown",
                    tool_call_id=call_id,
                    tool_status="requested",
                    tool_input=item.get("arguments"),
                    model=current_model,
                    metadata={"display": provider.get("argumentsDisplayText")},
                    raw=item,
                    sequence=line_no * 1000,
                )
            else:
                call_id = item.get("callId") or item.get("call_id")
                output = item.get("output")
                if isinstance(output, dict):
                    output_text = output.get("text") or output.get("content") or safe_json(output) or ""
                else:
                    output_text = str(output or "")
                status = str(item.get("status") or "success")
                batch.event(
                    current_session, "tool_result",
                    source_event_id=f"{item.get('id') or call_id}:result",
                    timestamp=ts,
                    content=output_text,
                    tool_name=item.get("name"),
                    tool_call_id=call_id,
                    tool_status="error" if status.lower() in {"error", "failed", "cancelled"} else status,
                    metadata={"provider": provider.get("toolResult") is not None},
                    raw=item,
                    sequence=line_no * 1000,
                )
            batch.session(current_session, cwd=current_cwd, model=current_model)
            continue

        if item_type in {"file-history-snapshot", "queue-operation", "summary", "system", "debug", "reasoning", "ai-title", "resend-fork-notice"}:
            continue

        message = item.get("message") if isinstance(item.get("message"), dict) else None
        role = str(item.get("role") or (message or {}).get("role") or "").lower()
        if item_type in {"user", "assistant"} and not role:
            role = item_type
        if item_type in {"message", "user", "assistant"} and role not in {"user", "assistant"}:
            continue
        if item_type not in {"message", "user", "assistant"}:
            # Some exporters wrap the payload under a data/message key.
            if item.get("type") in {"human", "ai"}:
                role = "user" if item.get("type") == "human" else "assistant"
                message = item
            else:
                continue

        sid = item.get("sessionId") or item.get("session_id") or (message or {}).get("sessionId") or current_session
        current_session = str(sid)
        current_cwd = item.get("cwd") or current_cwd
        timestamp = item.get("timestamp") or (message or {}).get("timestamp") or (message or {}).get("created_at")
        model = item.get("model") or (message or {}).get("model") or current_model
        if model:
            current_model = model
        base_event_id = str(item.get("uuid") or item.get("id") or (message or {}).get("id") or f"line-{line_no}")
        content_blocks = _blocks_from_message(message) if message is not None else item.get("content")
        blocks = normalize_content_blocks(content_blocks)
        usage_in, usage_out = _parse_usage((message or {}).get("usage") or item.get("usage"))

        text = _clean_text(text_from_blocks(blocks), user=(role == "user"))
        tool_use_blocks = [b for b in blocks if str(b.get("type") or "").lower() in {"tool_use", "tool-call", "tool_call", "function_call", "function"}]
        tool_result_blocks = [b for b in blocks if str(b.get("type") or "").lower() in {"tool_result", "tool-result", "tool_output", "function_call_output"}]

        # Tool calls are independent normalized events.
        for block_index, block in enumerate(tool_use_blocks):
            call_id = block.get("id") or block.get("tool_use_id") or block.get("call_id") or f"{base_event_id}:tool:{block_index}"
            name = block.get("name") or block.get("tool_name") or "unknown"
            arguments = block.get("input", block.get("arguments", block.get("parameters")))
            batch.event(
                current_session,
                "tool_call",
                source_event_id=f"{base_event_id}:call:{block_index}",
                timestamp=timestamp,
                content="",
                tool_name=name,
                tool_call_id=call_id,
                tool_status="requested",
                tool_input=arguments,
                model=current_model if role == "assistant" else None,
                metadata={"media": content_media_summary(blocks)},
                raw=item,
                sequence=line_no * 1000 + block_index,
            )

        for block_index, block in enumerate(tool_result_blocks):
            call_id = block.get("tool_use_id") or block.get("tool_call_id") or block.get("call_id")
            result_content = block.get("content")
            result_text = text_from_blocks(result_content) if not isinstance(result_content, str) else result_content
            if not result_text and item.get("toolUseResult") is not None:
                result_text = safe_json(item.get("toolUseResult")) or ""
            is_error = bool(block.get("is_error") or block.get("isError"))
            batch.event(
                current_session,
                "tool_result",
                source_event_id=f"{base_event_id}:result:{block_index}",
                timestamp=timestamp,
                content=result_text or "",
                tool_call_id=call_id,
                tool_status="error" if is_error else "success",
                metadata={"is_error": is_error},
                raw=item,
                sequence=line_no * 1000 + block_index,
            )

        if text and not (role == "user" and _is_injected_user_text(text)):
            batch.event(
                current_session,
                "user_message" if role == "user" else "assistant_message",
                source_event_id=base_event_id,
                timestamp=timestamp,
                content=text,
                model=current_model if role == "assistant" else None,
                tokens_input=usage_in if role == "assistant" else None,
                tokens_output=usage_out if role == "assistant" else None,
                metadata={
                    "cwd": current_cwd,
                    "parent_uuid": item.get("parentUuid"),
                    "media": content_media_summary(blocks),
                    "sidechain": bool(item.get("isSidechain")),
                },
                raw=item,
                sequence=line_no * 1000 + 900,
            )
        elif role == "user" and tool_result_blocks:
            batch.session(current_session, cwd=current_cwd, model=current_model)

    result = batch.finalize()
    for session in result["sessions"]:
        if not session.get("title") or session["title"].endswith(session["external_id"]):
            session["title"] = _derive_title_from_events(
                result["events"], session["external_id"],
                "Claude Code" if agent_key == "claudecode" else "WorkBuddy",
                session.get("project"),
            )
    return result


# ---------------------------------------------------------------------------
# DeepSeek Harness v4 event stream
# ---------------------------------------------------------------------------

def parse_dsh_jsonl(
    lines: Iterable[str],
    source_path: str | None = None,
    fallback_time: str | None = None,
    default_session_id: str | None = None,
    start_sequence: int = 0,
) -> dict[str, Any]:
    batch = ParsedBatch("deepseekharness", source_path, fallback_time, default_session_id)
    batch._sequence = start_sequence
    current_session = default_session_id or (Path(source_path).parent.name if source_path else "dsh-session")
    cwd: str | None = None
    model: str | None = None

    for line_no, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            batch.errors.append(f"line {line_no + 1}: invalid JSON")
            continue
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        timestamp = item.get("time") or item.get("timestamp")

        if item_type == "session":
            sid = item.get("id") or current_session
            current_session = str(sid)
            cwd = item.get("cwd") or cwd
            batch.session(
                current_session,
                cwd=cwd,
                project=_derive_project(cwd),
                started_at=item.get("createdAt"),
                metadata={"version": item.get("version"), "agent_preset": item.get("agentPreset")},
            )
            continue

        if item_type == "user/message":
            source = data.get("source") if isinstance(data.get("source"), dict) else {}
            if source.get("kind") in {"runtime-context", "skill"}:
                continue
            message = data.get("message") if isinstance(data.get("message"), dict) else data
            role = str(data.get("role") or message.get("role") or "user")
            if role != "user":
                continue
            text = _clean_text(text_from_blocks(message.get("content")), user=True)
            if not text or _is_injected_user_text(text):
                continue
            event_id = data.get("id") or message.get("id") or item.get("id") or f"line-{line_no}"
            batch.event(
                current_session,
                "user_message",
                source_event_id=event_id,
                timestamp=timestamp,
                content=text,
                metadata={
                    "source_kind": source.get("kind"),
                    "client_timezone": source.get("clientTimeZone"),
                    "media": content_media_summary(message.get("content")),
                },
                raw=item,
                sequence=item.get("seq", line_no),
            )
            continue

        if item_type == "assistant/message":
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            message_id = message.get("id") or data.get("id") or item.get("id") or f"line-{line_no}"
            source_meta = message.get("source") if isinstance(message.get("source"), dict) else {}
            model = source_meta.get("model") or data.get("model") or model
            usage_in, usage_out = _parse_usage(data.get("usage") or message.get("usage"))
            blocks = normalize_content_blocks(message.get("content"))
            text = text_from_blocks(blocks)
            if text:
                batch.event(
                    current_session,
                    "assistant_message",
                    source_event_id=f"{message_id}:text",
                    timestamp=timestamp,
                    content=text,
                    model=model,
                    tokens_input=usage_in,
                    tokens_output=usage_out,
                    metadata={
                        "turn": data.get("turn"),
                        "step": data.get("step"),
                        "provider": source_meta.get("provider"),
                        "stop_reason": source_meta.get("stopReason"),
                    },
                    raw=item,
                    sequence=item.get("seq", line_no) * 1000,
                )
            for block_index, block in enumerate(blocks):
                kind = str(block.get("type") or "").lower()
                if kind not in {"tool-call", "tool_call", "tool_use", "function_call"}:
                    continue
                call_id = block.get("id") or block.get("call_id") or f"{message_id}:tool:{block_index}"
                batch.event(
                    current_session,
                    "tool_call",
                    source_event_id=f"{message_id}:call:{block_index}",
                    timestamp=timestamp,
                    content="",
                    tool_name=block.get("name") or "unknown",
                    tool_call_id=call_id,
                    tool_status="requested",
                    tool_input=block.get("arguments", block.get("input")),
                    model=model,
                    raw={"block": block},
                    sequence=item.get("seq", line_no) * 1000 + block_index + 1,
                )
            continue

        if item_type == "tool/result":
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            call_id = data.get("toolCallId") or message.get("toolCallId") or message.get("callId")
            text = text_from_blocks(message.get("content"))
            is_error = bool(data.get("isError") or message.get("isError"))
            batch.event(
                current_session,
                "tool_result",
                source_event_id=f"{message.get('id') or item.get('id') or line_no}:result",
                timestamp=timestamp,
                content=text or "",
                tool_call_id=call_id,
                tool_status="error" if is_error else "success",
                metadata={"is_error": is_error},
                raw=item,
                sequence=item.get("seq", line_no),
            )
            continue

        # Ignore duplicated tool/call and PTC dispatch events. They repeat
        # information already present in assistant/message blocks.
        if item_type in {"session/meta", "session_meta"}:
            cwd = data.get("cwd") or cwd
            model = data.get("model") or model

    result = batch.finalize()
    for session in result["sessions"]:
        if not session.get("title") or session["title"].startswith("deepseekharness ·"):
            session["title"] = _derive_title_from_events(
                result["events"], session["external_id"], "DeepSeek Harness", session.get("project")
            )
    return result


# ---------------------------------------------------------------------------
# Generic OpenAI / ZCode message parser
# ---------------------------------------------------------------------------

def _parse_openai_message(
    batch: ParsedBatch,
    session_id: str,
    message: dict[str, Any],
    line_no: int,
    timestamp: Any = None,
    model: str | None = None,
) -> None:
    role = str(message.get("role") or message.get("type") or "").lower()
    if role in {"human", "prompt"}:
        role = "user"
    if role in {"ai", "bot", "model"}:
        role = "assistant"
    content = message.get("content", message.get("text"))
    blocks = normalize_content_blocks(content)
    text = _clean_text(text_from_blocks(blocks), user=(role == "user"))
    event_id = str(message.get("id") or message.get("uuid") or f"line-{line_no}")
    tool_calls = message.get("tool_calls") or []
    if isinstance(tool_calls, list):
        for index, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                continue
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            call_id = call.get("id") or f"{event_id}:call:{index}"
            batch.event(
                session_id, "tool_call",
                source_event_id=f"{event_id}:call:{index}",
                timestamp=timestamp,
                content="",
                tool_name=function.get("name") or call.get("name") or "unknown",
                tool_call_id=call_id,
                tool_status="requested",
                tool_input=function.get("arguments", call.get("arguments", call.get("input"))),
                model=model,
                raw=call,
                sequence=line_no * 1000 + index,
            )
    for index, block in enumerate(blocks):
        kind = str(block.get("type") or "").lower()
        if kind in {"tool_use", "tool-call", "tool_call", "function_call"}:
            call_id = block.get("id") or block.get("tool_call_id") or f"{event_id}:block:{index}"
            batch.event(
                session_id, "tool_call",
                source_event_id=f"{event_id}:block-call:{index}",
                timestamp=timestamp,
                content="",
                tool_name=block.get("name") or "unknown",
                tool_call_id=call_id,
                tool_status="requested",
                tool_input=block.get("input", block.get("arguments")),
                model=model,
                raw=block,
                sequence=line_no * 1000 + index,
            )
        elif kind in {"tool_result", "tool-output", "function_call_output"}:
            call_id = block.get("tool_use_id") or block.get("tool_call_id") or block.get("call_id")
            result_content = block.get("content") or block.get("output")
            result_text = result_content if isinstance(result_content, str) else text_from_blocks(result_content)
            batch.event(
                session_id, "tool_result",
                source_event_id=f"{event_id}:block-result:{index}",
                timestamp=timestamp,
                content=result_text or "",
                tool_call_id=call_id,
                tool_status="error" if block.get("is_error") else "success",
                raw=block,
                sequence=line_no * 1000 + index,
            )
    if role == "tool":
        call_id = message.get("tool_call_id") or message.get("tool_use_id")
        batch.event(
            session_id, "tool_result",
            source_event_id=f"{event_id}:tool",
            timestamp=timestamp,
            content=text,
            tool_call_id=call_id,
            tool_status="error" if message.get("is_error") else "success",
            raw=message,
            sequence=line_no,
        )
    elif role in {"user", "assistant"} and text and not (role == "user" and _is_injected_user_text(text)):
        batch.event(
            session_id, "user_message" if role == "user" else "assistant_message",
            source_event_id=event_id,
            timestamp=timestamp,
            content=text,
            model=model if role == "assistant" else None,
            tokens_input=(message.get("usage") or {}).get("prompt_tokens") if isinstance(message.get("usage"), dict) else None,
            tokens_output=(message.get("usage") or {}).get("completion_tokens") if isinstance(message.get("usage"), dict) else None,
            metadata={"media": content_media_summary(blocks)},
            raw=message,
            sequence=line_no,
        )


def parse_generic_document(
    text: str,
    source_path: str | None = None,
    fallback_time: str | None = None,
    default_session_id: str | None = None,
    agent_key: str = "zcode",
    start_sequence: int = 0,
) -> dict[str, Any]:
    batch = ParsedBatch(agent_key, source_path, fallback_time, default_session_id)
    batch._sequence = start_sequence
    stripped = text.lstrip()
    current_session = default_session_id or (Path(source_path).stem if source_path else f"{agent_key}-session")

    if not stripped.startswith(("[", "{")):
        # Treat it as a JSONL stream.
        return parse_generic_jsonl(
            text.splitlines(), source_path, fallback_time, default_session_id,
            agent_key=agent_key, start_sequence=start_sequence,
        )
    try:
        document = json.loads(stripped)
    except (TypeError, ValueError):
        # A .json file may still contain one JSON object per line.
        return parse_generic_jsonl(
            text.splitlines(), source_path, fallback_time, default_session_id,
            agent_key=agent_key, start_sequence=start_sequence,
        )

    visited: set[int] = set()

    def visit(node: Any, depth: int = 0, session_hint: str | None = None) -> None:
        nonlocal current_session
        if depth > 8 or node is None:
            return
        if isinstance(node, list):
            for child in node:
                visit(child, depth + 1, session_hint)
            return
        if not isinstance(node, dict):
            return
        if id(node) in visited:
            return
        visited.add(id(node))
        sid = str(node.get("sessionId") or node.get("session_id") or node.get("conversationId") or session_hint or current_session)
        current_session = sid
        cwd = node.get("cwd") or node.get("workspace") or node.get("directory")
        model = node.get("model") if isinstance(node.get("model"), str) else None
        if cwd:
            batch.session(sid, cwd=cwd, project=_derive_project(cwd), model=model)
        messages = node.get("messages") or node.get("turns") or node.get("conversation")
        if isinstance(messages, list):
            batch.session(
                sid, cwd=cwd, project=_derive_project(cwd), model=model,
                started_at=node.get("createdAt") or node.get("created_at"),
                metadata={"title": node.get("title") or node.get("name")},
            )
            if node.get("title") or node.get("name"):
                batch.sessions[sid]["title"] = shorten(str(node.get("title") or node.get("name")), 72)
            for index, message in enumerate(messages):
                if isinstance(message, dict):
                    _parse_openai_message(
                        batch, sid, message, index,
                        timestamp=message.get("timestamp") or message.get("created_at") or message.get("time"),
                        model=model,
                    )
            return
        if "role" in node and ("content" in node or "text" in node):
            _parse_openai_message(
                batch, sid, node, depth,
                timestamp=node.get("timestamp") or node.get("created_at") or node.get("time"),
                model=model,
            )
            return
        for key in ("data", "session", "conversation", "thread", "result", "events", "items", "children"):
            if key in node:
                visit(node[key], depth + 1, sid)

    visit(document)
    result = batch.finalize()
    for session in result["sessions"]:
        if not session.get("title") or session["title"].startswith(f"{agent_key} ·"):
            session["title"] = _derive_title_from_events(
                result["events"], session["external_id"], agent_key.upper(), session.get("project")
            )
    return result


def parse_generic_jsonl(
    lines: Iterable[str],
    source_path: str | None = None,
    fallback_time: str | None = None,
    default_session_id: str | None = None,
    agent_key: str = "zcode",
    start_sequence: int = 0,
) -> dict[str, Any]:
    batch = ParsedBatch(agent_key, source_path, fallback_time, default_session_id)
    batch._sequence = start_sequence
    current_session = default_session_id or (Path(source_path).stem if source_path else f"{agent_key}-session")
    model: str | None = None
    cwd: str | None = None
    for line_no, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            batch.errors.append(f"line {line_no + 1}: invalid JSON")
            continue
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"session", "session-meta", "session_meta"}:
            current_session = str(item.get("sessionId") or item.get("id") or current_session)
            cwd = item.get("cwd") or cwd
            model = item.get("model") or model
            batch.session(current_session, cwd=cwd, project=_derive_project(cwd), model=model, started_at=item.get("createdAt") or item.get("timestamp"))
            continue
        sid = str(item.get("sessionId") or item.get("session_id") or current_session)
        message = item.get("message") if isinstance(item.get("message"), dict) else item
        if "role" not in message and "content" not in message:
            # DSH-style or event-style generic payload.
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            if item.get("type") == "user/message" and data:
                message = data.get("message") if isinstance(data.get("message"), dict) else data
                item["role"] = "user"
            elif item.get("type") == "assistant/message" and data:
                message = data.get("message") if isinstance(data.get("message"), dict) else data
                item["role"] = "assistant"
            else:
                continue
        _parse_openai_message(
            batch, sid, message, line_no,
            timestamp=item.get("timestamp") or item.get("time") or message.get("timestamp"),
            model=item.get("model") or model,
        )
    result = batch.finalize()
    for session in result["sessions"]:
        if not session.get("title") or session["title"].startswith(f"{agent_key} ·"):
            session["title"] = _derive_title_from_events(
                result["events"], session["external_id"], agent_key.upper(), session.get("project")
            )
    return result


# ---------------------------------------------------------------------------
# VS Code Copilot chat logs
# ---------------------------------------------------------------------------
# VS Code stores each chat session as an append-only JSONL log.  The first line
# is a full snapshot ({"kind": 0, "v": {...}}), later lines are small patches:
#   kind 1 -> set the value at path k
#   kind 2 -> push items v at path k (optional i truncates the array first)
#   kind 3 -> delete the value at path k
# Replaying the patches yields the current session object, whose "requests"
# array holds the user message, the assistant response parts and tool calls.

_COPILOT_MISSING = object()


def _copilot_walk(root: Any, path: list[Any]) -> Any:
    node = root
    for key in path:
        if isinstance(node, dict):
            if key not in node:
                return _COPILOT_MISSING
            node = node[key]
        elif isinstance(node, list) and isinstance(key, int) and 0 <= key < len(node):
            node = node[key]
        else:
            return _COPILOT_MISSING
    return node


def _copilot_set(root: Any, path: list[Any], value: Any) -> None:
    parent = _copilot_walk(root, path[:-1]) if len(path) > 1 else root
    if parent is _COPILOT_MISSING:
        return
    key = path[-1]
    if isinstance(parent, dict):
        parent[key] = value
    elif isinstance(parent, list) and isinstance(key, int) and 0 <= key < len(parent):
        parent[key] = value


def _copilot_push(root: Any, path: list[Any], items: Any, keep: Any) -> None:
    parent = _copilot_walk(root, path[:-1]) if len(path) > 1 else root
    if parent is _COPILOT_MISSING:
        return
    key = path[-1]
    target = None
    if isinstance(parent, dict):
        target = parent.get(key)
        if not isinstance(target, list):
            target = []
            parent[key] = target
    elif isinstance(parent, list) and isinstance(key, int) and 0 <= key < len(parent):
        target = parent[key]
        if not isinstance(target, list):
            target = []
            parent[key] = target
    if target is None:
        return
    if isinstance(keep, int) and keep >= 0:
        del target[keep:]
    if isinstance(items, list):
        target.extend(items)


def _copilot_delete(root: Any, path: list[Any]) -> None:
    parent = _copilot_walk(root, path[:-1]) if len(path) > 1 else root
    if parent is _COPILOT_MISSING:
        return
    key = path[-1]
    if isinstance(parent, dict):
        parent.pop(key, None)
    elif isinstance(parent, list) and isinstance(key, int) and 0 <= key < len(parent):
        parent[key] = None


def _copilot_replay(lines: Iterable[str], errors: list[str]) -> dict[str, Any] | None:
    state: dict[str, Any] | None = None
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except (TypeError, ValueError):
            errors.append(f"line {line_no}: invalid JSON")
            continue
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        if kind == 0:
            snapshot = entry.get("v")
            state = snapshot if isinstance(snapshot, dict) else {}
            continue
        if state is None:
            errors.append(f"line {line_no}: log is missing its initial snapshot")
            continue
        path = entry.get("k")
        if not isinstance(path, list) or not path:
            continue
        if kind == 1:
            _copilot_set(state, path, entry.get("v"))
        elif kind == 2:
            _copilot_push(state, path, entry.get("v"), entry.get("i"))
        elif kind == 3:
            _copilot_delete(state, path)
    return state


def _copilot_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_copilot_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in ("value", "text", "content", "markdown", "message"):
            if key in value:
                text = _copilot_text(value[key])
                if text:
                    return text
    return ""


def _copilot_tool_input(part: dict[str, Any]) -> Any:
    payload: dict[str, Any] = {}
    message = _copilot_text(part.get("invocationMessage"))
    if message:
        payload["message"] = message
    specific = part.get("toolSpecificData")
    if isinstance(specific, dict) and specific:
        payload["data"] = specific
    return payload or None


def _copilot_result_text(part: dict[str, Any]) -> tuple[str, str]:
    error = _copilot_text(part.get("resultError"))
    if error:
        return error, "error"
    details = part.get("resultDetails")
    if isinstance(details, dict):
        output = details.get("output")
        if isinstance(output, dict):
            if output.get("type") == "data":
                mime = output.get("mimeType") or "binary"
                return f"[{mime} 数据已省略]", "success"
            text = _copilot_text(output)
            if text:
                return text, "success"
        text = _copilot_text(details.get("value") or details.get("text"))
        if text:
            return text, "success"
    text = _copilot_text(details)
    return (text, "success") if text else ("", "requested")


def parse_vscode_copilot_jsonl(
    lines: Iterable[str],
    path: str,
    fallback_time: str | None,
    default_session_id: str | None,
    *,
    start_sequence: int = 0,
) -> dict[str, Any]:
    """Parse a VS Code Copilot chat session log into normalized events."""
    source_path = str(path)
    batch = ParsedBatch(
        "vscodecopilot", source_path, fallback_time,
        default_session_id or Path(source_path).stem,
    )
    state = _copilot_replay(lines, batch.errors)
    if not isinstance(state, dict):
        if not batch.errors:
            batch.errors.append("会话日志为空")
        return batch.finalize()

    raw_requests = state.get("requests")
    requests = raw_requests if isinstance(raw_requests, list) else []
    if not requests:
        return batch.finalize()

    session_id = str(state.get("sessionId") or default_session_id or Path(source_path).stem)
    creation = state.get("creationDate")
    cwd = state.get("workingDirectory") or state.get("workspaceFolder") or state.get("cwd")
    title = _copilot_text(state.get("customTitle") or state.get("title"))
    total_input = 0
    total_output = 0
    for request in requests:
        if not isinstance(request, dict):
            continue
        prompt = request.get("promptTokens")
        completion = request.get("completionTokens")
        if isinstance(prompt, (int, float)):
            total_input += int(prompt)
        if isinstance(completion, (int, float)):
            total_output += int(completion)

    batch.session(
        session_id,
        title=title,
        cwd=cwd,
        project=_derive_project(cwd),
        started_at=creation,
        tokens_input=total_input,
        tokens_output=total_output,
    )

    for index, request in enumerate(requests):
        if not isinstance(request, dict):
            continue
        request_id = str(request.get("requestId") or f"request-{index}")
        base_sequence = start_sequence + (index + 1) * 1000
        timestamp = request.get("timestamp")
        model = request.get("modelId") or request.get("model")
        user_text = _copilot_text(request.get("message"))
        if user_text and not _is_injected_user_text(user_text):
            batch.event(
                session_id, "user_message",
                source_event_id=f"{request_id}:user",
                timestamp=timestamp,
                content=user_text,
                model=model,
                sequence=base_sequence,
                raw=request.get("message"),
            )
        response = request.get("response")
        if isinstance(response, str):
            response = [{"kind": "text", "value": response}]
        if not isinstance(response, list):
            continue
        response_time = request.get("responseTimestamp") or timestamp
        for part_index, part in enumerate(response):
            if not isinstance(part, dict):
                continue
            kind = str(part.get("kind") or "")
            sequence = base_sequence + part_index + 1
            if kind in {"text", "markdownContent"}:
                text = _copilot_text(part)
                if text:
                    batch.event(
                        session_id, "assistant_message",
                        source_event_id=f"{request_id}:text:{part_index}",
                        timestamp=part.get("timestamp") or response_time,
                        content=text,
                        model=model,
                        sequence=sequence,
                        raw=part,
                    )
            elif kind == "thinking":
                text = _copilot_text(part)
                if text:
                    batch.event(
                        session_id, "assistant_message",
                        source_event_id=f"{request_id}:thinking:{part_index}",
                        timestamp=part.get("timestamp") or response_time,
                        content=text,
                        model=model,
                        sequence=sequence,
                        metadata={"reasoning": True},
                        raw=part,
                    )
            elif kind == "toolInvocationSerialized":
                call_id = str(part.get("toolCallId") or f"{request_id}:tool:{part_index}")
                tool_name = str(part.get("toolId") or "unknown")
                batch.event(
                    session_id, "tool_call",
                    source_event_id=f"{request_id}:tool:{part_index}",
                    timestamp=part.get("startedAt") or response_time,
                    content="",
                    tool_name=tool_name,
                    tool_call_id=call_id,
                    tool_status="requested",
                    tool_input=_copilot_tool_input(part),
                    model=model,
                    sequence=sequence,
                    raw=part,
                )
                result_text, result_status = _copilot_result_text(part)
                if result_text:
                    batch.event(
                        session_id, "tool_result",
                        source_event_id=f"{request_id}:tool-result:{part_index}",
                        timestamp=part.get("completedAt") or response_time,
                        content=result_text,
                        tool_call_id=call_id,
                        tool_status=result_status,
                        sequence=sequence + 1,
                        raw=part,
                    )
    result = batch.finalize()
    for session in result["sessions"]:
        if not session.get("title") or session["title"].startswith("vscodecopilot ·"):
            session["title"] = _derive_title_from_events(
                result["events"], session["external_id"], "VS Code Copilot", session.get("project")
            )
    return result
