from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from .base import Capabilities
from ..policy import EffectivePolicy, load_policy, sensitive_policy_findings, sensitive_policy_flags
from ..security_audit import derive_security_tool_metadata
from ..models import (
    PolicyFinding,
    NormalizedSession,
    NormalizedToolEvent,
    NormalizedTurn,
    ParsedRecords,
)
from ..util import (
    detect_policy_flags,
    detect_privacy_flags,
    duration_ms,
    call_targets_to_text,
    extract_call_targets,
    flags_to_text,
    hash_file,
    normalize_timestamp,
    raw_hash_file,
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


KNOWN_EVENT_TYPES = {
    "assistant",
    "ai-title",
    "attachment",
    "last-prompt",
    "user",
    "summary",
    "system",
    "queue-operation",
}


KNOWN_ROLES = {"assistant", "user", "system"}


KNOWN_TOP_LEVEL_FIELDS = {
    "type",
    "uuid",
    "parentUuid",
    "sessionId",
    "session_id",
    "cwd",
    "timestamp",
    "created_at",
    "time",
    "message",
    "isSidechain",
    "userType",
    "version",
    "gitBranch",
    "requestId",
    "toolUseID",
    "entrypoint",
    "permissionMode",
    "model",
    "role",
    "summary",
    "title",
    "content",
    "prompt",
    "lastPrompt",
    "attachment",
    "attachments",
    "usage",
    "tokenUsage",
    "token_usage",
}


class ClaudeJsonlReader:
    provider = "claude"

    def __init__(self, policy: EffectivePolicy | None = None) -> None:
        self.policy = policy or load_policy()

    def capabilities(self) -> Capabilities:
        return Capabilities(
            token_usage=True,
            cache_semantics="disjoint",
            tool_events=True,
            turn_status=True,
            session_resume=True,
            background_flag=True,
            native_cost=False,
        )

    def discover(self, since, paths: dict[str, Path] | None = None, known_sources: set[str] | None = None):
        root = _claude_projects_dir()
        yield from _jsonl_files_since(root, since, known_sources)

    def read(self, path: Path) -> ParsedRecords:
        source = Path(path)
        source_hash = hash_file(source)
        source_identity_hash = raw_hash_file(source)
        created_at = utc_now_iso()
        session: NormalizedSession | None = None
        turns: list[NormalizedTurn] = []
        tool_events: list[NormalizedToolEvent] = []
        policy_findings: list[PolicyFinding] = []
        turn_by_message_id: dict[str, NormalizedTurn] = {}
        tool_by_raw_id: dict[str, NormalizedToolEvent] = {}
        raw_event_count = 0
        malformed_line_count = 0
        unknown_event_types: Counter[str] = Counter()
        unrecognized_field_count = 0
        total_field_count = 0
        current_line_number = 0
        first_seen_at: str | None = None
        last_seen_at: str | None = None
        session_used_source_hash = False
        session_privacy_flags: set[str] = set()
        session_policy_flags: set[str] = set()

        def ensure_session(event: dict[str, Any] | None = None) -> NormalizedSession:
            nonlocal session, session_used_source_hash
            event = event or {}
            root_session_id = _first_str(event, "sessionId", "session_id")
            agent_id = _first_str(event, "agentId") if _is_sidechain(event) else None
            raw_session_id = agent_id or root_session_id
            parent_session_id = root_session_id if agent_id and root_session_id != agent_id else None
            if session is not None:
                if raw_session_id and session_used_source_hash:
                    _rekey_session(
                        session,
                        turns,
                        tool_events,
                        stable_id("session", self.provider, raw_session_id),
                    )
                    session.native_session_id = raw_session_id
                    session.lineage_id = root_session_id or raw_session_id
                    session.parent_session_id = parent_session_id
                    session_used_source_hash = False
                if parent_session_id:
                    session.parent_session_id = parent_session_id
                    session.lineage_id = root_session_id or parent_session_id
                session.project_path = _first_str(event, "cwd") or session.project_path
                session.source_line_start = session.source_line_start or current_line_number
                session.source_line_end = current_line_number or session.source_line_end
                session.model = session.model or _message_model(event)
                session.task_type = session.task_type or _task_type(event)
                session.background_flag = session.background_flag or _is_sidechain(event)
                return session
            session_used_source_hash = raw_session_id is None
            session_id = stable_id(
                "session",
                self.provider,
                raw_session_id or source_identity_hash,
            )
            session = NormalizedSession(
                id=session_id,
                provider=self.provider,
                native_session_id=raw_session_id,
                lineage_id=root_session_id or raw_session_id or source_identity_hash,
                parent_session_id=parent_session_id,
                project_path=_first_str(event, "cwd") or _project_from_path(source),
                source_file=str(source),
                source_file_hash=source_hash,
                source_line_start=current_line_number or None,
                source_line_end=current_line_number or None,
                started_at=_timestamp(event) or first_seen_at,
                status="running",
                model=_message_model(event),
                task_type=_task_type(event),
                background_flag=_is_sidechain(event),
                raw_event_count=0,
                malformed_line_count=0,
                created_at=created_at,
            )
            return session

        with source.open("r", encoding="utf-8", errors="replace") as handle:
            for current_line_number, line in enumerate(handle, start=1):
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
                event_type = _first_str(event, "type")
                message = event.get("message") if isinstance(event.get("message"), dict) else {}
                role = _first_str(message, "role")
                unknown_event_types.update(_unknown_event_type_labels(event_type, role))
                unknown_fields, total_fields = _top_level_field_drift(event, KNOWN_TOP_LEVEL_FIELDS)
                unrecognized_field_count += unknown_fields
                total_field_count += total_fields
                event_time = _timestamp(event)
                if event_time and first_seen_at is None:
                    first_seen_at = event_time
                if event_time:
                    last_seen_at = event_time
                active_session = ensure_session(event)

                privacy_flags, policy_flags = _event_flags(event, self.policy)
                session_privacy_flags.update(privacy_flags)
                session_policy_flags.update(policy_flags)

                if event_type == "assistant" or role == "assistant":
                    raw_message_id = _first_str(message, "id") or _first_str(event, "uuid")
                    if raw_message_id and raw_message_id in turn_by_message_id:
                        turn = turn_by_message_id[raw_message_id]
                    else:
                        turn = NormalizedTurn(
                            id=stable_id(
                                "turn",
                                self.provider,
                                raw_message_id,
                            )
                            if raw_message_id
                            else stable_id(
                                "turn",
                                active_session.id,
                                source_identity_hash,
                                len(turns) + 1,
                            ),
                            session_id=active_session.id,
                            provider=self.provider,
                            native_turn_key=f"{self.provider}:{raw_message_id}" if raw_message_id else f"{self.provider}:{source_identity_hash}:{len(turns) + 1}",
                            source_file_hash=source_hash,
                            source_line_start=current_line_number,
                            source_line_end=current_line_number,
                            started_at=event_time,
                            ended_at=event_time,
                            status="completed",
                            model=_message_model(event) or active_session.model,
                            task_type=_task_type(event) or active_session.task_type,
                            background_flag=active_session.background_flag or _is_sidechain(event),
                        )
                        turns.append(turn)
                        if raw_message_id:
                            turn_by_message_id[raw_message_id] = turn
                    turn.source_line_end = current_line_number or turn.source_line_end
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
                        policy_findings,
                        self.policy,
                        source_hash,
                        current_line_number,
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
                        policy_findings,
                        self.policy,
                        source_hash,
                        current_line_number,
                    )
                    continue

                side_event_usage = _usage_from_event(event)
                if side_event_usage is not None:
                    raw_message_id = (
                        _first_str(message, "id")
                        or _first_str(event, "uuid", "requestId", "toolUseID")
                        or f"{source_identity_hash}:{current_line_number}"
                    )
                    native_turn_key = f"{self.provider}:{event_type or 'event'}:{raw_message_id}"
                    turn = NormalizedTurn(
                        id=stable_id("turn", self.provider, native_turn_key),
                        session_id=active_session.id,
                        provider=self.provider,
                        native_turn_key=native_turn_key,
                        source_file_hash=source_hash,
                        source_line_start=current_line_number,
                        source_line_end=current_line_number,
                        started_at=event_time,
                        ended_at=event_time,
                        status="completed",
                        model=_message_model(event) or active_session.model,
                        task_type=_task_type(event) or active_session.task_type,
                        background_flag=active_session.background_flag or _is_sidechain(event),
                    )
                    _apply_usage_values(turn, side_event_usage)
                    _add_turn_token_flag(turn, "CLAUDE_SIDE_EVENT_USAGE")
                    _merge_turn_flags(turn, privacy_flags, policy_flags)
                    turns.append(turn)
                    active_session.model = turn.model or active_session.model
                    continue

                if event_type in {"system", "queue-operation"} and _looks_failed(event):
                    active_session.status = "failed"

        active_session = ensure_session({})
        active_session.raw_event_count = raw_event_count
        active_session.malformed_line_count = malformed_line_count
        active_session.started_at = active_session.started_at or first_seen_at
        active_session.ended_at = active_session.ended_at or last_seen_at
        active_session.source_line_start = active_session.source_line_start or 1
        active_session.source_line_end = active_session.source_line_end or current_line_number or None
        active_session.duration_ms = duration_ms(active_session.started_at, active_session.ended_at)
        active_session.retry_count = sum(turn.retry_count for turn in turns)
        active_session.privacy_flags = flags_to_text(session_privacy_flags)
        active_session.policy_flags = flags_to_text(session_policy_flags)
        if active_session.status == "running":
            if not raw_event_count:
                active_session.status = "unknown"
            elif any(turn.status == "running" for turn in turns):
                active_session.status = "interrupted"
            elif turns:
                active_session.status = "completed"
            else:
                active_session.status = "unknown"
        for turn in turns:
            turn.duration_ms = duration_ms(turn.started_at, turn.ended_at)
            turn.source_line_end = turn.source_line_end or turn.source_line_start
            if turn.status == "running":
                turn.status = "interrupted"
        return ParsedRecords(
            sessions=[active_session],
            turns=turns,
            tool_events=tool_events,
            policy_findings=policy_findings,
            malformed_line_count=malformed_line_count,
            source_file=str(source),
            source_file_hash=source_hash,
            unknown_event_types=unknown_event_types,
            unrecognized_field_ratio=(unrecognized_field_count / total_field_count) if total_field_count else 0.0,
            unrecognized_field_count=unrecognized_field_count,
            total_field_count=total_field_count,
        )


