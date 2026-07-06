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
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


TOOL_TYPE_MAP = {
    "command_execution": "shell",
    "file_change": "file_edit",
    "file_edit": "file_edit",
    "mcp_tool_call": "mcp",
    "web_search": "web_search",
    "function_call": "tool",
    "function_call_output": "tool",
    "custom_tool_call": "shell",
    "custom_tool_call_output": "shell",
    "web_search_call": "web_search",
    "tool_search_call": "tool_search",
    "tool_search_output": "tool_search",
}


class CodexJsonlReader:
    provider = "codex"

    def read(self, path: Path) -> ParsedRecords:
        source = Path(path)
        source_hash = hash_file(source)
        created_at = utc_now_iso()
        session: NormalizedSession | None = None
        turns: list[NormalizedTurn] = []
        tool_events: list[NormalizedToolEvent] = []
        turn_by_raw_id: dict[str, NormalizedTurn] = {}
        tool_by_raw_id: dict[str, NormalizedToolEvent] = {}
        current_turn: NormalizedTurn | None = None
        turn_index = 0
        item_index = 0
        raw_event_count = 0
        malformed_line_count = 0
        first_seen_at: str | None = None
        last_seen_at: str | None = None
        session_privacy_flags: set[str] = set()
        session_policy_flags: set[str] = set()

        def ensure_session(event: dict[str, Any] | None = None) -> NormalizedSession:
            nonlocal session
            payload = _payload(event or {})
            if session is not None:
                _merge_session_metadata(session, payload)
                return session
            raw_session_id = _first_str(payload, "id", "thread_id", "session_id")
            session_id = (
                stable_id("session", self.provider, raw_session_id)
                if raw_session_id
                else stable_id("session", self.provider, source_hash)
            )
            timestamp = _event_timestamp(event or {})
            session = NormalizedSession(
                id=session_id,
                provider=self.provider,
                project_path=_project_path(payload),
                source_file=str(source),
                source_file_hash=source_hash,
                started_at=timestamp or first_seen_at,
                status="running",
                model=_first_str(payload, "model"),
                task_type=_task_type(payload),
                background_flag=_is_background(payload),
                raw_event_count=0,
                malformed_line_count=0,
                created_at=created_at,
            )
            return session

        def start_turn(
            event: dict[str, Any],
            raw_turn_id: str | None = None,
        ) -> NormalizedTurn:
            nonlocal current_turn, turn_index
            active_session = ensure_session(event)
            payload = _payload(event)
            raw_id = raw_turn_id or _first_str(payload, "turn_id", "id")
            if raw_id and raw_id in turn_by_raw_id:
                current_turn = turn_by_raw_id[raw_id]
                return current_turn
            turn_index += 1
            identity = raw_id or str(turn_index)
            current_turn = NormalizedTurn(
                id=stable_id("turn", active_session.id, identity),
                session_id=active_session.id,
                provider=self.provider,
                started_at=_event_timestamp(event),
                status="running",
                model=_first_str(payload, "model") or active_session.model,
                task_type=_task_type(payload) or active_session.task_type,
                background_flag=active_session.background_flag or _is_background(payload),
            )
            turns.append(current_turn)
            if raw_id:
                turn_by_raw_id[raw_id] = current_turn
            return current_turn

        def turn_for_event(event: dict[str, Any]) -> NormalizedTurn:
            payload = _payload(event)
            raw_turn_id = _first_str(payload, "turn_id")
            if raw_turn_id and raw_turn_id in turn_by_raw_id:
                return turn_by_raw_id[raw_turn_id]
            if current_turn is not None:
                return current_turn
            return start_turn(event, raw_turn_id)

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
                event_type = _event_type(event)
                payload = _payload(event)
                event_time = _event_timestamp(event)
                if event_time and first_seen_at is None:
                    first_seen_at = event_time
                if event_time:
                    last_seen_at = event_time

                privacy_flags, policy_flags = _payload_flags(payload)
                session_privacy_flags.update(privacy_flags)
                session_policy_flags.update(policy_flags)

                if event_type in {"session_meta", "thread.started"}:
                    active_session = ensure_session(event)
                    _merge_session_metadata(active_session, payload)
                    continue

                if event_type == "turn_context":
                    turn = start_turn(event, _first_str(payload, "turn_id"))
                    _merge_turn_flags(turn, privacy_flags, policy_flags)
                    if payload.get("model"):
                        turn.model = _first_str(payload, "model")
                        ensure_session(event).model = turn.model
                    continue

                if event_type == "event_msg":
                    payload_type = _first_str(payload, "type")
                    if payload_type == "task_started":
                        turn = start_turn(event, _first_str(payload, "turn_id"))
                        turn.started_at = _first_str(payload, "started_at") or event_time
                        turn.task_type = _task_type(payload) or turn.task_type
                        _merge_turn_flags(turn, privacy_flags, policy_flags)
                    elif payload_type == "token_count":
                        turn = turn_for_event(event)
                        usage = _token_count_usage(payload)
                        if usage:
                            _apply_usage(turn, usage)
                            turn.status = "completed"
                            turn.ended_at = event_time or turn.ended_at
                        _merge_turn_flags(turn, privacy_flags, policy_flags)
                    elif _looks_failed(payload):
                        active_session = ensure_session(event)
                        turn = turn_for_event(event)
                        _mark_failed(turn, payload, event_time)
                        active_session.status = "failed"
                        active_session.ended_at = event_time or active_session.ended_at
                    continue

                if event_type in {"turn.started"}:
                    turn = start_turn(event)
                    _merge_turn_flags(turn, privacy_flags, policy_flags)
                    continue

                if event_type == "turn.completed":
                    turn = turn_for_event(event)
                    turn.status = "completed"
                    turn.ended_at = event_time
                    turn.model = _first_str(payload, "model") or turn.model
                    _apply_usage(turn, payload)
                    _merge_turn_flags(turn, privacy_flags, policy_flags)
                    active_session = ensure_session(event)
                    if active_session.status != "failed":
                        active_session.status = "completed"
                    active_session.ended_at = event_time or active_session.ended_at
                    active_session.model = turn.model or active_session.model
                    continue

                if event_type in {"turn.failed", "error"}:
                    active_session = ensure_session(event)
                    turn = turn_for_event(event)
                    _mark_failed(turn, payload, event_time)
                    _merge_turn_flags(turn, privacy_flags, policy_flags)
                    active_session.status = "failed"
                    active_session.ended_at = event_time or active_session.ended_at
                    continue

                if event_type in {"item.started", "item.completed"}:
                    active_session = ensure_session(event)
                    item_payload = _item_payload(event)
                    item_type = _first_str(item_payload, "type", "item_type") or _first_str(
                        payload, "item_type"
                    )
                    raw_item_id = _first_str(
                        item_payload, "id", "item_id", "call_id"
                    ) or _first_str(payload, "item_id", "call_id")
                    item_privacy, item_policy = _payload_flags(item_payload)
                    session_privacy_flags.update(item_privacy)
                    session_policy_flags.update(item_policy)
                    item_index += 1
                    tool = _upsert_tool_event(
                        tool_events,
                        tool_by_raw_id,
                        active_session,
                        current_turn,
                        raw_item_id,
                        item_index,
                        TOOL_TYPE_MAP.get(item_type or "", "unknown"),
                        _tool_name(item_payload, item_type),
                        "running" if event_type == "item.started" else "completed",
                        event_time if event_type == "item.started" else None,
                        event_time if event_type == "item.completed" else None,
                        _estimate_output_bytes(item_payload),
                        _int_or_none(item_payload.get("exit_code")),
                    )
                    if event_type == "item.completed":
                        tool.status = _first_str(item_payload, "status") or "completed"
                    continue

                if event_type == "response_item":
                    active_session = ensure_session(event)
                    response_type = _first_str(payload, "type")
                    if response_type in TOOL_TYPE_MAP:
                        raw_item_id = _first_str(payload, "call_id", "id")
                        item_index += 1
                        status = _first_str(payload, "status") or "completed"
                        if response_type.endswith("_output"):
                            status = "completed"
                        tool = _upsert_tool_event(
                            tool_events,
                            tool_by_raw_id,
                            active_session,
                            current_turn,
                            raw_item_id,
                            item_index,
                            TOOL_TYPE_MAP.get(response_type or "", "unknown"),
                            _tool_name(payload, response_type),
                            status,
                            event_time,
                            event_time if response_type.endswith("_output") else None,
                            _estimate_output_bytes(payload),
                            None,
                        )
                        if response_type.endswith("_output"):
                            tool.output_bytes = max(
                                tool.output_bytes,
                                _estimate_output_bytes(payload),
                            )
                            tool.ended_at = event_time or tool.ended_at
                            tool.status = "completed"
                            tool.duration_ms = duration_ms(tool.started_at, tool.ended_at)
                    if _looks_failed(payload):
                        turn = turn_for_event(event)
                        _mark_failed(turn, payload, event_time)
                        active_session.status = "failed"
                    if current_turn is not None:
                        _merge_turn_flags(current_turn, privacy_flags, policy_flags)
                    continue

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
            if turn.status == "running":
                turn.status = "completed"

        return ParsedRecords(
            sessions=[active_session],
            turns=turns,
            tool_events=tool_events,
            malformed_line_count=malformed_line_count,
            source_file=str(source),
            source_file_hash=source_hash,
        )


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, dict):
        return payload
    return event


