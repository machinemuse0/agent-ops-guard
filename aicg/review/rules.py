from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..models import ReviewEvidence, ReviewFinding
from ..util import stable_id
from .features import SessionFeatures, ToolFeature, TurnFeature, load_session_features, timeline_key


REVIEW_RULESET_VERSION = 2
CONFIDENCE_ORDER = {"low": 1, "medium": 2, "high": 3}
DEFAULT_CREATED_AT = "1970-01-01T00:00:00Z"


@dataclass(frozen=True)
class ReviewConfig:
    repeated_error_hash_min: int = 3
    edit_failure_run_min: int = 3
    context_growth_min_turns: int = 5
    context_growth_multiple: float = 3.0
    context_min_start_tokens: int = 100
    context_cache_drop_min: float = 0.15
    tool_output_spike_min_bytes: int = 102400
    tool_output_spike_multiplier: float = 5.0
    tool_output_failure_window_turns: int = 2
    early_shell_window: int = 5
    early_shell_failure_score: float = 2.0
    no_progress_min_turns: int = 20
    no_progress_edit_success_max: float = 0.3
    model_fallback_failure_multiplier: float = 2.0
    model_fallback_min_before_turns: int = 2
    model_fallback_min_after_turns: int = 3
    model_fallback_min_after_failures: int = 2
    model_fallback_failure_rate_delta_min: float = 0.25


@dataclass(frozen=True)
class SessionReview:
    session: SessionFeatures
    findings: list[ReviewFinding]


def load_review_config(config: dict[str, Any] | None) -> ReviewConfig:
    values = dict((config or {}).get("review") or {})
    defaults = ReviewConfig()
    parsed: dict[str, Any] = {}
    for field, default in defaults.__dict__.items():
        raw = values.get(field, default)
        if isinstance(default, int):
            parsed[field] = _positive_int(field, raw)
        else:
            parsed[field] = _positive_float(field, raw)
    if parsed["no_progress_edit_success_max"] > 1:
        raise ValueError("review.no_progress_edit_success_max must be between 0 and 1")
    if parsed["context_cache_drop_min"] > 1:
        raise ValueError("review.context_cache_drop_min must be between 0 and 1")
    return ReviewConfig(**parsed)


def build_session_review(
    conn: sqlite3.Connection,
    session_id: str,
    config: dict[str, Any] | ReviewConfig | None = None,
) -> SessionReview | None:
    session = load_session_features(conn, session_id)
    if session is None:
        return None
    review_config = config if isinstance(config, ReviewConfig) else load_review_config(config)
    findings: list[ReviewFinding] = []
    findings.extend(_repeated_identical_failure(session, review_config))
    findings.extend(_edit_retry_churn(session, review_config))
    findings.extend(_context_overload(session, review_config))
    findings.extend(_tool_output_flood(session, review_config))
    findings.extend(_missing_setup(session, review_config))
    findings.extend(_no_progress_thrashing(session, review_config))
    findings.extend(_model_fallback_degradation(session, review_config))
    findings.extend(_interrupted_tail(session))
    findings.sort(key=lambda item: (-CONFIDENCE_ORDER[item.confidence], item.code, item.id))
    return SessionReview(session=session, findings=findings)


def _repeated_identical_failure(session: SessionFeatures, config: ReviewConfig) -> list[ReviewFinding]:
    best_run: list[TurnFeature] = []
    current: list[TurnFeature] = []
    current_hash = None
    for turn in session.turns:
        error_hash = turn.error_message_hash
        if not error_hash:
            current = []
            current_hash = None
            continue
        if current and error_hash == current_hash:
            current.append(turn)
        else:
            current = [turn]
            current_hash = error_hash
        if len(current) > len(best_run):
            best_run = list(current)
    if len(best_run) < config.repeated_error_hash_min:
        return []
    error_hash = best_run[0].error_message_hash or ""
    count = len(best_run)
    confidence = "high" if count >= max(config.repeated_error_hash_min + 2, 5) else "medium"
    return [
        _finding(
            session,
            "REPEATED_IDENTICAL_FAILURE",
            confidence,
            f"Same error message hash repeated in {count} adjacent turn(s).",
            "Resolve the repeated root cause manually before continuing the same run.",
            [
                _turn_evidence(session, "REPEATED_IDENTICAL_FAILURE", turn, "adjacent_error_hash_repetitions", str(count), error_hash)
                for turn in best_run[: config.repeated_error_hash_min]
            ],
        )
    ]