def _unknown_event_type_labels(event_type: str | None, role: str | None) -> list[str]:
    if event_type in KNOWN_EVENT_TYPES or role in KNOWN_ROLES:
        return []
    if event_type:
        return [event_type]
    if role:
        return [f"role:{role}"]
    return ["<missing>"]


def _top_level_field_drift(event: dict[str, Any], known_fields: set[str]) -> tuple[int, int]:
    total = len(event)
    unknown = sum(1 for key in event if key not in known_fields)
    return unknown, total


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
    return _first_str(event, "entrypoint", "permissionMode")


def _is_sidechain(event: dict[str, Any]) -> bool:
    return event.get("isSidechain") is True


def _rekey_session(
    session: NormalizedSession,
    turns: list[NormalizedTurn],
    tool_events: list[NormalizedToolEvent],
    new_session_id: str,
) -> None:
    if session.id == new_session_id:
        return
    session.id = new_session_id
    for turn in turns:
        turn.session_id = new_session_id
    for event in tool_events:
        event.session_id = new_session_id


def _project_from_path(source: Path) -> str | None:
    parent = source.parent.name
    if parent.startswith("-"):
        return "/" + parent[1:].replace("-", "/")
    return None


def _claude_projects_dir() -> Path:
    if "CLAUDE_CONFIG_DIR" in os.environ:
        return Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser() / "projects"
    return Path("~/.claude/projects").expanduser()


