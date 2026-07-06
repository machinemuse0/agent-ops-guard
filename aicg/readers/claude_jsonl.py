from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import (
    NormalizedSession,
    NormalizedToolEvent,
    NormalizedTurn,
    ParsedRecords,
)
from ..util import (
    detect_policy_flags,
    detect_privacy_flags,
    duration_ms,
    flags_to_text,
    hash_file,
    normalize_timestamp,
    redact_secrets,
    sha256_text,
    stable_id,
    utc_now_iso,
)


TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


class ClaudeJsonlReader:
    provider = "claude"

    def read(self, path: Path) -> ParsedRecords:
        source = Path(path)
        source_hash = hash_file(source)
        created_at = utc_now_iso()
        session: NormalizedSession | None = None
        turns: list[NormalizedTurn] = []
        tool_events: list[NormalizedToolEvent] = []
        turn_by_message_id: dict[str, NormalizedTurn] = {}
        tool_by_raw_id: dict[str, NormalizedToolEvent] = {}
        raw_event_count = 0
        malformed_line_count = 0
        first_seen_at: str | None = None
        last_seen_at: str | None = None
        session_privacy_flags: set[str] = set()
        session_policy_flags: set[str] = set()

        def ensure_session(event: dict[str, Any] | None = None) -> NormalizedSession:
            nonlocal session
            if session is not None:
                if event:
                    session.project_path = _first_str(event, "cwd") or session.project_path
                return session
            event = event or {}
            session_id = stable_id(
                "session",
                self.provider,
                _first_str(event, "sessionId", "session_id") or source_hash,
            )
            session = NormalizedSession(
                id=session_id,
                provider=self.provider,
                project_path=_first_str(event, "cwd") or _project_from_path(source),
                source_file=str(source),
                source_file_hash=source_hash,
                started_at=_timestamp(event) or first_seen_at,
                status="running",
                model=_message_model(event),
                task_type=_task_type(event),
                background_flag=bool(event.get("isSidechain")),
                raw_event_count=0,
                malformed_line_count=0,
                created_at=created_at,
            )
            return session

        with source.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    malformed_line_count += 1
                    continue
                if not isinstance(event, dict):
                    malformed_line_count += 1
                    continue

                raw_event_count += 1
                event_time = _timestamp(event)
                if event_time and first_seen_at is None:
                    first_seen_at = event_time
                if event_time:
                    last_seen_at = event_time
                active_session = ensure_session(event)

                privacy_flags, policy_flags = _event_flags(event)
                session_privacy_flags.update(privacy_flags)
                session_policy_flags.update(policy_flags)

                event_type = _first_str(event, "type")
                message = event.get("message") if isinstance(event.get("message"), dict) else {}
                role = _first_str(message, "role")

                if event_type == "assistant" or role == "assistant":
                    raw_message_id = _first_str(message, "id") or _first_str(event, "uuid")
                    if raw_message_id and raw_message_id in turn_by_message_id:
                        turn = turn_by_message_id[raw_message_id]
                    else:
                        turn = NormalizedTurn(
                            id=stable_id(
                                "turn",
                                active_session.id,
                                raw_message_id or len(turns) + 1,
                            ),
                            session_id=active_session.id,
                            provider=self.provider,
                            started_at=event_time,
                            ended_at=event_time,
                            status="completed",
                            model=_message_model(event) or active_session.model,
                            task_type=_task_type(event) or active_session.task_type,
                            background_flag=active_session.background_flag,
                        )
                        turns.append(turn)
                        if raw_message_id:
                            turn_by_message_id[raw_message_id] = turn
                    _apply_usage(turn, message)
                    _merge_turn_flags(turn, privacy_flags, policy_flags)
                    active_session.model = turn.model or active_session.model
                    if _assistant_failed(message):
                        _mark_failed(turn, message, event_time)
                        active_session.status = "failed"
                    _collect_tool_use(
                        tool_events,
                        tool_by_raw_id,
                        active_session,
                        turn,
                        message,
                        event_time,
                        session_privacy_flags,
                        session_policy_flags,
                    )
                    continue

                if event_type == "user" or role == "user":
                    _collect_tool_results(
                        tool_events,
                        tool_by_raw_id,
                        active_session,
                        message,
                        event_time,
                        session_privacy_flags,
                        session_policy_flags,
                    )
                    continue

                if event_type in {"system", "queue-operation"} and _looks_failed(event):
                    active_session.status = "failed"

        active_session = ensure_session({})
        active_session.raw_event_count = raw_event_count
        active_session.malformed_line_count = malformed_line_count
        active_session.started_at = active_session.started_at or first_seen_at
        active_session.ended_at = active_session.ended_at or last_seen_at
        active_session.duration_ms = duration_ms(active_session.started_at, active_session.ended_at)
        active_session.retry_count = sum(1 for turn in turns if turn.status == "failed")
        active_session.privacy_flags = flags_to_text(session_privacy_flags)
        active_session.policy_flags = flags_to_text(session_policy_flags)
        if active_session.status == "running":
            active_session.status = "completed" if raw_event_count else "unknown"
        for turn in turns:
            turn.duration_ms = duration_ms(turn.started_at, turn.ended_at)
        return ParsedRecords(
            sessions=[active_session],
            turns=turns,
            tool_events=tool_events,
            malformed_line_count=malformed_line_count,
            source_file=str(source),
            source_file_hash=source_hash,
        )