def _edit_retry_churn(session: SessionFeatures, config: ReviewConfig) -> list[ReviewFinding]:
    best_run: list[ToolFeature] = []
    current: list[ToolFeature] = []
    current_name = None
    for tool in session.tools:
        name = (tool.tool_name or "unknown").lower()
        if _is_mutating_file_tool(tool) and _is_failed_status(tool.status):
            if current and name == current_name:
                current.append(tool)
            else:
                current = [tool]
                current_name = name
            if len(current) > len(best_run):
                best_run = list(current)
        else:
            current = []
            current_name = None
    if len(best_run) < config.edit_failure_run_min:
        return []
    return [
        _finding(
            session,
            "EDIT_RETRY_CHURN",
            "medium",
            f"File-edit tool failures repeated in a run of {len(best_run)} event(s).",
            "Check file state or conflicts, then retry with a smaller edit.",
            [_tool_evidence(session, "EDIT_RETRY_CHURN", tool, "tool_failure_run", str(len(best_run))) for tool in best_run],
        )
    ]


def _context_overload(session: SessionFeatures, config: ReviewConfig) -> list[ReviewFinding]:
    segments: list[list[TurnFeature]] = []
    current: list[TurnFeature] = []
    previous = None
    for turn in session.turns:
        if turn.has_unreliable_tokens or turn.input_uncached_tokens <= 0:
            if current:
                segments.append(current)
            current = []
            previous = None
            continue
        value = turn.input_uncached_tokens
        if previous is None or value >= previous:
            current.append(turn)
        else:
            if current:
                segments.append(current)
            current = [turn]
        previous = value
    if current:
        segments.append(current)
    best: tuple[float, float, list[TurnFeature]] | None = None
    for segment in segments:
        if len(segment) < config.context_growth_min_turns:
            continue
        start = segment[0].input_uncached_tokens
        end = segment[-1].input_uncached_tokens
        if start < config.context_min_start_tokens:
            continue
        growth = end / start
        cache_drop = segment[0].cache_ratio - segment[-1].cache_ratio
        if growth <= config.context_growth_multiple or cache_drop < config.context_cache_drop_min:
            continue
        candidate = (growth, cache_drop, segment)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is None:
        return []
    growth, cache_drop, best_segment = best
    start = best_segment[0].input_uncached_tokens
    end = best_segment[-1].input_uncached_tokens
    confidence = (
        "high"
        if growth >= config.context_growth_multiple * 2 or cache_drop >= config.context_cache_drop_min * 2
        else "medium"
    )
    evidence = [
        _turn_evidence(session, "CONTEXT_OVERLOAD", best_segment[0], "input_uncached_start", str(start)),
        _turn_evidence(session, "CONTEXT_OVERLOAD", best_segment[-1], "input_uncached_end", str(end)),
    ]
    return [
        _finding(
            session,
            "CONTEXT_OVERLOAD",
            confidence,
            f"Uncached input grew {growth:.2f}x across {len(best_segment)} monotonic turn(s) while cache ratio dropped {cache_drop:.2f}.",
            "Split the task or restart with a compact handoff before adding more context.",
            evidence,
        )
    ]


def _tool_output_flood(session: SessionFeatures, config: ReviewConfig) -> list[ReviewFinding]:
    outputs = [tool.output_bytes for tool in session.tools if tool.output_bytes > 0]
    if not outputs:
        return []
    for tool in sorted(session.tools, key=lambda item: item.output_bytes, reverse=True):
        if tool.output_bytes <= 0:
            continue
        baseline_values = [value for value in outputs if value != tool.output_bytes]
        baseline = _percentile(sorted(baseline_values), 0.95) if baseline_values else 0
        threshold = max(config.tool_output_spike_min_bytes, int(baseline * config.tool_output_spike_multiplier))
        if tool.output_bytes < threshold:
            continue
        if not _has_failure_or_token_jump_after_tool(session, tool, config.tool_output_failure_window_turns):
            continue
        return [
            _finding(
                session,
                "TOOL_OUTPUT_FLOOD",
                "medium",
                f"One tool event produced {tool.output_bytes} byte(s) before nearby failure or token jump.",
                "Filter, paginate, or summarize large command output before continuing.",
                [_tool_evidence(session, "TOOL_OUTPUT_FLOOD", tool, "output_bytes", str(tool.output_bytes))],
            )
        ]
    return []


