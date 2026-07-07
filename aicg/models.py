from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class NormalizedSession:
    id: str
    provider: str
    native_session_id: str | None = None
    lineage_id: str | None = None
    parent_session_id: str | None = None
    project_path: str | None = None
    source_file: str | None = None
    source_file_hash: str | None = None
    source_line_start: int | None = None
    source_line_end: int | None = None
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
    cost_source: str | None = None
    wasted_cost_usd: float | None = None
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
    native_turn_key: str | None = None
    source_file_hash: str | None = None
    source_line_start: int | None = None
    source_line_end: int | None = None
    started_at: str | None = None
    ended_at: str | None = None
    status: str | None = None
    model: str | None = None
    task_type: str | None = None
    duration_ms: int | None = None
    retry_count: int = 0
    background_flag: bool = False
    input_uncached_tokens: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    estimated_cost_usd: float | None = None
    credit_estimate: float | None = None
    cost_source: str | None = None
    privacy_flags: str | None = None
    policy_flags: str | None = None
    token_flags: str | None = None
    error_type: str | None = None
    error_message_hash: str | None = None


@dataclass
class NormalizedToolEvent:
    id: str
    session_id: str
    turn_id: str | None
    provider: str
    source_file_hash: str | None = None
    source_line_start: int | None = None
    source_line_end: int | None = None
    tool_type: str | None = None
    tool_name: str | None = None
    status: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    output_bytes: int = 0
    exit_code: int | None = None
    call_target: str | None = None


@dataclass
class PolicyFinding:
    id: str
    session_id: str | None
    tool_event_id: str | None
    rule_id: str
    level: str
    surface: str
    detail_hash: str | None
    first_seen_at: str
    last_seen_at: str


@dataclass
class ReviewFinding:
    id: str
    session_id: str
    ruleset_version: int
    code: str
    confidence: str
    detail: str
    recommendation: str
    created_at: str = ""
    evidence_pointers: list["ReviewEvidence"] = field(default_factory=list)


@dataclass
class ReviewEvidence:
    id: str
    finding_id: str
    session_id: str
    turn_id: str | None = None
    tool_event_id: str | None = None
    source_file_hash: str | None = None
    source_line_start: int | None = None
    source_line_end: int | None = None
    metric_name: str | None = None
    metric_value: str | None = None
    message_hash: str | None = None
    created_at: str = ""


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
    evidence_pointers: list["EvidencePointer"] = field(default_factory=list)


@dataclass
class EvidencePointer:
    id: str
    issue_id: str
    session_id: str | None = None
    turn_id: str | None = None
    tool_event_id: str | None = None
    source_file_hash: str | None = None
    source_line_start: int | None = None
    source_line_end: int | None = None
    metric_name: str | None = None
    metric_value: str | None = None
    message_hash: str | None = None
    created_at: str = ""


@dataclass
class ParsedRecords:
    sessions: list[NormalizedSession] = field(default_factory=list)
    turns: list[NormalizedTurn] = field(default_factory=list)
    tool_events: list[NormalizedToolEvent] = field(default_factory=list)
    policy_findings: list[PolicyFinding] = field(default_factory=list)
    malformed_line_count: int = 0
    source_file: str | None = None
    source_file_hash: str | None = None