def _timestamp(event: dict[str, Any]) -> str | None:
    return normalize_timestamp(_first_str(event, "timestamp", "created_at", "time"))


def _first_str(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _message_model(event: dict[str, Any]) -> str | None:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    return _first_str(message, "model") or _first_str(event, "model")


def _task_type(event: dict[str, Any]) -> str | None:
    return _first_str(event, "entrypoint", "permissionMode") or _first_str(event, "type")


def _project_from_path(source: Path) -> str | None:
    parent = source.parent.name
    if parent.startswith("-"):
        return "/" + parent[1:].replace("-", "/")
    return None


def _apply_usage(turn: NormalizedTurn, message: dict[str, Any]) -> None:
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return
    for field in TOKEN_FIELDS:
        parsed = _int_or_none(usage.get(field))
        if parsed is not None:
            setattr(turn, field, parsed)


def _collect_tool_use(
    tool_events: list[NormalizedToolEvent],
    tool_by_raw_id: dict[str, NormalizedToolEvent],
    session: NormalizedSession,
    turn: NormalizedTurn,
    message: dict[str, Any],
    event_time: str | None,
    session_privacy_flags: set[str],
    session_policy_flags: set[str],
) -> None:
    for item in _content_items(message):
        if item.get("type") != "tool_use":
            continue
        raw_tool_id = _first_str(item, "id")
        session_privacy_flags.update(detect_privacy_flags(item.get("input")))
        session_policy_flags.update(detect_policy_flags(item.get("input")))
        tool = NormalizedToolEvent(
            id=stable_id("tool", session.id, turn.id, raw_tool_id or len(tool_events) + 1),
            session_id=session.id,
            turn_id=turn.id,
            provider="claude",
            tool_type=_claude_tool_type(_first_str(item, "name")),
            tool_name=_first_str(item, "name"),
            status="running",
            started_at=event_time,
            ended_at=None,
            output_bytes=0,
            exit_code=None,
        )
        if raw_tool_id and raw_tool_id in tool_by_raw_id:
            existing = tool_by_raw_id[raw_tool_id]
            existing.tool_name = existing.tool_name or tool.tool_name
            existing.tool_type = existing.tool_type or tool.tool_type
            existing.started_at = existing.started_at or tool.started_at
            existing.turn_id = existing.turn_id or tool.turn_id
        else:
            tool_events.append(tool)
            if raw_tool_id:
                tool_by_raw_id[raw_tool_id] = tool


def _collect_tool_results(
    tool_events: list[NormalizedToolEvent],
    tool_by_raw_id: dict[str, NormalizedToolEvent],
    session: NormalizedSession,
    message: dict[str, Any],
    event_time: str | None,
    session_privacy_flags: set[str],
    session_policy_flags: set[str],
) -> None:
    for item in _content_items(message):
        if item.get("type") != "tool_result":
            continue
        raw_tool_id = _first_str(item, "tool_use_id")
        output_bytes = _content_bytes(item.get("content"))
        session_privacy_flags.update(detect_privacy_flags(item.get("content")))
        session_policy_flags.update(detect_policy_flags(item.get("content")))
        tool = tool_by_raw_id.get(raw_tool_id or "")
        if tool is None:
            tool = NormalizedToolEvent(
                id=stable_id("tool", session.id, raw_tool_id or len(tool_events) + 1),
                session_id=session.id,
                turn_id=None,
                provider="claude",
                tool_type="unknown",
                tool_name=None,
                status="completed",
                started_at=None,
                ended_at=event_time,
                output_bytes=output_bytes,
                exit_code=None,
            )
            tool_events.append(tool)
            if raw_tool_id:
                tool_by_raw_id[raw_tool_id] = tool
        else:
            tool.status = "completed"
            tool.ended_at = event_time or tool.ended_at
            tool.output_bytes = max(tool.output_bytes, output_bytes)
            tool.duration_ms = duration_ms(tool.started_at, tool.ended_at)


def _content_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [item for item in content if isinstance(item, dict)]


def _content_bytes(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value.encode("utf-8", errors="replace"))
    if isinstance(value, list):
        total = 0
        for item in value:
            if isinstance(item, dict):
                total += _content_bytes(item.get("text") or item.get("content"))
            else:
                total += _content_bytes(item)
        return total
    if isinstance(value, dict):
        return sum(_content_bytes(item) for item in value.values())
    return len(str(value).encode("utf-8", errors="replace"))


def _event_flags(event: dict[str, Any]) -> tuple[set[str], set[str]]:
    privacy_flags: set[str] = set()
    policy_flags: set[str] = set()
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    for key in ("content", "diagnostics", "stop_details"):
        if key in message:
            privacy_flags.update(detect_privacy_flags(message[key]))
            policy_flags.update(detect_policy_flags(message[key]))
    for key in ("content", "attachment", "operation"):
        if key in event:
            privacy_flags.update(detect_privacy_flags(event[key]))
            policy_flags.update(detect_policy_flags(event[key]))
    return privacy_flags, policy_flags


def _merge_turn_flags(
    turn: NormalizedTurn,
    privacy_flags: set[str],
    policy_flags: set[str],
) -> None:
    existing_privacy = set(turn.privacy_flags.split(",")) if turn.privacy_flags else set()
    existing_policy = set(turn.policy_flags.split(",")) if turn.policy_flags else set()
    existing_privacy.update(privacy_flags)
    existing_policy.update(policy_flags)
    turn.privacy_flags = flags_to_text(existing_privacy)
    turn.policy_flags = flags_to_text(existing_policy)


def _assistant_failed(message: dict[str, Any]) -> bool:
    stop_reason = (_first_str(message, "stop_reason") or "").lower()
    return stop_reason in {"error", "max_tokens"} or _looks_failed(message)


def _looks_failed(mapping: dict[str, Any]) -> bool:
    status = (_first_str(mapping, "status", "type", "stop_reason") or "").lower()
    if status in {"failed", "error"}:
        return True
    if mapping.get("error"):
        return True
    return False


def _mark_failed(turn: NormalizedTurn, message: dict[str, Any], event_time: str | None) -> None:
    turn.status = "failed"
    turn.ended_at = event_time or turn.ended_at
    turn.retry_count += 1
    turn.error_type = _first_str(message, "stop_reason", "type") or "error"
    error_text = _first_str(message, "error", "stop_sequence")
    if error_text:
        turn.error_message_hash = sha256_text(redact_secrets(error_text))


def _claude_tool_type(name: str | None) -> str:
    if not name:
        return "unknown"
    lowered = name.lower()
    if lowered == "bash":
        return "shell"
    if lowered in {"edit", "write", "read"}:
        return "file"
    if lowered.startswith("web"):
        return "web"
    if lowered.startswith("mcp__"):
        return "mcp"
    return "tool"


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