def _missing_setup(session: SessionFeatures, config: ReviewConfig) -> list[ReviewFinding]:
    shell_events = [tool for tool in session.tools if _is_shell_tool(tool)][: config.early_shell_window]
    turns_by_id = {turn.id: turn for turn in session.turns}
    scored: list[tuple[ToolFeature, float, str]] = []
    for tool in shell_events:
        linked_turn = turns_by_id.get(tool.turn_id or "")
        if tool.exit_code in {126, 127}:
            scored.append((tool, 1.5, f"exit_{tool.exit_code}"))
        elif tool.exit_code is None and _is_failed_status(tool.status):
            scored.append((tool, 1.0, "provider_failed_status"))
        elif tool.exit_code not in (None, 0) and linked_turn and _is_failed_status(linked_turn.status):
            scored.append((tool, 0.75, "nonzero_exit_linked_failed_turn"))
    score = sum(points for _, points, _ in scored)
    if score < config.early_shell_failure_score:
        return []
    strong_count = sum(1 for tool, _, reason in scored if reason.startswith("exit_") and tool.exit_code in {126, 127})
    confidence = "high" if strong_count >= 2 or score >= config.early_shell_failure_score + 1.0 else "medium"
    distribution = Counter(reason for _, _, reason in scored)
    metric_value = ",".join(f"{reason}:{count}" for reason, count in sorted(distribution.items()))
    return [
        _finding(
            session,
            "MISSING_SETUP",
            confidence,
            f"Early shell commands accumulated setup failure score {score:.1f}.",
            "Verify dependencies, permissions, and paths manually before resuming.",
            [_tool_evidence(session, "MISSING_SETUP", tool, "setup_failure_signals", metric_value) for tool, _, _ in scored],
        )
    ]


def _no_progress_thrashing(session: SessionFeatures, config: ReviewConfig) -> list[ReviewFinding]:
    if len(session.turns) < config.no_progress_min_turns:
        return []
    last_turn = session.turns[-1] if session.turns else None
    if (session.status or "").lower() == "completed" or (last_turn and (last_turn.status or "").lower() == "completed"):
        return []
    file_tools = [tool for tool in session.tools if _is_mutating_file_tool(tool)]
    if not file_tools:
        return []
    success = sum(1 for tool in file_tools if _is_success_status(tool.status))
    ratio = success / len(file_tools) if file_tools else 0.0
    if ratio >= config.no_progress_edit_success_max:
        return []
    evidence = []
    if session.turns:
        evidence.append(_turn_evidence(session, "NO_PROGRESS_THRASHING", session.turns[0], "turn_count", str(len(session.turns))))
        evidence.append(_turn_evidence(session, "NO_PROGRESS_THRASHING", session.turns[-1], "edit_success_ratio", f"{ratio:.4f}"))
    return [
        _finding(
            session,
            "NO_PROGRESS_THRASHING",
            "high" if len(session.turns) >= config.no_progress_min_turns * 2 and ratio == 0 else "medium",
            f"Session had {len(session.turns)} turn(s) with edit success ratio {ratio:.2f} and no completed tail.",
            "Reduce the task scope or provide the missing setup/context before another run.",
            evidence,
        )
    ]