def _jsonl_files_since(root: Path, cutoff, known_sources: set[str] | None = None) -> list[Path]:
    if not root.exists():
        return []
    known_sources = known_sources or set()
    files: list[Path] = []
    for path in root.rglob("*.jsonl"):
        try:
            if path.is_file() and (str(path) not in known_sources or path.stat().st_mtime >= cutoff.timestamp()):
                files.append(path)
        except OSError:
            continue
    return sorted(files)


def _apply_usage(turn: NormalizedTurn, message: dict[str, Any]) -> None:
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return
    _apply_usage_values(turn, usage)


def _apply_usage_values(turn: NormalizedTurn, usage: dict[str, Any]) -> None:
    for field in TOKEN_FIELDS:
        parsed = _int_or_none(usage.get(field))
        if parsed is not None:
            setattr(turn, field, parsed)
    turn.input_uncached_tokens = turn.input_tokens


def _usage_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    for candidate in (
        message.get("usage"),
        event.get("usage"),
        event.get("tokenUsage"),
        event.get("token_usage"),
    ):
        if not isinstance(candidate, dict):
            continue
        if any(_int_or_none(candidate.get(field)) is not None for field in TOKEN_FIELDS):
            return candidate
    return None


def _add_turn_token_flag(turn: NormalizedTurn, flag: str) -> None:
    existing = set(turn.token_flags.split(",")) if turn.token_flags else set()
    existing.add(flag)
    turn.token_flags = flags_to_text(existing)


