from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class NormalizedSession:
    id: str
    provider: str
    project_path: str | None = None
    source_file: str | None = None
    source_file_hash: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    status: str | None = None
    model: str | None = None
    task_type: str | None = None
    duration_ms: int | None = None
    retry_count: int = 0
    background_flag: bool = False
    estimated_cost_usd: float | None = None
    credit_estimate: float | None = None
    privacy_flags: str | None = None
    policy_flags: str | None = None
    raw_event_count: int = 0
    malformed_line_count: int = 0
    created_at: str = ""


@dataclass
class NormalizedTurn:
    id: str
    session_id: str
    provider: str
    started_at: str | None = None
    ended_at: str | None = None
    status: str | None = None
    model: str | None = None
    task_type: str | None = None
    duration_ms: int | None = None
    retry_count: int = 0
    background_flag: bool = False
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    estimated_cost_usd: float | None = None
    credit_estimate: float | None = None
    privacy_flags: str | None = None
    policy_flags: str | None = None
    error_type: str | None = None
    error_message_hash: str | None = None


@dataclass
class NormalizedToolEvent:
    id: str
    session_id: str
    turn_id: str | None
    provider: str
    tool_type: str | None = None
    tool_name: str | None = None
    status: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    output_bytes: int = 0
    exit_code: int | None = None


@dataclass
class Issue:
    id: str
    session_id: str | None
    severity: str
    code: str
    title: str
    detail: str | None = None
    recommendation: str | None = None
    evidence: str | None = None
    created_at: str = ""


@dataclass
class ParsedRecords:
    sessions: list[NormalizedSession] = field(default_factory=list)
    turns: list[NormalizedTurn] = field(default_factory=list)
    tool_events: list[NormalizedToolEvent] = field(default_factory=list)
    malformed_line_count: int = 0
    source_file: str | None = None
    source_file_hash: str | None = None