def _model_fallback_degradation(session: SessionFeatures, config: ReviewConfig) -> list[ReviewFinding]:
    turns = [turn for turn in session.turns if _known_model(turn.model)]
    for index in range(1, len(turns)):
        if turns[index].model == turns[index - 1].model:
            continue
        before = turns[:index]
        after = turns[index:]
        if len(before) < config.model_fallback_min_before_turns or len(after) < config.model_fallback_min_after_turns:
            continue
        after_failures = _failure_count(after)
        if after_failures < config.model_fallback_min_after_failures:
            continue
        before_rate = _failure_rate(before)
        after_rate = _failure_rate(after)
        if after_rate == 0:
            continue
        rate_delta = after_rate - before_rate
        if rate_delta < config.model_fallback_failure_rate_delta_min:
            continue
        if before_rate == 0 or after_rate >= before_rate * config.model_fallback_failure_multiplier:
            confidence = "high" if after_failures >= config.model_fallback_min_after_failures + 2 or rate_delta >= 0.5 else "medium"
            return [
                _finding(
                    session,
                    "MODEL_FALLBACK_DEGRADATION",
                    confidence,
                    f"Failure density increased after model switch point {index}: {before_rate:.2f} before, {after_rate:.2f} after.",
                    "Check model/profile fallback settings and retry with an explicit model if needed.",
                    [
                        _turn_evidence(session, "MODEL_FALLBACK_DEGRADATION", turns[index], "model_switch_index", str(index)),
                        _turn_evidence(session, "MODEL_FALLBACK_DEGRADATION", turns[index], "after_failures", str(after_failures)),
                    ],
                )
            ]
    return []


def _interrupted_tail(session: SessionFeatures) -> list[ReviewFinding]:
    if (session.status or "").lower() != "interrupted":
        return []
    timeline = sorted([*session.turns, *session.tools], key=timeline_key)
    if not timeline or not isinstance(timeline[-1], ToolFeature):
        return []
    tool = timeline[-1]
    if not _is_running_tool(tool):
        return []
    return [
        _finding(
            session,
            "INTERRUPTED_TAIL",
            "high",
            "Session ended as interrupted while the last event was a running tool.",
            "Check whether the run was interrupted or crashed before resuming from this state.",
            [_tool_evidence(session, "INTERRUPTED_TAIL", tool, "tail_tool_status", _safe_status(tool.status))],
        )
    ]


def _finding(
    session: SessionFeatures,
    code: str,
    confidence: str,
    detail: str,
    recommendation: str,
    evidence: list[ReviewEvidence],
) -> ReviewFinding:
    finding_id = stable_id("review", session.id, REVIEW_RULESET_VERSION, code)
    created_at = session.started_at or session.ended_at or DEFAULT_CREATED_AT
    normalized_evidence = []
    for pointer in evidence:
        normalized_evidence.append(
            ReviewEvidence(
                id=stable_id(
                    "review_ev",
                    finding_id,
                    pointer.turn_id or "",
                    pointer.tool_event_id or "",
                    pointer.metric_name or "",
                    pointer.metric_value or "",
                    pointer.message_hash or "",
                    pointer.source_file_hash or "",
                    pointer.source_line_start or "",
                    pointer.source_line_end or "",
                ),
                finding_id=finding_id,
                session_id=session.id,
                turn_id=pointer.turn_id,
                tool_event_id=pointer.tool_event_id,
                source_file_hash=pointer.source_file_hash,
                source_line_start=pointer.source_line_start,
                source_line_end=pointer.source_line_end,
                metric_name=pointer.metric_name,
                metric_value=pointer.metric_value,
                message_hash=pointer.message_hash,
                created_at=created_at,
            )
        )
    return ReviewFinding(
        id=finding_id,
        session_id=session.id,
        ruleset_version=REVIEW_RULESET_VERSION,
        code=code,
        confidence=confidence,
        detail=detail,
        recommendation=recommendation,
        created_at=created_at,
        evidence_pointers=normalized_evidence,
    )


def _turn_evidence(
    session: SessionFeatures,
    code: str,
    turn: TurnFeature,
    metric_name: str,
    metric_value: str,
    message_hash: str | None = None,
) -> ReviewEvidence:
    return ReviewEvidence(
        id="",
        finding_id=stable_id("review", session.id, REVIEW_RULESET_VERSION, code),
        session_id=session.id,
        turn_id=turn.id,
        source_file_hash=turn.source_file_hash or session.source_file_hash,
        source_line_start=turn.source_line_start,
        source_line_end=turn.source_line_end,
        metric_name=metric_name,
        metric_value=metric_value,
        message_hash=message_hash,
    )


def _tool_evidence(
    session: SessionFeatures,
    code: str,
    tool: ToolFeature,
    metric_name: str,
    metric_value: str,
) -> ReviewEvidence:
    return ReviewEvidence(
        id="",
        finding_id=stable_id("review", session.id, REVIEW_RULESET_VERSION, code),
        session_id=session.id,
        turn_id=tool.turn_id,
        tool_event_id=tool.id,
        source_file_hash=tool.source_file_hash or session.source_file_hash,
        source_line_start=tool.source_line_start,
        source_line_end=tool.source_line_end,
        metric_name=metric_name,
        metric_value=metric_value,
    )