def _collect_tool_use(
    tool_events: list[NormalizedToolEvent],
    tool_by_raw_id: dict[str, NormalizedToolEvent],
    session: NormalizedSession,
    turn: NormalizedTurn,
    message: dict[str, Any],
    event_time: str | None,
    session_privacy_flags: set[str],
    session_policy_flags: set[str],
    policy_findings: list[PolicyFinding],
    policy: EffectivePolicy,
    source_file_hash: str,
    source_line: int,
) -> None:
    for item in _content_items(message):
        if item.get("type") != "tool_use":
            continue
        raw_tool_id = _first_str(item, "id")
        security_metadata = derive_security_tool_metadata(item.get("input"))
        session_privacy_flags.update(detect_privacy_flags(item.get("input")))
        custom_privacy, custom_policy = sensitive_policy_flags(policy, item.get("input"))
        session_privacy_flags.update(custom_privacy)
        session_policy_flags.update(custom_policy)
        session_policy_flags.update(detect_policy_flags(item.get("input")))
        tool = NormalizedToolEvent(
            id=stable_id("tool", "claude", raw_tool_id)
            if raw_tool_id
            else stable_id("tool", session.id, turn.id, len(tool_events) + 1),
            session_id=session.id,
            turn_id=turn.id,
            provider="claude",
            source_file_hash=source_file_hash,
            source_line_start=source_line,
            source_line_end=source_line,
            tool_type=_claude_tool_type(_first_str(item, "name")),
            tool_name=_first_str(item, "name"),
            status="running",
            started_at=event_time,
            ended_at=None,
            output_bytes=0,
            exit_code=None,
            call_target=call_targets_to_text(extract_call_targets(item.get("input"))),
            security_flags=security_metadata.get("security_flags"),
            security_detail=security_metadata.get("security_detail"),
            command_hash=security_metadata.get("command_hash"),
        )
        if raw_tool_id and raw_tool_id in tool_by_raw_id:
            existing = tool_by_raw_id[raw_tool_id]
            existing.tool_name = existing.tool_name or tool.tool_name
            existing.tool_type = existing.tool_type or tool.tool_type
            existing.started_at = existing.started_at or tool.started_at
            existing.turn_id = existing.turn_id or tool.turn_id
            existing.source_file_hash = existing.source_file_hash or source_file_hash
            existing.source_line_start = existing.source_line_start or source_line
            existing.source_line_end = source_line
            existing.call_target = existing.call_target or call_targets_to_text(extract_call_targets(item.get("input")))
            existing.security_flags = existing.security_flags or security_metadata.get("security_flags")
            existing.security_detail = existing.security_detail or security_metadata.get("security_detail")
            existing.command_hash = existing.command_hash or security_metadata.get("command_hash")
            tool = existing
        else:
            tool_events.append(tool)
            if raw_tool_id:
                tool_by_raw_id[raw_tool_id] = tool
        policy_findings.extend(
            sensitive_policy_findings(
                policy,
                value=item.get("input"),
                session_id=session.id,
                tool_event_id=tool.id,
                surface="call",
                now=event_time,
            )
        )