def _event_type(event: dict[str, Any]) -> str | None:
    return _first_str(event, "type", "event", "event_type")


def _event_timestamp(event: dict[str, Any]) -> str | None:
    payload = _payload(event)
    return normalize_timestamp(
        _first_str(payload, "timestamp", "time", "created_at", "started_at", "ended_at")
        or _first_str(event, "timestamp", "time", "created_at", "started_at", "ended_at")
    )


def _first_str(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _project_path(payload: dict[str, Any]) -> str | None:
    return _first_str(payload, "project_path", "cwd", "working_dir")


def _task_type(payload: dict[str, Any]) -> str | None:
    return (
        _first_str(payload, "task_type", "collaboration_mode_kind", "collaboration_mode")
        or _first_str(payload, "source")
    )


def _is_background(payload: dict[str, Any]) -> bool:
    source = (_first_str(payload, "source", "originator", "agent_role") or "").lower()
    if source.startswith("subagent") or "background" in source:
        return True
    if payload.get("realtime_active") is True:
        return True
    return False


def _merge_session_metadata(session: NormalizedSession, payload: dict[str, Any]) -> None:
    session.project_path = session.project_path or _project_path(payload)
    session.model = session.model or _first_str(payload, "model")
    session.task_type = session.task_type or _task_type(payload)
    session.background_flag = session.background_flag or _is_background(payload)


def _merge_turn_flags(
    turn: NormalizedTurn,
    privacy_flags: set[str],
    policy_flags: set[str],
) -> None:
    merged_privacy = set(turn.privacy_flags.split(",")) if turn.privacy_flags else set()
    merged_policy = set(turn.policy_flags.split(",")) if turn.policy_flags else set()
    merged_privacy.update(privacy_flags)
    merged_policy.update(policy_flags)
    turn.privacy_flags = flags_to_text(merged_privacy)
    turn.policy_flags = flags_to_text(merged_policy)


def _item_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(event)
    item = payload.get("item")
    if isinstance(item, dict):
        return item
    item = event.get("item")
    if isinstance(item, dict):
        return item
    return payload


def _token_count_usage(payload: dict[str, Any]) -> dict[str, Any]:
    info = payload.get("info")
    if not isinstance(info, dict):
        return {}
    usage = info.get("last_token_usage") or info.get("total_token_usage")
    return usage if isinstance(usage, dict) else {}


def _lookup_usage(event: dict[str, Any]) -> dict[str, Any]:
    usage = event.get("usage")
    if isinstance(usage, dict):
        return usage
    return event


def _apply_usage(turn: NormalizedTurn, event: dict[str, Any]) -> None:
    usage = _lookup_usage(event)
    for field in TOKEN_FIELDS:
        value = usage.get(field, event.get(field))
        parsed = _int_or_none(value)
        if parsed is not None:
            setattr(turn, field, parsed)


def _mark_failed(turn: NormalizedTurn, event: dict[str, Any], event_time: str | None = None) -> None:
    turn.status = "failed"
    turn.ended_at = event_time or _event_timestamp(event) or turn.ended_at
    turn.retry_count += 1
    turn.error_type = _first_str(event, "error_type", "type", "status") or "error"
    message = _error_message(event)
    if message:
        turn.error_message_hash = sha256_text(redact_secrets(message))


def _looks_failed(payload: dict[str, Any]) -> bool:
    status = (_first_str(payload, "status", "type", "reason") or "").lower()
    if status in {"failed", "error", "turn.failed", "tool_error"}:
        return True
    if "error" in payload and payload.get("error"):
        return True
    return False


def _error_message(event: dict[str, Any]) -> str | None:
    for key in ("message", "error_message", "detail", "reason"):
        value = event.get(key)
        if isinstance(value, str):
            return value
    error = event.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        return _first_str(error, "message", "detail")
    return None


def _tool_name(payload: dict[str, Any], item_type: str | None) -> str | None:
    name = _first_str(payload, "tool_name", "name", "mcp_tool_name")
    if name:
        return name
    return item_type


def _estimate_output_bytes(payload: dict[str, Any]) -> int:
    total = 0
    for key in ("output", "result", "stdout", "stderr", "aggregated_output", "formatted_output"):
        value = payload.get(key)
        if isinstance(value, str):
            total += len(value.encode("utf-8", errors="replace"))
    return total


def _payload_flags(payload: dict[str, Any]) -> tuple[set[str], set[str]]:
    privacy_flags: set[str] = set()
    policy_flags: set[str] = set()
    for key in (
        "arguments",
        "input",
        "output",
        "result",
        "stdout",
        "stderr",
        "aggregated_output",
        "formatted_output",
        "command",
        "query",
        "message",
        "content",
        "summary",
    ):
        if key in payload:
            privacy_flags.update(detect_privacy_flags(payload[key]))
            policy_flags.update(detect_policy_flags(payload[key]))
    return privacy_flags, policy_flags


def _upsert_tool_event(
    tool_events: list[NormalizedToolEvent],
    tool_by_raw_id: dict[str, NormalizedToolEvent],
    session: NormalizedSession,
    current_turn: NormalizedTurn | None,
    raw_item_id: str | None,
    item_index: int,
    tool_type: str | None,
    tool_name: str | None,
    status: str | None,
    started_at: str | None,
    ended_at: str | None,
    output_bytes: int,
    exit_code: int | None,
) -> NormalizedToolEvent:
    tool: NormalizedToolEvent | None = None
    if raw_item_id:
        tool = tool_by_raw_id.get(raw_item_id)
    if tool is None:
        tool = NormalizedToolEvent(
            id=stable_id(
                "tool",
                session.id,
                current_turn.id if current_turn else "",
                raw_item_id or item_index,
            ),
            session_id=session.id,
            turn_id=current_turn.id if current_turn else None,
            provider="codex",
            tool_type=tool_type,
            tool_name=tool_name,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            output_bytes=output_bytes,
            exit_code=exit_code,
        )
        tool_events.append(tool)
        if raw_item_id:
            tool_by_raw_id[raw_item_id] = tool
    else:
        tool.tool_type = tool.tool_type or tool_type
        tool.tool_name = tool.tool_name or tool_name
        tool.status = status or tool.status
        tool.started_at = tool.started_at or started_at
        tool.ended_at = ended_at or tool.ended_at
        tool.output_bytes = max(tool.output_bytes, output_bytes)
        tool.exit_code = exit_code if exit_code is not None else tool.exit_code
    tool.duration_ms = duration_ms(tool.started_at, tool.ended_at)
    return tool


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