def _is_file_tool(tool: ToolFeature) -> bool:
    return _is_mutating_file_tool(tool) or _is_read_only_file_tool(tool)


def _is_mutating_file_tool(tool: ToolFeature) -> bool:
    text = f"{tool.tool_type or ''} {tool.tool_name or ''}".lower()
    name = (tool.tool_name or "").lower()
    if name in {"read", "view", "open"}:
        return False
    return any(token in text for token in ("edit", "apply_patch", "write", "replace", "patch", "create", "delete"))


def _is_read_only_file_tool(tool: ToolFeature) -> bool:
    text = f"{tool.tool_type or ''} {tool.tool_name or ''}".lower()
    name = (tool.tool_name or "").lower()
    return "file" in text and (name in {"read", "view", "open"} or "read" in text)


def _is_shell_tool(tool: ToolFeature) -> bool:
    text = f"{tool.tool_type or ''} {tool.tool_name or ''}".lower()
    return any(token in text for token in ("shell", "exec_command", "terminal", "bash", "subprocess", "run_command"))


def _is_failed_status(status: str | None) -> bool:
    return (status or "").lower() in {"failed", "error", "errored"}


def _is_success_status(status: str | None) -> bool:
    return (status or "").lower() in {"completed", "success", "succeeded", "ok"}


def _is_running_tool(tool: ToolFeature) -> bool:
    status = (tool.status or "").lower()
    if status in {"running", "started", "in_progress", "pending"}:
        return True
    return tool.ended_at is None and status not in {"completed", "success", "succeeded", "ok", "failed", "error", "canceled", "cancelled"}


def _safe_status(status: str | None) -> str:
    if not status:
        return "unknown"
    lowered = status.lower()
    return lowered if lowered in {"running", "started", "in_progress", "pending", "unknown"} else "other"


def _has_failure_or_token_jump_after_tool(session: SessionFeatures, tool: ToolFeature, window: int) -> bool:
    if not session.turns:
        return False
    start_index = _turn_index_after_tool(session.turns, tool)
    if start_index is None:
        return False
    window_turns = session.turns[start_index : start_index + window + 1]
    if any(_is_failed_status(turn.status) for turn in window_turns):
        return True
    previous_turn = session.turns[start_index - 1] if start_index > 0 else None
    previous_value = (
        previous_turn.input_uncached_tokens
        if previous_turn and not previous_turn.has_unreliable_tokens
        else 0
    )
    for turn in window_turns:
        if turn.has_unreliable_tokens:
            continue
        if previous_value > 0 and turn.input_uncached_tokens >= previous_value * 1.5:
            return True
        previous_value = max(previous_value, turn.input_uncached_tokens)
    return False


def _turn_index_after_tool(turns: list[TurnFeature], tool: ToolFeature) -> int | None:
    if tool.turn_id:
        for index, turn in enumerate(turns):
            if turn.id == tool.turn_id:
                return index
    if not tool.source_file_hash or tool.source_line_start is None:
        return None
    tool_line = tool.source_line_start
    for index, turn in enumerate(turns):
        if turn.source_file_hash == tool.source_file_hash and (turn.source_line_start or 0) >= tool_line:
            return index
    return None


def _known_model(model: str | None) -> bool:
    return bool(model and model.lower() not in {"unknown", "none", "null"})


def _failure_rate(turns: list[TurnFeature]) -> float:
    if not turns:
        return 0.0
    return _failure_count(turns) / len(turns)


def _failure_count(turns: list[TurnFeature]) -> int:
    return sum(1 for turn in turns if _is_failed_status(turn.status))


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    index = min(len(values) - 1, max(0, round((len(values) - 1) * percentile)))
    return int(values[index])


def _positive_int(name: str, value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"review.{name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"review.{name} must be positive")
    return parsed


def _positive_float(name: str, value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"review.{name} must be numeric") from exc
    if parsed <= 0:
        raise ValueError(f"review.{name} must be positive")
    return parsed