def _collect_tool_results(
    tool_events: list[NormalizedToolEvent],
    tool_by_raw_id: dict[str, NormalizedToolEvent],
    session: NormalizedSession,
    message: dict[str, Any],
    event_time: str | None,
    session_privacy_flags: set[str],
    session_policy_flags: set[str],
    policy_findings: list[PolicyFinding],
    policy: EffectivePolicy,
    source_file_hash: str,
    source_line: int,
) -> None:
    for item in _content_items(message):
        if item.get("type") != "tool_result":
            continue
        raw_tool_id = _first_str(item, "tool_use_id")
        output_bytes = _content_bytes(item.get("content"))
        session_privacy_flags.update(detect_privacy_flags(item.get("content")))
        custom_privacy, custom_policy = sensitive_policy_flags(policy, item.get("content"))
        session_privacy_flags.update(custom_privacy)
        session_policy_flags.update(custom_policy)
        tool = tool_by_raw_id.get(raw_tool_id or "")
        if tool is None:
            tool = NormalizedToolEvent(
                id=stable_id("tool", "claude", raw_tool_id)
                if raw_tool_id
                else stable_id("tool", session.id, len(tool_events) + 1),
                session_id=session.id,
                turn_id=None,
                provider="claude",
                source_file_hash=source_file_hash,
                source_line_start=source_line,
                source_line_end=source_line,
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
            tool.source_file_hash = tool.source_file_hash or source_file_hash
            tool.source_line_start = tool.source_line_start or source_line
            tool.source_line_end = source_line
            tool.duration_ms = duration_ms(tool.started_at, tool.ended_at)
        policy_findings.extend(
            sensitive_policy_findings(
                policy,
                value=item.get("content"),
                session_id=session.id,
                tool_event_id=tool.id,
                surface="output",
                now=event_time,
            )
        )


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


def _event_flags(event: dict[str, Any], policy: EffectivePolicy) -> tuple[set[str], set[str]]:
    privacy_flags: set[str] = set()
    policy_flags: set[str] = set()
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    for key in ("content", "diagnostics", "stop_details"):
        if key in message:
            privacy_flags.update(detect_privacy_flags(message[key]))
            custom_privacy, custom_policy = sensitive_policy_flags(policy, message[key])
            privacy_flags.update(custom_privacy)
            policy_flags.update(custom_policy)
    for key in ("content", "attachment", "operation"):
        if key in event:
            privacy_flags.update(detect_privacy_flags(event[key]))
            custom_privacy, custom_policy = sensitive_policy_flags(policy, event[key])
            privacy_flags.update(custom_privacy)
            policy_flags.update(custom_policy)
            if key == "operation":
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
    was_failed = turn.status == "failed"
    turn.status = "failed"
    turn.ended_at = event_time or turn.ended_at
    if not was_failed:
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
